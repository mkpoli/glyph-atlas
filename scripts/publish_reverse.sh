#!/usr/bin/env bash
# Keep the image search's Vectorize index at the current similar-crop revision.
#
#   scripts/publish_reverse.sh create [work/similar]    once per encoder: the index and its metadata indexes
#   scripts/publish_reverse.sh publish [work/similar]   after every `atlas similar index`
#
# The index is named after the encoder (`glyph-atlas-similar-<first 8 hex digits>`), so a retrained
# classifier fills a new index and the Worker's `SIMILAR_INDEX` binding and `SIMILAR_INDEX_NAME` are
# moved to it. `publish` first finishes a plan an earlier run left unfinished, then runs
# `atlas similar vectorize` when the index is behind the current revision, upserts the crops that are
# new or changed and deletes the ones the revision dropped. It keeps the state the index now holds in
# `work/similar/vectorize/<index>/` and writes `reverse/index/<index>.json` to R2, the pointer the
# Worker bound to that index reads. Each uploaded file is recorded, so a rerun after a failure sends
# only the rest. A first upload is about 2.2 GB of JSON for 503,013 crops. Vectorize applies upserts
# and deletions a little after it accepts them, so the pointer can lead the index briefly.
set -euo pipefail
command="${1:?usage: scripts/publish_reverse.sh create|publish [work/similar]}"
root="$(cd "$(dirname "$0")/.." && pwd -P)"
out="$(cd "${2:-$root/work/similar}" && pwd -P)"
encoder="$(jq -er '.encoder' "$out/current/manifest.json")"
index="glyph-atlas-similar-${encoder:0:8}"
wrangler() { (cd "$root/apps/cloudflare" && bunx wrangler "$@"); }

# Upsert and delete what the plan in $plan lists, record the state the index then holds, and write its pointer.
apply_plan() {
  [ "$(jq -r '.index' "$plan/plan.json")" = "$index" ] || { echo "the plan in $plan is for another index" >&2; exit 1; }
  touch "$plan/sent.txt"
  for file in "$plan"/upsert-*.ndjson; do
    [ -e "$file" ] || continue
    name="$(basename "$file")"
    grep -qxF "$name" "$plan/sent.txt" && continue
    wrangler vectorize upsert "$index" --file "$file" --batch-size 5000 >/dev/null
    echo "$name" >> "$plan/sent.txt"
    echo "upserted $name"
  done
  if [ -s "$plan/delete.txt" ] && ! grep -qxF delete.txt "$plan/sent.txt"; then
    # 100 ids a call keeps each command line short; a failed call stops the run and a rerun repeats them.
    xargs -a "$plan/delete.txt" -d '\n' -n 100 bash -c 'cd "$0/apps/cloudflare" && bunx wrangler vectorize delete-vectors "$1" --ids "${@:2}" >/dev/null' "$root" "$index"
    echo delete.txt >> "$plan/sent.txt"
  fi
  if [ -f "$plan/state.parquet" ]; then mv "$plan/state.parquet" "$state/state.parquet"; fi
  jq -n --arg index "$index" --arg encoder "$encoder" --slurpfile plan "$plan/plan.json" \
    '{index: $index, encoder: $encoder, revision: $plan[0].revision, crops: $plan[0].crops, dimensions: $plan[0].dimensions}' \
    > "$state/index.json"
  wrangler r2 object put "glyph-atlas/reverse/index/$index.json" --file "$state/index.json" --content-type application/json --remote >/dev/null
  rm -rf "$plan"
  echo "published $index at revision $(jq -r '.revision' "$state/index.json")"
}

case "$command" in
create)
  dimensions="$(cd "$root" && uv run --no-sync python -c 'import sys, numpy; print(numpy.load(sys.argv[1], mmap_mode="r").shape[1])' "$out/current/vectors.npy")"
  wrangler vectorize create "$index" --dimensions "$dimensions" --metric cosine \
    --description "Similar-crop vectors of encoder ${encoder:0:16}"
  # Metadata indexes apply to vectors upserted after them, so they come before the first upload, and
  # they are created a few seconds after they are asked for.
  wrangler vectorize create-metadata-index "$index" --propertyName label --type string
  wrangler vectorize create-metadata-index "$index" --propertyName origin --type string
  for attempt in $(seq 1 60); do
    listed="$(wrangler vectorize list-metadata-index "$index" --json 2>/dev/null || true)"
    if grep -q '"label"' <<<"$listed" && grep -q '"origin"' <<<"$listed"; then break; fi
    [ "$attempt" -lt 60 ] || { echo "the metadata indexes of $index are not listed yet; wait before publishing" >&2; exit 1; }
    sleep 5
  done
  echo "created $index ($dimensions dimensions); bind it as SIMILAR_INDEX and name it in SIMILAR_INDEX_NAME in apps/cloudflare/wrangler.jsonc"
  ;;
publish)
  state="$out/vectorize/$index"
  plan="$state/plan"
  mkdir -p "$state"
  exec 9>"$state/.lock"
  flock -n 9 || { echo "another publish of $index is running" >&2; exit 1; }
  # A plan an earlier run left unfinished is finished first: a new plan diffs against the state the
  # index holds, which only a finished plan records.
  if [ -f "$plan/plan.json" ]; then apply_plan; fi
  wanted="$(jq -r '.revision' "$out/current/manifest.json")"
  if [ ! -f "$state/index.json" ] || [ "$(jq -r '.revision' "$state/index.json")" != "$wanted" ]; then
    (cd "$root" && uv run --no-sync atlas similar vectorize --out "$out" --state "$state/state.parquet" --plan "$plan")
    apply_plan
  else
    echo "$index is at revision $wanted"
  fi
  if ! grep -q "\"SIMILAR_INDEX_NAME\": *\"$index\"" "$root/apps/cloudflare/wrangler.jsonc"; then
    echo "apps/cloudflare/wrangler.jsonc does not name $index in SIMILAR_INDEX_NAME; the Worker answers from it only once a deploy binds it" >&2
  fi
  ;;
*) echo "usage: scripts/publish_reverse.sh create|publish [work/similar]" >&2; exit 2 ;;
esac
