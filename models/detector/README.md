# Character detector

A class-agnostic detector of character boxes on pre-modern Japanese page images, trained on the CODH
boxes of T20 and exported to ONNX for `kuzushiji_atlas.detect`. It proposes boxes inside a line; the
transcription is aligned to them by T23 and a character classifier (T22) says which character each
one holds.

The framework is RT-DETR through `transformers`, on a ResNet-18 backbone, Apache-2.0. mmdetection
with RTMDet-s was tried first and rejected: `mmcv` 2.1.0 has no wheel for Python 3.14 and its source
build fails on this machine. The commands, versions and failure messages are in `SPIKE.md`.

## Data

`work/detector/{train,val,test}.json` from `scripts/build_detector_data.py` (T20), COCO with one
category `character`. An image entry is one 1024x1024 tile, naming the cached page it is cut from
(`cache_path`) and where the tile sits on that page (`origin`, `[x, y]`); the annotation `bbox` is
the source character box clipped to the tile, and `source_unit_id` names the box it came from. A
tile is cut from the cached page when the run reaches it and padded with white at the right and
bottom, so the split file stays small. A split that names materialised tiles (`tile_path`, or files
under `work/detector/tiles/`) is read from those instead.

- `iscrowd: 1` annotations are the `unreadable` and `gap` units. They are not trained on, and a
  detection that lands in one is neither a hit nor a false positive.
- Two characters that touch and that CODH ground truth splits are two boxes in the data, and the
  detector has to return them as two.
- Ruby is not annotated by CODH, so it is background during training and a false positive during
  measurement. The card excludes a detection inside a known ruby line box; those boxes are not in
  T20's split, so that exclusion is not wired yet, and the ignore regions that are wired are the two
  kinds T20 marks `iscrowd`.
- The split is by book, so a scan of a book never straddles `train`, `val` and `test`.
- Augmentation is brightness and contrast jitter only. A character mirrored or rotated is a
  different character, so neither is used.

## Hyperparameters

Every value lives in `config.yaml`; `train.py` refuses to start when the densest training tile holds
more boxes than `num_queries`, which is the one number T20's statistics set.

| Setting | Value |
| --- | --- |
| Model | `PekingU/rtdetr_r18vd`, revision `ac77a11f`, ResNet-18, 20.1M parameters |
| Labels | 1, class-agnostic (`num_labels: 1`) |
| Object queries | 600 |
| Input | 1024x1024 tiles, RGB, ImageNet mean and standard deviation, no resizing |
| Tiling | 1024 px tiles, 128 px overlap, white padding at the right and bottom |
| Loss | RT-DETR bipartite matching, as `transformers` implements it |
| Optimizer | AdamW, lr 1e-4, backbone lr 1e-5, weight decay 1e-4, grad clip 0.1 |
| Schedule | cosine with 500 warmup steps |
| Batch | 8 tiles x 2 accumulation steps = 16 |
| Precision | bfloat16 autocast |
| Epochs | 24 |

## Cost

Measured on the workstation (RTX 5070 Ti, 16 GB, driver 580.97) with 1024x1024 tiles at
`num_queries: 600` and bfloat16, on the synthetic tiles the spike built, because T20's data was not
written when this card was implemented:

| Batch | Peak VRAM | Seconds per step |
| --- | --- | --- |
| 4 | 4.14 GiB | 0.80 |
| 8 | 7.77 GiB | 1.29 |
| 12 | 11.40 GiB | 3.35 |
| 16 | 14.78 GiB | 5.32 |

Batch 8 is the configured point: two accumulation steps give an effective batch of 16 with 7.77 GiB,
which leaves room for the display and for a second run on the same GPU.
`python models/detector/train.py --profile` reproduces the table.

Wall clock for the full training: not yet run. At 1.3 s per step of 8 tiles, one epoch over the
training split costs about 1.3 s x tiles / 8, so the wall clock follows from the tile count T20
prints in `work/detector/stats.md`.

## Metrics

Not yet run: T20's splits were still being written, so there is no checkpoint and no measurement. The
commands below write `artifacts/metrics.json` with the numbers the card asks for.

```sh
export HF_HOME=$PWD/cache/huggingface           # the checkpoint is cached under cache/
uv run python models/detector/train.py --config models/detector/config.yaml
uv run python models/detector/train.py --config models/detector/config.yaml --test
uv run python models/detector/export_onnx.py --parity 10
```

`train.py` reports precision, recall, F1 at IoU 0.5 and the mean IoU of the matches on the tiles of
`val` for every epoch and keeps `artifacts/best.pt` by F1. `--test` first chooses the score
threshold on `val` (the value of `evaluation.score_grid` with the best whole-page F1), then measures
`test` at IoU 0.5 with the pages put back together after tile merging: precision, recall, F1, mean
IoU, the same split by production type (print and manuscript, from `data/splits/codh.tsv`) and recall
by box-size decile.

`export_onnx.py` writes one self-contained ONNX file with the legacy exporter, named `logits` and
`pred_boxes`, and compares PyTorch against it on ten tiles of `val`. The comparison is between the
two sets of detections rather than between query indexes: the decoder's queries have no fixed
meaning, and the encoder picks its `topk` tokens from scores that trace to within about 1e-4 but not
exactly, so a near tie can send one query to a different reference point on either side. A pair
counts as the same box above IoU 0.99, and the check passes when no pair differs by more than 1 px
and at most two detections (or 2%, whichever is larger) are on one side only. With the base
checkpoint on a synthetic tile that comparison matches 39 of 41 detections within 0.067 px
(`SPIKE.md`).

Acceptance is recall 0.95 and precision 0.95 at IoU 0.5 on the printed `test` books. If the numbers
fall short, the README gains an experiment report with the failure cases.

## Serving

```python
from kuzushiji_atlas.detect import Detector

detector = Detector("models/detector/artifacts/detector.onnx", score=0.3, nms=0.5, max_per_tile=1500)
for box, score in detector.boxes(page_image):
    ...
inside = detector.boxes_in(page_image, line_box)   # page coordinates, same geometry
```

`Detector` cuts the page into the tiles T20 trained on, runs them on `onnxruntime`'s CUDA provider
when it is available, maps the detections to page coordinates and suppresses the duplicates that two
overlapping tiles both report. A character on a tile border is one box in the output, including one
at a corner where four tiles meet, where the sliver a tile holds is dropped against the box that
contains it. The export is fixed at batch 1 and 1024x1024, which is what a tile always is. On this
machine `onnxruntime-gpu` needs `onnxruntime.preload_dlls()` before a CUDA session, because cuDNN
comes from the `nvidia-cudnn-cu13` package; `Detector` calls it and falls back to the CPU provider.

`src/kuzushiji_atlas/detect.py` is the only reader of the export. The versions it was checked
against are in `config.yaml`: Python 3.14.0, torch 2.14.0+cu130, transformers 5.17.0, onnx 1.22.0,
onnxruntime-gpu 1.30.0, numpy 2.5.3, CUDA 13.0 with driver 580.97.

## Licence

The code is MIT, as the repository. The base checkpoint `PekingU/rtdetr_r18vd` is Apache-2.0 and the
training data is CODH's CC BY-SA 4.0 (or PDM) ground truth on public-domain and CC BY-SA page
images. The weights are intended to be released under CC BY-SA 4.0, the licence of the data they
were trained on. Whether trained weights are a derivative work of their training data is not settled
in any jurisdiction, so the release states the intent, names the data, and carries the attribution
T50 generates. Weights go to a GitHub release, not to git.
