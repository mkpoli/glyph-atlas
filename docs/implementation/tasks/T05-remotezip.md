# T05 Remote zip reader

Goal: list and extract members of a large zip over HTTP range requests.

Read first: the zip format's end-of-central-directory, zip64 locator and central directory
records; `data/sources/hi-lab-kuzushiji.yaml`, `data/sources/codh-kokatsuji.yaml`.

Outputs
- `src/kuzushiji_atlas/remotezip.py`: `RemoteZip(url)` with `.entries` (name, method, compressed
  and uncompressed sizes, crc32, local header offset), `.read(name) -> bytes` (crc checked),
  `.extract(names, dest)` (paths sanitised, no `..`), `.iter_prefix(prefix)`. Listing: a HEAD for
  the size and `ETag`, one range request for the last 64 KiB, one more for the central directory
  when it is not inside that tail (zip64 aware); at most three requests and the central directory's
  size in bytes. `Content-Range` is checked on every response and a 200 without `Content-Range`
  fails with a message that the server ignores ranges. The listing is cached in
  `cache/zipindex/<sha256 of url>.parquet` together with the `ETag` and size, and reused only when
  both still match.
- Extraction of many small members batches members whose local headers lie within 1 MiB of each
  other into one range request.

Tests: a zip built in `tmp_path` with stored and deflated members served by the range-capable
local server fixture; a zip64 fixture built by appending more than 65,535 tiny members
(`zipfile` writes zip64 central directory records then); a server that ignores ranges; a member
with a corrupted byte fails the crc check.

Acceptance: `scripts/check_remotezip.py https://data.lab.hi.u-tokyo.ac.jp/kuzushiji/2023-03-27/all.zip`
lists 634,782 entries within three requests (command, not test).

Size: small. Depends on: nothing.
