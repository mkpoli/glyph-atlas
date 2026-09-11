# T13 Import Honkoku-Lines

Goal: every Honkoku-Lines line as a line record with its page, document, rights and split.

Read first: `data/sources/honkoku-lines.yaml`, https://huggingface.co/datasets/yuta1984/honkoku-lines
(dataset card: sample structure, companion files), `docs/schema.md`.

Inputs: `lines.jsonl.gz` (99.7 MB; fields image_id, item_id, project_id, iiif_host, image_index,
line_index, iiif_image_url, iiif_region_url, bbox, text, plain_text, plain_len, ocr_text,
edit_distance, length_ratio, det_score, image_license, image_license_url, holding_institution,
split, image_on_hf) and `items.tsv` (item_id, project_id, title, iiif_host, honkoku_url,
iiif_manifest_url, n_pages, n_lines, n_chars_plain, mean_align_distance, image_license,
image_license_url, holding_institution, split), both from the Hugging Face repository.

Schema change: add `meta: dict[str, Any]` to `Line` for upstream fields that have no column of
their own (`split`, `det_score`, `edit_distance`, `length_ratio`, `ocr_text`, `image_on_hf`).

Outputs
- `work/honkoku-lines/`: documents `hl:<item_id>` (title, holder, `source_refs` with the
  みんなで翻刻 entry id and manifest URL, image rights from `image_license` through T16's mapping or,
  before T16, a direct mapping of the licence strings that occur), pages `hl:<item_id>:<image_index>`
  with `image` = service base of `iiif_image_url` and `canvas` unknown, lines `hl:<image_id>` with
  `box` from bbox, `text_raw=text`, `text=plain_text`, `match_method="honkoku-lines-v2"`,
  `match_confidence = 1 - edit_distance`.
- Page width and height come from `info.json` (T02 `images info`) lazily; the importer leaves 0.
- CLI: `atlas import honkoku-lines [--items <ids>] [--licence PDM-1.0,CC-BY-4.0]`.

Edge cases: items in `lines.jsonl.gz` missing from `items.tsv` (create the document from the line);
`image_license` values `(unspecified)`; duplicate `image_id`.

Tests: a ten-line fixture across two items.

Acceptance: 1,169,304 lines, 4,140 documents, 79,086 pages; counts by `image_license` printed.

Size: medium. Depends on: T01, T02, T03 (to check `plain` against `plain_text` on import and log
mismatches).
