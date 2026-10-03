#!/usr/bin/env bash
# Upload a browser model version that models/classifier/browser_model.py built to R2, then name it as
# the current one. The local files are checked against the manifest's names, sizes and SHA-256 and
# its passing parity check, and the manifest's encoder against the published index of that encoder
# (`reverse/index/glyph-atlas-similar-<encoder[:8]>.json`, which scripts/publish_reverse.sh writes): a
# model whose vectors no index holds would turn the image search off for every reader. The files go
# under `reverse/models/<version>/`, and `reverse/model.json`, which the image search reads to offer the
# download, is written last. The image search offers a new version as an update; it never fetches one
# by itself.
#   scripts/publish_browser_model.sh work/browser-model/<version>
#   scripts/publish_browser_model.sh work/browser-model/<version> --before-index   # the index comes later
set -euo pipefail
directory="$(cd "${1:?usage: scripts/publish_browser_model.sh work/browser-model/<version> [--before-index]}" && pwd -P)"
before_index="${2:-}"
manifest="$directory/model.json"
version="$(jq -er '.version' "$manifest")"
encoder="$(jq -er '.encoder' "$manifest")"
[ "$(basename "$directory")" = "$version" ] || { echo "$directory does not hold version $version" >&2; exit 1; }
jq -e '.parity.passed == true' "$manifest" >/dev/null || { echo "model.json records no passing parity check" >&2; exit 1; }
[ "$(jq -r '.files.model.name' "$manifest")" = classifier.onnx ] && [ "$(jq -r '.files.classes.name' "$manifest")" = classes.json ] \
  || { echo "model.json names files the site does not serve" >&2; exit 1; }
for name in model classes; do
  file="$directory/$(jq -er ".files.$name.name" "$manifest")"
  [ "$(stat -c %s "$file")" = "$(jq -er ".files.$name.bytes" "$manifest")" ] || { echo "$file is not the size model.json records" >&2; exit 1; }
  [ "$(sha256sum "$file" | cut -d' ' -f1)" = "$(jq -er ".files.$name.sha256" "$manifest")" ] || { echo "$file does not match model.json" >&2; exit 1; }
done
cd "$(dirname "$0")/../apps/cloudflare"
if [ "$before_index" != --before-index ]; then
  published="$(bunx wrangler r2 object get "glyph-atlas/reverse/index/glyph-atlas-similar-${encoder:0:8}.json" --pipe --remote 2>/dev/null | jq -r '.encoder' 2>/dev/null || true)"
  [ "$published" = "$encoder" ] || { echo "no published index holds encoder ${encoder:0:16}; run scripts/publish_reverse.sh first, or pass --before-index" >&2; exit 1; }
fi
put() { bunx wrangler r2 object put "glyph-atlas/$1" --file "$2" --content-type "$3" --remote >/dev/null; }
put "reverse/models/$version/classifier.onnx" "$directory/classifier.onnx" application/octet-stream
put "reverse/models/$version/classes.json" "$directory/classes.json" application/json
put "reverse/models/$version/model.json" "$manifest" application/json
put reverse/model.json "$manifest" application/json
echo "published reverse/models/$version ($(jq -r '.files.model.bytes' "$manifest") bytes)"
