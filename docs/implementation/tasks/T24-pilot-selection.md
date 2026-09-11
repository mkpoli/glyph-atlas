# T24 Pilot selection, package format and evaluation harness

Goal: the pages the pipeline is measured on, the format in which they travel to reviewers, and the
script that measures results.

Read first: `docs/plan.md` section 7, `work/honkoku-lines/documents.parquet` (T13), `docs/schema.md`.

Outputs
- `data/pilot/items.tsv`: about ten items from Honkoku-Lines whose `image_license` is PDM-1.0 or
  CC-BY-4.0, spanning holders, production (print and manuscript), and line density, each with a
  reason; for each, 10 to 20 page ids in two groups: `calibration` (the first two pages) and
  `heldout` (the rest). Items sharing a printing block or a scan with another item go to the same
  group.
- `docs/implementation/pilot-protocol.md`: annotation instructions for reviewers: box convention
  (tight to ink, marks included with their base), what counts as one unit, how to handle 連綿,
  ruby, 割書, damaged characters, and the label policy (reading as written, 字母 when known).
- `atlas pilot export <out>`: page packages as `<out>/<page id>/` with `page.json` (page record,
  image path), `lines.parquet`, `units.parquet` (machine units when present), `image.jpg`.
- `atlas eval alignment --truth <dir> --pred <dir> [--pages …]`: matches predicted units to truth
  units by IoU ≥ 0.5 within a page, reports joint precision (box and label), box precision, label
  precision, coverage (truth units matched), split and merge errors, missed regions (truth units
  with no prediction within 0.3 IoU), per document and overall, Wilson intervals, and a
  cluster bootstrap over pages for the overall interval; Markdown output.

Tests: the evaluation on a synthetic truth and prediction pair with one split, one merge, one
miss and one false positive gives the expected counts.

Acceptance: `data/pilot/items.tsv` and the protocol committed; `atlas pilot export` produces
packages for the calibration pages.

Size: small to medium. Depends on: T13.
