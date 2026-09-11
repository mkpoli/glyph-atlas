# T14 Import the NDL古典籍OCR学習用データセット（みんなで翻刻加工データ）

Goal: 523,283 lines with boxes on pages that Honkoku-Lines may not cover.

Read first: `data/sources/ndl-minhon-ocr.yaml`, https://github.com/ndl-lab/ndl-minhon-ocrdataset.

Inputs: the zip `ndl-minhon-ocrdataset_20240207.zip`: `v1/<book>/<image>.json`,
`v2/<project>/<book>/<page>.json`, each a list of `{boundingBox: [[x,y] × 4], id, isVertical,
text, isTextline, confidence}` where `isVertical` and `isTextline` are the strings `"true"` and
`"false"`; `v1_metadata.csv` (Book ID, Book Name, Attribution, File ID(Minna De Honkoku), File
ID(NDL), Image URL, GitHub URL) and `v2_metadata.csv` (Project ID, Book ID, Book Name,
Attribution, File ID(Minna De Honkoku), Image URL, GitHub URL). File ids keep their leading zeros.

Outputs
- `work/ndl-minhon/`: documents `ndl-minhon:v1:<book>` and `ndl-minhon:v2:<project>:<book>`
  (title from Book Name, holder from Attribution, image rights through `rights.resolve(holder=…)`
  and the NDL IIIF licence page where the Image URL is on `dl.ndl.go.jp`, text rights CC BY-SA
  4.0); pages keyed by Image URL (`image` = service base for NDL IIIF URLs, the URL itself
  otherwise, `seq` = the file id as an integer); lines `<document id>:<page file id>:<id>` with
  `box` as the bounding rectangle of the four points, the points kept in `meta.points`, `vertical`,
  `text_raw = text`, `text` from T03, `match_method = "ndl-minhon-2024-02"`, `seq` by the box's
  x descending then y (right-to-left columns) for vertical lines.
- CLI: `atlas import ndl-minhon [--zip …]`.

Edge cases: `isTextline` false rows (skipped, counted); v1 pages whose transcription id and image
id differ (join on File ID(NDL)); a page JSON without a metadata row (page created with the URL
unknown, warning).

Tests: a two-page fixture, one per version, with one non-textline row.

Acceptance: 523,283 lines (66,537 v1, 456,746 v2), 32,822 pages, counts of skipped rows printed.

Size: small. Depends on: T01, T03, T06.
