#!/usr/bin/env bash
# Upload a browser model version that models/classifier/browser_model.py built to R2, then name it as
# the current one. The files go under `reverse/models/<version>/`, and `reverse/model.json`, which the
# image search reads to offer the download, is written only once both are in R2 with the sizes the
# manifest records, so a reader never sees a version whose files are missing. The image search offers
# a new version as an update; it never fetches one by itself.
#   scripts/publish_browser_model.sh work/browser-model/<version>
set -euo pipefail
directory="$(cd "${1:?usage: scripts/publish_browser_model.sh work/browser-model/<version>}" && pwd -P)"
manifest="$directory/model.json"
version="$(jq -er '.version' "$manifest")"
[ "$(basename "$directory")" = "$version" ] || { echo "$directory does not hold version $version" >&2; exit 1; }
for name in model classes; do
  file="$directory/$(jq -er ".files.$name.name" "$manifest")"
  [ "$(stat -c %s "$file")" = "$(jq -er ".files.$name.bytes" "$manifest")" ] || { echo "$file is not the size model.json records" >&2; exit 1; }
  [ "$(sha256sum "$file" | cut -d' ' -f1)" = "$(jq -er ".files.$name.sha256" "$manifest")" ] || { echo "$file does not match model.json" >&2; exit 1; }
done
cd "$(dirname "$0")/../apps/cloudflare"
put() { bunx wrangler r2 object put "glyph-atlas/$1" --file "$2" --content-type "$3" --remote >/dev/null; }
put "reverse/models/$version/classifier.onnx" "$directory/classifier.onnx" application/octet-stream
put "reverse/models/$version/classes.json" "$directory/classes.json" application/json
put "reverse/models/$version/model.json" "$manifest" application/json
put reverse/model.json "$manifest" application/json
echo "published reverse/models/$version ($(jq -r '.files.model.bytes' "$manifest") bytes)"
