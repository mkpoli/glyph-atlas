#!/usr/bin/env bash
# Upload a sealed publication: every object to R2 first, then the SQL parts to D1 in order.
# Nothing reaches D1 unless every object is in R2, so no row names a pack that is missing.
set -euo pipefail
publication="$(cd "$1" && pwd)"
cd "$(dirname "$0")/../apps/cloudflare"
# A manifest without SQL parts (a full seal keeps its SQL in catalogue.sql) is refused before any upload.
mapfile -t parts < <(jq -er '.sql[]' "$publication/publication.json")
[ "${#parts[@]}" -gt 0 ] || { echo "no SQL parts in $publication/publication.json" >&2; exit 1; }

jq -r '.objects[] | "\(.key) \(.file)"' "$publication/publication.json" |
  xargs -P 4 -L 1 sh -c 'bunx wrangler r2 object put "glyph-atlas/$0" --file "'"$publication"'/$1" \
    --content-type application/octet-stream --remote >/dev/null && echo "put $0"'

for part in "${parts[@]}"; do
  echo "d1 $part"
  bunx wrangler d1 execute glyph-atlas --remote --yes --file "$publication/$part"
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
