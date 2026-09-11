# T31 字母 classifier

Goal: for a kana unit with a known reading, choose the code point among the candidates, or say that
it cannot.

Read first: `docs/plan.md` section 5, T04 (`candidates`), T11 (古活字 units), T30, T24 (reviewed
units).

Training data: single-character 古活字 blocks with their 字母 (the code point follows from reading
and 字母 through T04; where several code points share the pair, the block is labelled with the pair
and scored at pair level); T30 renderings; reviewed pilot units with a code point. Test data:
held-out 古活字 pages and reviewed units, never renderings.

Outputs
- `models/jibo/`: `train.py`, `README.md` with metrics per reading; a model that takes a crop and a
  reading and returns probabilities over the candidate code points (masking non-candidates).
- `atlas jibo classify <dir> [--threshold 0.8]`: sets `unicode`, `jibo`, `classification=identified`
  when the top probability is at least the threshold and its margin over the second at least 0.3;
  `classification=ambiguous` otherwise with the top three in `confidence.model` and `upstream`;
  writes `confidence.jibo`.

Edge cases: readings with one candidate (skip, identified); voiced kana (classify the base);
katakana (skip in this card).

Tests: a fixture model that returns fixed scores; threshold and margin logic.

Acceptance: pair-level accuracy on held-out 古活字 pages reported; on the reviewed pilot subset,
precision of `identified` decisions ≥ 0.95.

Size: medium. Depends on: T04, T11, T30, T23.
