#!/usr/bin/env bash
# Upload the onnxruntime-web WebAssembly the image search runs its model with to R2, under the version
# apps/review bundles: `reverse/runtime/onnxruntime-web-<version>/`. The file is larger than a Workers
# static asset may be, so it is served from R2 like the model, and downloaded with it when the reader
# asks. `runtime.json` beside it gives its size and SHA-256, which the page checks; it is written last.
#   scripts/publish_onnx_runtime.sh
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd -P)"
package="$root/apps/review/node_modules/onnxruntime-web"
[ -f "$package/package.json" ] || { echo "run bun install in apps/review first" >&2; exit 1; }
version="$(jq -er '.version' "$package/package.json")"
pinned="$(jq -er '.dependencies["onnxruntime-web"]' "$root/apps/review/package.json")"
[ "$version" = "$pinned" ] || { echo "apps/review pins onnxruntime-web $pinned and node_modules holds $version" >&2; exit 1; }
file=ort-wasm-simd-threaded.asyncify.wasm
wasm="$package/dist/$file"
prefix="reverse/runtime/onnxruntime-web-$version"
manifest="$(mktemp)"
trap 'rm -f "$manifest"' EXIT
jq -n --arg version "$version" --arg file "$file" --argjson bytes "$(stat -c %s "$wasm")" \
  --arg sha256 "$(sha256sum "$wasm" | cut -d' ' -f1)" '{version: $version, file: $file, bytes: $bytes, sha256: $sha256}' > "$manifest"
cd "$root/apps/cloudflare"
bunx wrangler r2 object put "glyph-atlas/$prefix/$file" --file "$wasm" --content-type application/wasm --remote >/dev/null
bunx wrangler r2 object put "glyph-atlas/$prefix/runtime.json" --file "$manifest" --content-type application/json --remote >/dev/null
echo "published $prefix/$file ($(jq -r '.bytes' "$manifest") bytes)"
