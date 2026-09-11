# T14 Import the NDL古典籍OCR学習用データセット（みんなで翻刻加工データ）

Goal: 523,283 lines with boxes on pages that Honkoku-Lines may not cover.

Read first: `data/sources/ndl-minhon-ocr.yaml`, https://github.com/ndl-lab/ndl-minhon-ocrdataset.

Inputs: the 45 MB zip (`v1/<book>/<image>.json`, `v2/<project>/<book>/<page>.json`, each a list of
`{boundingBox: [[x,y]×4], id, isVertical, text, isTextline, confidence}`), `v1_metadata.csv`,
`v2_metadata.csv` (Project ID, Book ID, Book Name, Attribution, File ID, Image URL, GitHub URL).

Outputs
- `work/ndl-minhon/`: documents `ndl-minhon:<version>:<book>`, pages keyed by Image URL, lines
  `ndl-minhon:<version>:<book>:<page>:<id>` with `box` as the bounding rectangle of the four points,
  `vertical` from `isVertical`, `text_raw=text`, `text` from T03, `match_method="ndl-minhon-2024"`.
- Where the Image URL is an NDL IIIF URL, `pages.image` is its service base; other hosts keep the
  direct URL.
- CLI: `atlas import ndl-minhon [--zip ...]`.

Edge cases: `isTextline` false rows (skip, count); v1 image ids that differ from transcription ids
(use the CSV's File ID(NDL)).

Tests: a two-page fixture, one per version.

Acceptance: 523,283 lines (66,537 v1, 456,746 v2), 32,822 pages.

Size: small. Depends on: T01, T03.
