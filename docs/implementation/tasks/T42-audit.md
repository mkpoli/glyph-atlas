# T42 Audit sampling and precision report

Goal: the published precision of accepted units, from a random sample reviewed blind.

Read first: T24 evaluation, T40, `docs/plan.md` section 6 step 7.

Outputs
- `atlas audit sample <dir> --n 2000 --strata document,script --seed 0`: a sample of accepted units
  (`review=machine` or later, not rejected), stratified, written as a review queue the interface
  serves without showing machine confidence.
- `atlas audit report <dir>`: precision of box (IoU ≥ 0.5 against the reviewer's box), of label, and
  joint, with Wilson 95% intervals, per stratum and overall; Markdown.
- The sample and the reviews are kept apart from active-learning queues so that the estimate stays
  unbiased.

Tests: sampling reproducibility with a seed; the interval computation against known values.

Acceptance: a report for the pilot committed under `docs/reports/`.

Size: small. Depends on: T23, T41.
