# T15 Import みんなで翻刻データ v3 and the platform manifests

Goal: page-level transcriptions for every public entry, to find pages that no line dataset covers
and to record transcription revisions.

Read first: `data/sources/honkoku-data.yaml`, https://github.com/yuta1984/honkoku-data,
https://wiki.honkoku.org/doku.php?id=api, `~/projects/Philology/honkoku-collate/collate/honkoku.py`.

Inputs: a clone of honkoku-data (183 MB): `v3/<project>/info.tsv` (id, label, manifestUrl,
projectId, size, progress, attribution, thumbnail) and `v3/<project>/<entry>/<NNN>.txt`; the
platform API `GET https://app.honkoku.org/api/entries/{id}` for the current text and status.

Schema change: a `page_texts` table (page_id, source, revision, text_raw) for whole-page text.

Outputs
- `work/honkoku-data/`: documents `hk:<entry>` with title, manifest, attribution as given; pages
  from the manifest's canvases (fetched with T02's client, cached under `cache/manifests/`), page N
  of the entry mapped to canvas N; `page_texts` rows from the `.txt` files.
- `atlas import honkoku-data --clone <dir> [--projects a,b]`; `atlas honkoku refresh <entry>`
  writes the API text with its `updatedAt` as revision.
- `atlas coverage` joins documents across `work/honkoku-lines`, `work/ndl-minhon` and
  `work/honkoku-data` on the entry id and prints pages with text and no lines.

Edge cases: manifests that fail to fetch (page rows with `image` empty and a note in `meta`);
entries with more text files than canvases; 【右丁】/【左丁】 markers inside a page text.

Tests: a fixture project with one entry, two pages, and a manifest served locally.

Acceptance: 70 projects imported; the coverage report committed to the pull request.

Size: medium. Depends on: T01, T02.
