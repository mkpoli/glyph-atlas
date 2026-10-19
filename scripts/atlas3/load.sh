#!/usr/bin/env bash
# Load the first build (out/atlas.sqlite, out/atlas-runs.sqlite) into the new, empty D1 databases.
# Each database gets its tables, then its rows in parts of PART statements, then its indexes, so the
# rows go in without index upkeep per statement (the index build bills one written row per entry).
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
here="${ATLAS3_DIR:-$root/work/atlas-v3}"
cf="$root/apps/cloudflare"
PART=${PART:-200000}
load() { # <database> <sqlite> <migrations folder>
  local db=$1 file=$2 schema="$here/out/schema-$1.sql" dir="$here/out/load-$1"
  # The schema is every file of the folder, in order.
  cat "$3"/*.sql > "$schema"
  # Parts belong to one build: a rebuilt file starts the parts again.
  local build; build="$(stat -c '%s %Y' "$file")"
  if [ ! -f "$dir/.split" ] || [ "$(cat "$dir/.split")" != "$build" ]; then
  rm -rf "$dir"; mkdir -p "$dir"
  grep -v "^CREATE INDEX" "$schema" | sed "s/^CREATE TABLE /CREATE TABLE IF NOT EXISTS /" > "$dir/0-tables.sql"
  grep "^CREATE INDEX" "$schema" | sed "s/^CREATE INDEX /CREATE INDEX IF NOT EXISTS /" > "$dir/9-indexes.sql"
  for t in $(sqlite3 "$file" "SELECT name FROM sqlite_master WHERE type='table'"); do
    sqlite3 "$file" ".mode insert $t" "SELECT * FROM \"$t\"" | sed 's/^INSERT INTO/INSERT OR IGNORE INTO/' | split -l "$PART" -d -a 4 --additional-suffix=.sql - "$dir/5-$t-"
  done
  echo "$build" > "$dir/.split"
  fi
  for part in $(ls "$dir"/*.sql | sort); do
    [ -f "$part.done" ] && continue
    echo "$db $(basename "$part") $(wc -l < "$part")"
    (cd "$cf" && bunx wrangler d1 execute "$db" --remote --yes --file "$part" > "$part.log" 2>&1) ||
      { echo "failed: $part (see $part.log); rerun from it" >&2; exit 1; }
    touch "$part.done"
  done
}
# Once anything is published, the local copies have moved on from the build: loading again would
# resend every row. A new layout starts from build.py --replace and empty databases.
if [ -n "$(ls "$here/out/publish" 2>/dev/null)" ]; then
  echo "out/publish/ holds publishes; this build is loaded and live. Not loading again." >&2; exit 1
fi
build_id="$(sqlite3 "$here/out/mirror.sqlite" "SELECT value FROM meta WHERE key='build'")"
load glyph-atlas-v3 "$here/out/atlas.sqlite" "$cf/migrations-atlas"
load glyph-atlas-v3-runs "$here/out/atlas-runs.sqlite" "$cf/migrations-atlas-runs"
# The databases name the build their ids come from; publish.py checks it before every publish.
(cd "$cf" && bunx wrangler d1 execute glyph-atlas-v3 --remote --yes \
  --command "INSERT OR IGNORE INTO imports(stamp, at) VALUES('build:$build_id', unixepoch())") > /dev/null
echo loaded
