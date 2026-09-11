# T25 Calibration ground truth

Goal: complete, adjudicated character annotations for the calibration pages, from which alignment
costs are tuned and the review tool is timed.

Read first: T24 (protocol, packages), T41.

Procedure: two reviewers annotate every calibration page in full with the review interface,
creating lines and units where detection produced none; disagreements (box IoU < 0.7, or label,
字母 or reading differ) are adjudicated by a third; time per page is recorded by the interface.

Outputs
- `data/pilot/truth/calibration/`: `units.parquet`, `lines.parquet`, `groups.parquet` and the
  `reviews.jsonl` log, with `review=adjudicated` on every unit; a `MANIFEST.json` naming the
  package snapshot the annotations started from.
- `docs/reports/pilot-calibration.md`: pages, units, agreement before adjudication, minutes per
  page per reviewer, the disagreement categories.

Acceptance: every calibration page in `data/pilot/items.tsv` annotated in full; the report
committed.

Size: human time, about 20 pages. Depends on: T24, T41.
