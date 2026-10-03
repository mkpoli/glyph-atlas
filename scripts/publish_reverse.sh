#!/usr/bin/env bash
# Keep the image search's Vectorize index at the current similar-crop revision.
#
#   scripts/publish_reverse.sh create [work/similar]    once per encoder: the index and its metadata indexes
#   scripts/publish_reverse.sh publish [work/similar]   after every `atlas similar index`
#
# The index is named after the encoder (`glyph-atlas-similar-<first 8 hex digits>`), so a retrained
# classifier fills a new index and the Worker's `SIMILAR_INDEX` binding is moved to it. `publish` runs
# `atlas similar vectorize`, upserts the crops that are new or changed and deletes the ones the
# revision dropped, then keeps the state the index now holds in `work/similar/vectorize/<index>/` and
# writes `reverse/index.json` to R2, which tells the Worker which encoder the index holds. Each
# uploaded file is recorded, so a rerun after a failure sends only the rest. A first upload is about
# 2.6 GB of JSON for 503,013 crops.
set -euo pipefail
command="${1:?usage: scripts/publish_reverse.sh create|publish [work/similar]}"
root="$(cd "$(dirname "$0")/.." && pwd -P)"
out="$(cd "${2:-$root/work/similar}" && pwd -P)"
encoder="$(jq -er '.encoder' "$out/current/manifest.json")"
index="glyph-atlas-similar-${encoder:0:8}"
dimensions="$(cd "$root" && uv run --no-sync python -c 'import sys, numpy; print(numpy.load(sys.argv[1], mmap_mode="r").shape[1])' "$out/current/vectors.npy")"
wrangler() { (cd "$root/apps/cloudflare" && bunx wrangler "$@"); }

case "$command" in
create)
  wrangler vectorize create "$index" --dimensions "$dimensions" --metric cosine \
    --description "Similar-crop vectors of encoder ${encoder:0:16}"
  # Metadata indexes apply to vectors upserted after them, so they come before the first upload.
  wrangler vectorize create-metadata-index "$index" --propertyName label --type string
  wrangler vectorize create-metadata-index "$index" --propertyName origin --type string
  echo "created $index ($dimensions dimensions); bind it as SIMILAR_INDEX in apps/cloudflare/wrangler.jsonc"
  ;;
publish)
  state="$out/vectorize/$index"
  plan="$state/plan"
  mkdir -p "$state"
  # The plan is kept until it is applied, so a rerun resumes it instead of planning again.
  if [ ! -f "$plan/plan.json" ] || [ "$(jq -r '.revision' "$plan/plan.json")" != "$(jq -r '.revision' "$out/current/manifest.json")" ]; then
    (cd "$root" && uv run --no-sync atlas similar vectorize --out "$out" --state "$state/state.parquet" --plan "$plan")
    : > "$plan/sent.txt"
  fi
  [ "$(jq -r '.index' "$plan/plan.json")" = "$index" ] || { echo "the plan is for another index" >&2; exit 1; }
  for file in "$plan"/upsert-*.ndjson; do
    [ -e "$file" ] || continue
    name="$(basename "$file")"
    grep -qxF "$name" "$plan/sent.txt" && continue
    wrangler vectorize upsert "$index" --file "$file" --batch-size 5000 >/dev/null
    echo "$name" >> "$plan/sent.txt"
    echo "upserted $name"
  done
  if [ -s "$plan/delete.txt" ] && ! grep -qxF delete.txt "$plan/sent.txt"; then
    # 100 ids a call keeps each command line short.
    xargs -a "$plan/delete.txt" -d '\n' -n 100 bash -c 'cd "$0/apps/cloudflare" && bunx wrangler vectorize delete-vectors "$1" --ids "${@:2}" >/dev/null' "$root" "$index"
    echo delete.txt >> "$plan/sent.txt"
  fi
  if [ -f "$plan/state.parquet" ]; then mv "$plan/state.parquet" "$state/state.parquet"; fi
  jq -n --arg index "$index" --arg encoder "$encoder" --slurpfile plan "$plan/plan.json" \
    '{index: $index, encoder: $encoder, revision: $plan[0].revision, crops: $plan[0].crops, dimensions: $plan[0].dimensions}' \
    > "$state/index.json"
  wrangler r2 object put glyph-atlas/reverse/index.json --file "$state/index.json" --content-type application/json --remote >/dev/null
  rm -rf "$plan"
  echo "published $index at revision $(jq -r '.revision' "$state/index.json")"
  if ! grep -q "\"index_name\": *\"$index\"" "$root/apps/cloudflare/wrangler.jsonc"; then
    echo "apps/cloudflare/wrangler.jsonc does not bind $index as SIMILAR_INDEX; the image search answers only once it does" >&2
  fi
  ;;
*) echo "usage: scripts/publish_reverse.sh create|publish [work/similar]" >&2; exit 2 ;;
esac
