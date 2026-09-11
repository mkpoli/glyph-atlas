# T05 Remote zip reader

Goal: list and extract members of a large zip over HTTP range requests without downloading it.

Read first: the zip specification's central directory and zip64 records; `data/sources/hi-lab-kuzushiji.yaml`
and `data/sources/codh-kokatsuji.yaml` (both hosts serve range requests).

Outputs
- `src/kuzushiji_atlas/remotezip.py`: `RemoteZip(url)` with `.entries` (name, compressed size,
  uncompressed size, method, local header offset, crc), `.read(name) -> bytes`, `.extract(names,
  dest)`, `.iter_prefix(prefix)`. Central directory read in one range request (zip64 aware), cached
  on disk under `cache/zipindex/<sha256 of url>.parquet` so a second run does no listing request.
- Streaming extraction of many small members batches adjacent members into one range request.

Edge cases: zip64 offsets (the HI Lab zip is 4 GB); stored and deflated members; a server that
ignores `Range` (detect a 200 with full length and fail with a clear message); `.DS_Store` members.

Tests: build a zip in `tmp_path` (including one stored and one deflated member), serve it with the
local HTTP server fixture with range support, list and extract; a zip64 fixture generated with
`zipfile` and `force_zip64=True`.

Acceptance: `RemoteZip("https://data.lab.hi.u-tokyo.ac.jp/kuzushiji/2023-03-27/all.zip").entries`
lists 634,782 entries in one request (command, not test).

Size: small. Depends on: nothing.
