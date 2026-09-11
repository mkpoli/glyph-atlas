# T13 Import Honkoku-Lines

Goal: every Honkoku-Lines line as a line record with its page, document, rights and split.

Read first: `data/sources/honkoku-lines.yaml`, the dataset card at
https://huggingface.co/datasets/yuta1984/honkoku-lines (sample structure, companion files),
`docs/schema.md`, T06.

Inputs, pinned to the Hugging Face revision recorded in `data/sources/honkoku-lines.yaml` at import
time: `lines.jsonl.gz` (99,721,635 bytes; fields image_id, item_id, project_id, iiif_host,
image_index, line_index, iiif_image_url, iiif_region_url, bbox, text, plain_text, plain_len,
ocr_text, edit_distance, length_ratio, det_score, image_license, image_license_url,
holding_institution, split, image_on_hf) and `items.tsv` (item_id, project_id, title, iiif_host,
honkoku_url, iiif_manifest_url, n_pages, n_lines, n_chars_plain, mean_align_distance,
image_license, image_license_url, holding_institution, split).

Outputs
- `work/honkoku-lines/`: documents `hl:<item_id>` (title, holder, `source_refs` with
  `honkoku-data` = the entry id from `honkoku_url` and `iiif-manifest` = the manifest URL,
  `image_rights` from `rights.resolve(licence=image_license, url=image_license_url,
  holder=holding_institution)`, `text_rights` CC BY-SA 4.0 with the Honkoku-Lines attribution,
  `meta.split`); pages `hl:<item_id>:<image_index>` with `seq = image_index`, `image` = the
  service base of `iiif_image_url` (or the URL itself when it is not an Image API URL), width and
  height 0 until `images info` fills them; lines `hl:<image_id>` with `seq = line_index`, `box`
  from bbox, `text_raw = text`, `text = plain_text`, `match_method = "honkoku-lines-v2.0"`,
  `match_confidence = 1 - edit_distance` (a similarity, as `docs/schema.md` says), and `meta`
  holding `split`, `det_score`, `edit_distance`, `length_ratio`, `ocr_text`, `image_on_hf`,
  `iiif_region_url`.
- On import, `koji.parse(text).plain` is compared with `plain_text`; mismatches are counted and the
  first 100 written to `work/honkoku-lines/koji-mismatches.tsv`.
- CLI: `atlas import honkoku-lines [--items <ids>] [--licence PDM-1.0,CC-BY-4.0]`; the filters
  are for pilots, and acceptance counts apply to the unfiltered import.

Edge cases: items in `lines.jsonl.gz` absent from `items.tsv` (document created from the line,
`meta.items_row = false`); duplicate `image_id` with identical rows (keep one) or different rows
(fail and print both); `(unspecified)` licences resolve to `unknown`.

Tests: a ten-line fixture across two items, one licence unspecified.

Acceptance: 1,169,304 lines, 4,140 documents, 79,086 pages; counts by `image_rights.licence`
equal to the card's provider table, the reconciliation printed in the pull request.

Size: medium. Depends on: T01, T02, T03, T06.
