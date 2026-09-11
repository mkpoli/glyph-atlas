# T22 Coarse character classifier

Goal: calibrated probabilities over code points for a character crop, used to score alignments.

Read first: `work/codh` tables, `work/hilab` tables (T12), `docs/plan.md` section 6.

Outputs
- `models/classifier/`: `train.py` (ConvNeXt-tiny from `timm`, Apache-2.0), `export_onnx.py`,
  `README.md` with class list, counts, metrics.
- Classes: every code point with at least 20 crops in the training books, plus `other`; hentaigana
  are trained under their modern kana code point (the CODH label); HI Lab crops join when T12 is done.
- Calibration by temperature scaling on `val`; expected calibration error reported.
- `src/kuzushiji_atlas/classify.py`: `Classifier(onnx_path).scores(crop) -> dict[str, float]` and
  `score_class(crop, equivalence: set[str]) -> float` summing probabilities over a set of code points.

Budget: 16 GB VRAM; input 64×64 grey or 96×96, decided on `val`.

Edge cases: heavy class imbalance (sampling weights); crops smaller than 12 px.

Tests: the ONNX model on ten fixture crops returns a distribution that sums to 1.

Acceptance: top-1 ≥ 0.93 and top-5 ≥ 0.99 on held-out CODH books; calibration error below 0.03.

Size: medium. Depends on: T10.
