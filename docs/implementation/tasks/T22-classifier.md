# T22 Coarse character classifier

Goal: calibrated probabilities over code points for a character crop, used to score alignments.

Read first: `work/codh` tables (T10), `data/splits/codh.tsv` (T20), `docs/plan.md` section 6.

Outputs
- `models/classifier/`: `train.py` (ConvNeXt-tiny from `timm`, starting checkpoint
  `timm/convnext_tiny.fb_in22k_ft_in1k`, Apache-2.0), `export_onnx.py`, `README.md` with class
  list, counts, metrics; `classes.json` fixing the class order.
- Data manifests `work/classifier/{train,val,test}.parquet` (unit id, crop path, label) built from
  CODH crops cut from cached pages with T02, split by `data/splits/codh.tsv`. Classes: every code
  point with at least 20 crops in `train`, plus `other` for the rest; `other` is a target during
  training and an abstention at inference, never an identification.
- Preprocessing: grey, aspect preserved by padding to square, 96×96; class-balanced sampling.
- Calibration by temperature scaling on `val` crops sampled in proportion to their frequency;
  expected calibration error reported with 15 bins.
- `src/kuzushiji_atlas/classify.py`: `Classifier(onnx_path).scores(crop) -> dict[str, float]` and
  `score_set(crop, code_points: set[str]) -> float` summing over a set.
- HI Lab crops (T12) are a separate experiment `train_with_hilab.py` with its own manifests and
  README section, never mixed into the baseline.

Budget: 16 GB VRAM; batch 64 at 96², mixed precision.

Tests: the ONNX model on ten fixture crops returns distributions summing to 1; `score_set` over
all classes equals 1.

Acceptance: on `test` crops whose class is in `classes.json`, top-1 ≥ 0.93 and top-5 ≥ 0.99;
accuracy over all `test` crops reported beside it; calibration error below 0.03; macro F1 reported.

Size: medium. Depends on: T10, T20.
