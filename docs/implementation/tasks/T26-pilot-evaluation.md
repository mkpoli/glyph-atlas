# T26 Measured pilot

Goal: the first published precision and coverage of the character-level pipeline on pages nobody
tuned on.

Read first: T23, T24, T25.

Procedure: freeze a run configuration tuned on the calibration truth; run `atlas align` on the
held-out pilot pages; annotate the held-out pages as in T25 (two reviewers, adjudication) without
showing machine output; evaluate with `atlas eval alignment`.

Outputs
- `data/pilot/truth/heldout/` as in T25.
- `docs/reports/pilot-evaluation.md`: the evaluation tables, precision against coverage curves,
  failure categories with examples, and the run configuration hash.

Acceptance: the report committed; the numbers copied into `docs/plan.md` section 7.

Size: human time, 80 to 180 pages, plus compute. Depends on: T23, T25.
