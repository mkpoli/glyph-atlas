# T31 字母 classifier

Goal: for a kana unit with a known reading, score the code points among its candidates, identify
one when the evidence allows, and say so when it does not.

Read first: `docs/plan.md` section 5, T04, T11, T30, T25, T26, `docs/schema.md` (candidates).

Data: single-character 古活字 units with a code point (`identified`) or a candidate set
(`ambiguous`) from T11; T30 renderings; adjudicated pilot units with a code point. Test data:
held-out 古活字 pages (split by page, and repeated impressions of one type block kept in one split)
and held-out pilot units; renderings are never test data.

Model: the T22 backbone with a head over all hentaigana and hiragana code points; at inference
the output is masked to `refs.candidates(reading)` and renormalised. Loss: cross-entropy for
labelled units and, for units labelled with a candidate set S, `-log Σ_{c∈S} p(c)`.

Outputs
- `models/jibo/`: `train.py`, `export_onnx.py`, `README.md` with metrics per reading and the
  counts of training units by source; `classes.json`.
- `src/kuzushiji_atlas/jibo.py`: `JiboClassifier(onnx_path).scores(crop, reading) -> list[Candidate]`.
- `atlas jibo classify <dir> --run <name>`: for kana units with `classification=unassessed` writes
  `candidates` (all scored), and sets `unicode`, `jibo`, `classification=identified` when the top
  probability is at least the run's threshold (default 0.8) and its margin over the second at
  least 0.3, otherwise `classification=ambiguous`; `confidence.jibo` = the top probability;
  `confidence.model` names the run. The ordinary hiragana code point is always among the
  candidates, so a reading never has a single candidate.

Edge cases: voiced kana (the base is classified, the mark kept); katakana (skipped in this card);
crops under 12 px (left `unassessed`).

Tests: a fixture model with fixed scores; threshold and margin logic; the partial-label loss on a
two-candidate example.

Acceptance: on held-out 古活字 pages, accuracy at candidate-set level reported; on the adjudicated
pilot units, precision of `identified` decisions ≥ 0.95 with the number of decisions and the
coverage reported beside it.

Size: medium. Depends on: T04, T11, T22, T30, T25.
