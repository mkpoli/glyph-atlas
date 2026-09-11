# T24 Pilot selection, ground truth and evaluation harness

Goal: the pages the pipeline is measured on, annotated in full, and the script that measures it.

Read first: `docs/plan.md` section 7, `work/honkoku-lines/documents.parquet`, T40.

Outputs
- `data/pilot/items.tsv`: about ten items from Honkoku-Lines with `image_license` PDM-1.0 or
  CC-BY-4.0, chosen to span holders, production (print and manuscript), and script density; for
  each, 10 to 20 pages, listed with reasons. The calibration tranche is the first two pages of each.
- `atlas pilot export <out>`: page packages (image, lines, machine units) for the review tool.
- `atlas eval alignment --truth <reviews or units> --pred <units>` reporting joint precision at
  IoU 0.5, label precision alone, coverage (truth units matched), split and merge errors,
  per-document, with Wilson intervals; Markdown output.
- Ground truth produced with the review tool (T41) by two reviewers on the calibration tranche,
  disagreements adjudicated, timing recorded per page.

Edge cases: pages whose Honkoku-Lines lines cover only part of the text (record missed regions as a
separate count).

Tests: the evaluation on a synthetic truth/prediction pair with one split and one miss.

Acceptance: items file committed; calibration ground truth exported under `data/pilot/truth/`
(reviews JSON Lines, CC BY-SA 4.0); the first evaluation report committed to the pull request.

Size: medium, mostly human time. Depends on: T13, T23, T41.
