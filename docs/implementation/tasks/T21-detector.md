# T21 Character detector

Goal: a class-agnostic detector of character boxes on page images, exported to ONNX.

Read first: T20 outputs; https://github.com/ndl-lab/ndlkotenocr-lite (RTMDet line detector, the
same family); `docs/implementation/README.md` (hardware).

Framework: RTMDet-s through mmdetection (Apache-2.0). If mmdetection cannot be installed against
the current PyTorch and CUDA, use RT-DETR from `transformers` (Apache-2.0). Ultralytics is
excluded (AGPL-3.0 would bind the weights).

Outputs
- `models/detector/`: config, `train.py`, `export_onnx.py`, `README.md` with data, hyperparameters,
  epochs, wall-clock, VRAM used, and metrics.
- Metrics on the held-out books: precision and recall at IoU 0.5, at the score threshold chosen on
  `val`; mean IoU of matched boxes; recall by box size decile.
- `src/kuzushiji_atlas/detect.py`: `Detector(onnx_path).boxes(image, region=None) -> list[(Box,
  score)]`, running on tiles and merging with NMS across tile borders.
- Weights published in a GitHub release under CC BY-SA 4.0 (they derive from CC BY-SA data).

Budget: 16 GB VRAM; batch size chosen to fit; training under 24 hours.

Edge cases: 連綿 in manuscripts, where the CODH ground truth splits characters that touch; ruby,
which CODH does not annotate (ignore regions where a page has known ruby blocks is out of scope for
this card, note the effect in the README).

Tests: the ONNX model on one cached page returns boxes; `boxes(region=...)` returns coordinates in
page space.

Acceptance: recall ≥ 0.95 and precision ≥ 0.95 at IoU 0.5 on the held-out printed books; numbers
for manuscript books reported separately.

Size: large. Depends on: T20.
