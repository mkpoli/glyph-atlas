# T02 Downloads, IIIF images and the image cache

Goal: fetch files politely once, know page image sizes without downloading when possible, and
address every image by checksum.

Read first: `docs/schema.md` (pages), `docs/implementation/README.md` (rate limits).

Outputs
- `src/kuzushiji_atlas/net.py`: `download(url, dest, *, pause=None, retries=5) -> Path` with a
  per-host pause (3 s default; 1 s for `codh.rois.ac.jp` and `huggingface.co`), exponential
  backoff, HTTP 429 and 503 honoured with `Retry-After`, resume through `Range` when the server
  allows it, the project User-Agent, and a browser User-Agent fallback for hosts that refuse the
  first (kept in a small host list). A 200 with an HTML body where an image or JSON was expected
  is a failure.
- `src/kuzushiji_atlas/images.py`: `service_of(url)` strips an Image API request suffix
  (`/{region}/{size}/{rotation}/{quality}.{format}`) to the service base and returns None for a
  plain file URL; `info(service)` parses `info.json` for width, height, API version (1, 2 or 3),
  tile sizes; `full_url(service, version)` gives `/full/max/0/default.jpg` for 3 and
  `/full/full/0/default.jpg` for 1 and 2; `fetch(url) -> ImageRecord` downloads into
  `cache/images/<sha256[:2]>/<sha256>.<ext>` and appends to `cache/images/index.parquet`
  (url, service, sha256, width, height, bytes, fetched_at, etag, last_modified); `register(path,
  url)` inserts a local file (a page image taken from a zip) under a URL key without a download;
  `path_for(url) -> Path | None`; `crop(url, box) -> PIL.Image`.
- CLI: `atlas images info <pages.parquet>` fills width and height from `info.json` and rewrites
  the table; `atlas images fetch <pages.parquet> [--limit N] [--document ID] [--pages a,b]`;
  `atlas images crop <units.parquet> --out <dir>` writes `<id with ':' replaced by '_'>.jpg`.

Edge cases: Image API 1 (`info.json` with `@context` of version 1, sizes absent); an `etag` change
on refetch (new checksum, old row kept with `superseded_by`); images over 10,000 px on a side
(never resized); a service that redirects.

Tests: a local HTTP server fixture serving `info.json`, a JPEG, a 429 then 200 sequence, and a
range-supporting partial download; the pause measured with an injected clock; a cache hit makes no
request; `register` then `fetch` of the same URL makes no request.

Acceptance: `atlas images fetch work/codh/pages.parquet --document codh:200006663` after T01's
acceptance stores 10 images with checksums and sizes in the index.

Size: medium. Depends on: nothing.
