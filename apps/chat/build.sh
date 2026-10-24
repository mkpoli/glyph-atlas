#!/usr/bin/env bash
# Fill dist/ with Element Web and this folder's config and images, for chat.glyphatlas.org.
# The release is pinned by version and by the SHA-256 of its tarball, taken after its signature was
# checked against Element's release key (https://packages.element.io/element-release-key.asc).
set -euo pipefail
cd "$(dirname "$0")"
VERSION=v1.12.30
SHA256=41ecae1e7af5d09baf2c1a7646cebad51ced6a44257d2096b9618bff3fee625d
tarball="element-$VERSION.tar.gz"
mkdir -p .cache
[ -f ".cache/$tarball" ] || curl -fsSL -o ".cache/$tarball" "https://github.com/element-hq/element-web/releases/download/$VERSION/$tarball"
echo "$SHA256  .cache/$tarball" | sha256sum -c -
rm -rf dist && mkdir dist
tar xzf ".cache/$tarball" -C dist --strip-components=1
cp config.json dist/config.json
cp static/* dist/
