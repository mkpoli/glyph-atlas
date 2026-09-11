# T42 Audit sampling and precision report

Goal: the published precision of accepted units, from a random sample reviewed blind.

Read first: T24 (evaluation), T40, `docs/plan.md` section 6 step 7.

Outputs
- `atlas audit sample <dir> --n 2000 --strata document,script --seed 0 --run <name>`: the
  population is active units of the run with `review=machine` (accepted by the pipeline and not
  yet reviewed); a stratified sample with recorded inclusion probabilities, written as a review
  queue in which the interface hides machine confidence, predicted reading, predicted code point
  and 字母; the sample id is stored on each sampled unit's `meta.audit`.
- `atlas audit report <dir> --sample <id>`: weighted precision of box (IoU ≥ 0.5 against the
  reviewer's box), of label, and joint, with Wilson intervals per stratum and a cluster bootstrap
  over pages for the overall figures; Markdown under `docs/reports/audit-<sample id>.md`.
- Audit samples are kept apart from active-learning queues, and a unit in an audit sample is not
  used to tune anything.

Tests: sampling reproducibility with a seed; inclusion probabilities sum to the sample size; the
interval computation against known values.

Acceptance: a report for the pilot run committed under `docs/reports/`.

Size: small. Depends on: T23, T41.
