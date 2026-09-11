# T02 IIIF client and image cache

Goal: fetch full-size page images once, know their size without downloading when possible, and
address them by checksum.

Read first: `docs/schema.md` (pages), `~/projects/Philology/honkoku-collate/collate/harvest.py` for
the polite-fetch pattern (browser User-Agent for hosts that require it, backoff).

Outputs
- `src/kuzushiji_atlas/images.py`: `service_of(url)` (strip an Image API request suffix such as
  `/full/full/0/default.jpg` to the service base), `info(service)` (parse `info.json` for width,
  height, API version, tile sizes), `full_url(service, version)` (`/full/max/0/default.jpg` for
  version 3, `/full/full/0/default.jpg` for 2), `fetch(url) -> Path` (cache hit by URL, sha256 on
  disk, index in `cache/images/index.parquet` with url, sha256, width, height, fetched_at, status).
- Per-host rate limit (`--pause`, default 3 s; 1 s for `codh.rois.ac.jp` and `huggingface.co`),
  five retries with exponential backoff, resume of partial files, HTTP 429 and 503 honoured.
- CLI: `atlas images info <pages.parquet>` fills `width` and `height` from `info.json` and writes the
  table back; `atlas images fetch <pages.parquet> [--limit N] [--document ID]` downloads full-size
  images into the cache; `atlas images crop <units.parquet> --out <dir>` cuts unit boxes from cached
  pages (used by T50).

Edge cases: hosts that serve only Image API 1; direct JPEG URLs with no service (`info` returns
None, size read after download); images larger than 10,000 px on a side (kept, never resized);
a server that answers 200 with an HTML error page (detect by content type and treat as failure).

Tests: a local HTTP server fixture serving a `info.json` and a JPEG; rate limit measured with a
fake clock; cache hit does not touch the network; a corrupt partial file is re-fetched.

Acceptance: `atlas images fetch work/codh/pages.parquet --document codh:200006663` stores 10 images
and the index rows carry their checksums and sizes.

Size: medium. Depends on: nothing.
