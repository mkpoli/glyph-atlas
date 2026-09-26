#!/usr/bin/env bash
# Upload a sealed publication: every object to R2 first, then the SQL parts to D1 in order.
# Nothing reaches D1 unless every object is in R2, so no row names a pack that is missing.
set -euo pipefail
publication="$(cd "$1" && pwd)"
cd "$(dirname "$0")/../apps/cloudflare"
# A manifest without SQL parts (a full seal keeps its SQL in catalogue.sql) is refused before any upload.
mapfile -t parts < <(jq -er '.sql[]' "$publication/publication.json")
[ "${#parts[@]}" -gt 0 ] || { echo "no SQL parts in $publication/publication.json" >&2; exit 1; }

# Each object put is recorded, so a rerun after a failed or interrupted upload sends only the rest.
# Objects are content-addressed, so sending one twice is harmless.
uploaded="$publication/uploaded.txt"
touch "$uploaded"
for round in 1 2 3 4; do
  jq -r '.objects[] | "\(.key) \(.file)"' "$publication/publication.json" |
    while read -r key file; do grep -qxF "$key" "$uploaded" || echo "$key $file"; done > "$publication/pending.txt"
  [ -s "$publication/pending.txt" ] || break
  echo "r2 round $round: $(wc -l < "$publication/pending.txt") objects"
  xargs -P 4 -L 1 sh -c 'bunx wrangler r2 object put "glyph-atlas/$0" --file "'"$publication"'/$1" \
    --content-type application/octet-stream --remote >/dev/null 2>&1 && echo "$0" >> "'"$uploaded"'"' \
    < "$publication/pending.txt" || true
done
missing=$(jq -r '.objects[].key' "$publication/publication.json" | grep -cvxFf "$uploaded" || true)
[ "$missing" -eq 0 ] || { echo "$missing objects did not upload; rerun to send them" >&2; exit 1; }

# A part fails whole and changes nothing, and D1 takes one import at a time, so a refused part is
# retried; every part is written to be applied again safely.
for part in "${parts[@]}"; do
  echo "d1 $part"
  for try in 1 2 3 4; do
    bunx wrangler d1 execute glyph-atlas --remote --yes --file "$publication/$part" && break
    [ "$try" -lt 4 ] || { echo "$part failed four times; rerun to continue" >&2; exit 1; }
    sleep 30
  done
done
# The homepage gallery deals copies of published records; bring them up to date with the rows just
# written. The parts go beside the publication, in a directory of their own for each run.
gallery="$publication/gallery-$(date -u +%Y%m%dT%H%M%SZ)"
uv run ../../scripts/fill_corpus_gallery.py "$gallery"
for part in "$gallery"/*.sql; do
  echo "d1 $part"
  bunx wrangler d1 execute glyph-atlas --remote --yes --file "$part"
done
echo "published"
