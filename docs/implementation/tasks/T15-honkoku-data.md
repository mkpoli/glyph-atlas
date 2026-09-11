# T15 Import みんなで翻刻データ v3 and the platform manifests

Goal: page-level transcriptions for every public entry, to find pages that no line dataset covers
and to record which revision of a transcription the dataset used.

Read first: `data/sources/honkoku-data.yaml`, https://github.com/yuta1984/honkoku-data,
https://wiki.honkoku.org/doku.php?id=api, `docs/schema.md` (page_texts).

Inputs: a clone of honkoku-data pinned by commit (`v3/<project>/info.tsv` with id, label,
manifestUrl, projectId, size, progress, attribution, thumbnail; `v3/<project>/<entry>/<NNN>.txt`);
IIIF manifests from `manifestUrl`, fetched through T02 into `cache/manifests/<sha256 of url>.json`;
the platform API `GET https://app.honkoku.org/api/entries/{id}` for the current text and
`updatedAt`.

Outputs
- `work/honkoku-data/`: documents `hk:<entry>` (title from label, manifest in `source_refs`,
  holder from attribution, image rights from the manifest through `rights.manifest_rights` with a
  fallback to `resolve(holder=…)`, text rights CC BY-SA 4.0); pages `hk:<entry>:<n>` where page
  `n` (1-based, from the file name `NNN.txt`) maps to the n-th canvas of the manifest's first
  sequence (Presentation 2) or of `items` (Presentation 3), with `canvas`, `image` = the service of
  the canvas's first image, width and height from the canvas; `page_texts` rows (`page_id`,
  `source = "honkoku-data"`, `revision` = the clone commit, `text_raw` = the file).
- `atlas import honkoku-data --clone <dir> [--projects a,b]`; `atlas honkoku refresh <entry>`
  writes a second `page_texts` row per page with `source = "honkoku-api"` and `revision =
  updatedAt`; `atlas coverage` joins documents of `work/honkoku-lines`, `work/ndl-minhon` and
  `work/honkoku-data` on the entry id and writes `work/coverage.tsv` (entry, pages with text, pages
  with lines from each source).

Edge cases: a manifest that fails to fetch (pages created from the text files with `image` empty
and `meta.manifest_error`); more text files than canvases (extra pages created with `canvas`
null and a warning); 【右丁】/【左丁】 markers left in `text_raw`; v1 entries, which are out of scope.

Tests: a fixture project with one entry, two pages, and Presentation 2 and 3 manifests served
locally.

Acceptance: 70 projects imported; `work/coverage.tsv` attached to the pull request with the number
of pages that have text and no lines.

Size: medium. Depends on: T01, T02, T06.
