# T21 Character detector

Goal: a class-agnostic detector of character boxes on page images, exported to ONNX, with a
measured precision and recall on held-out books.

Read first: T20 outputs, `docs/implementation/README.md` (hardware), the RTMDet line detector in
https://github.com/ndl-lab/ndlkotenocr-lite.

Step 1, time-boxed to one day: an installation spike. Try mmdetection with RTMDet-s against the
PyTorch build that runs on the machine's GPU (Blackwell needs CUDA 12.8 wheels; mmcv's version
pins may block this). Record in `models/detector/SPIKE.md` whether one training step, a checkpoint
reload and an ONNX inference worked. If they did not, use RT-DETR (`transformers`, Apache-2.0)
with a small backbone and the number of object queries raised above the densest tile's box count
from T20's statistics. Ultralytics is excluded (AGPL-3.0). Pin the framework, checkpoint and
preprocessing in `models/detector/config.yaml` before training.

Outputs
- `models/detector/`: config, `train.py`, `export_onnx.py`, `README.md` with data, hyperparameters,
  epochs, wall-clock, peak VRAM, and metrics.
- `src/kuzushiji_atlas/detect.py`: `Detector(onnx_path, score=0.3, nms=0.5, max_per_tile=1500)`
  with `.boxes(image) -> list[(Box, score)]` running on tiles with the T20 geometry, mapping to
  page space, and suppressing duplicates across tile borders; `.boxes_in(image, region)` restricts
  to a region and returns page coordinates.
- Metrics on the `test` books at IoU 0.5, reconstructed to whole pages after tile merging:
  precision, recall, F1 at the score threshold chosen on `val`; mean IoU of matches; recall by box
  size decile; all reported for print and manuscript separately. A PyTorch-to-ONNX parity test
  (same boxes within 1 px on ten pages).
- Weights in a GitHub release, licence stated in the README (CC BY-SA 4.0 intended; the legal
  status of weights trained on CC BY-SA data is not settled, and the README says so).

Budget: 16 GB VRAM; starting point 1024² tiles, mixed precision, batch 2 for RTMDet-s; profiled
and raised. Training runs alone on the GPU.

Edge cases: 連綿, where CODH ground truth splits touching characters; ruby, which CODH does not
annotate and which therefore counts as background in training and as false positives in
evaluation, so the metrics exclude detections inside ruby-role line boxes where those are known.

Tests: the ONNX model on one cached page returns boxes; `boxes_in` returns page coordinates; the
cross-tile duplicate test with a box on a border.

Acceptance: recall ≥ 0.95 and precision ≥ 0.95 at IoU 0.5 on the printed `test` books; manuscript
numbers reported; if the targets are missed, an experiment report with the failure cases.

Size: large. Depends on: T20.
