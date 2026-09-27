#!/usr/bin/env bash
# Upload a similar-crop revision's neighbour shards to R2, gzipped, then point the site at it.
# The shards go under `similar/<revision>-<neighbours digest>/`, so neighbours computed again for the
# same index are a new prefix. The pointer `similar/current.json` is written only once every shard
# the neighbours manifest counts is in R2, so the site never reads a half-uploaded set. Each put is
# recorded, so a rerun sends only the rest.
#   scripts/publish_similar.sh [work/similar/current]
set -euo pipefail
directory="$(cd "${1:-work/similar/current}" && pwd -P)"
manifest="$directory/neighbours/manifest.json"
digits="$(jq -er '.shard_digits' "$manifest")"
expected="$(jq -er '.shards' "$manifest")"
revision="$(basename "$directory")-$(cat "$directory"/neighbours/*.json | sha256sum | cut -c1-12)"
staging="$directory/publish-$revision"
mkdir -p "$staging"
for shard in "$directory"/neighbours/*.json; do
  name="$(basename "$shard")"
  [ "$name" = manifest.json ] && continue
  if [ ! -s "$staging/$name.gz" ]; then
    gzip -9 -c "$shard" > "$staging/$name.gz.tmp"
    mv "$staging/$name.gz.tmp" "$staging/$name.gz"
  fi
done
cd "$(dirname "$0")/../apps/cloudflare"
uploaded="$staging/uploaded.txt"
touch "$uploaded"
total=$(find "$staging" -name '*.json.gz' | wc -l)
for round in 1 2 3 4; do
  find "$staging" -name '*.json.gz' -printf '%f\n' | sort | grep -vxFf "$uploaded" > "$staging/pending.txt" || true
  [ -s "$staging/pending.txt" ] || break
  echo "r2 round $round: $(wc -l < "$staging/pending.txt") of $total shards to send"
  xargs -P 8 -I{} sh -c 'bunx wrangler r2 object put "glyph-atlas/similar/'"$revision"'/{}" --file "'"$staging"'/{}" \
    --content-type application/gzip --remote >/dev/null 2>&1 && echo "{}" >> "'"$uploaded"'" || echo "r2 failed {} (retried next round)"' \
    < "$staging/pending.txt" || true
done
missing=$(find "$staging" -name '*.json.gz' -printf '%f\n' | grep -cvxFf "$uploaded" || true)
[ "$missing" -eq 0 ] || { echo "$missing shards did not upload; rerun to send them" >&2; exit 1; }
sent=$(sort -u "$uploaded" | wc -l)
[ "$sent" -eq "$expected" ] || { echo "$sent shards in R2, the manifest counts $expected; not switching" >&2; exit 1; }
jq -n --arg revision "$revision" --argjson digits "$digits" '{revision: $revision, shard_digits: $digits}' > "$staging/current.json"
bunx wrangler r2 object put glyph-atlas/similar/current.json --file "$staging/current.json" \
  --content-type application/json --remote >/dev/null
echo "published similar/$revision ($total shards)"
