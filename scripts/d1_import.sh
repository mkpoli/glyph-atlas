#!/usr/bin/env bash
# Import one SQL file into the site's D1 and confirm that it applied.
#
#   d1_import.sh <file.sql>        run from apps/cloudflare, where wrangler finds the database
#
# Wrangler's exit status is not proof either way: an import it loses track of ends with
# "Not currently importing anything" although the file applied. So a stamp made for this import is
# appended to a copy of the file, and the import counts as applied when the stamp is in `metadata`.
# D1 applies an imported file whole or not at all ("if the execution fails to complete, your DB will
# return to its original state", developers.cloudflare.com/d1/get-started/), so the stamp is there
# exactly when every statement before it is. A file that never applies still fails.
#
# D1_TARGET (default --remote), D1_TRIES (4) and D1_WAIT (seconds between tries, 30) can be set, for
# example to test against a local database: D1_TARGET="--local --persist-to state" D1_WAIT=1.
set -uo pipefail
file="$1"
[ -f "$file" ] || { echo "d1_import: no such file: $file" >&2; exit 2; }
read -r -a target <<< "${D1_TARGET:---remote}"
tries="${D1_TRIES:-4}"
pause="${D1_WAIT:-30}"
name="$(basename "$file")"
stamp="$name $(date -u +%Y%m%dT%H%M%SZ) $RANDOM$RANDOM"
staged="$(mktemp --suffix=.sql)"
trap 'rm -f "$staged"' EXIT
{ cat "$file"; printf "\nINSERT OR REPLACE INTO metadata(key,value) VALUES('import_stamp',json_quote('%s'));\n" "$stamp"; } > "$staged"

# 0: the stamp is there; 1: it is not; 2: the database could not be asked.
stamped() {
  local out
  out="$(bunx wrangler d1 execute glyph-atlas "${target[@]}" --json \
    --command "SELECT value FROM metadata WHERE key='import_stamp'" 2>/dev/null)" || return 2
  [ "$(jq -r '.[0].results[0].value // empty' <<< "$out" 2>/dev/null)" = "\"$stamp\"" ]
}
# Asks again while the database is too busy to answer, so a slow read is not taken for a missing stamp.
confirmed() {
  local ask status
  for ask in 1 2 3 4 5; do
    stamped; status=$?
    [ "$status" -eq 2 ] || return "$status"
    sleep "$pause"
  done
  return 1
}

for try in $(seq 1 "$tries"); do
  if bunx wrangler d1 execute glyph-atlas "${target[@]}" --yes --file "$staged"; then
    confirmed && exit 0
    echo "d1_import: $name: wrangler reported success, but its stamp is missing" >&2
  else
    # An import wrangler stopped following may still be finishing on D1's side.
    sleep "$pause"
    if confirmed; then echo "d1_import: $name applied although wrangler reported a failure" >&2; exit 0; fi
  fi
done
echo "d1_import: $name did not apply after $tries tries" >&2
exit 1
