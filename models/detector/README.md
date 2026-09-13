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
| Epochs | 24 configured; the shipped checkpoint had 6, see below |

## Cost

Measured on the workstation (RTX 5070 Ti, 16 GB, driver 580.97) with 1024x1024 tiles at
`num_queries: 600` and bfloat16. The first table is a batch sweep on the synthetic tiles the spike
built, before T20's data existed; the second is the real run over the training split:

| Batch | Peak VRAM | Seconds per step |
| --- | --- | --- |
| 4 | 4.14 GiB | 0.80 |
| 8 | 7.77 GiB | 1.29 |
| 12 | 11.40 GiB | 3.35 |
| 16 | 14.78 GiB | 5.32 |

Batch 8 is the configured point: two accumulation steps give an effective batch of 16 with 7.77 GiB,
which leaves room for the display and for a second run on the same GPU.
`python models/detector/train.py --profile` reproduces the table.

The real run, 45,162 training tiles (5,646 steps an epoch) and 2,713 validation tiles:

| Quantity | Value |
| --- | --- |
| Seconds per step | 0.28-0.32 (steady state; the first 100 steps average 0.83 while caches warm) |
| Wall clock an epoch | 28-29 minutes, validation included |
| Wall clock, six epochs | 3 h 14 min of GPU time, checkpoints and validation included |
| Wall clock, whole session | 5 h 25 min, of which about 1 h was spent on a run abandoned at epoch 2 and on the first 24-epoch-scheduled attempt the card's budget stopped |
| Peak VRAM (training) | 7.92 GiB with batch 8, against 16 GB |
| Peak RSS, main process | 6.5 GB sampled once a minute (`logs/t21-memory.tsv`); 118 of 347 samples missed the process and read 0, so this is a lower bound |
| Lowest free memory seen | 5 GB at 00:06, and below 8 GB in twelve samples between 23:32 and 00:06 while other work held the machine; never during the last four epochs |

A 24-epoch schedule is about 11.6 hours at this rate, not the 31 first estimated from the
warmup-dominated profile; the estimate in the pull request should use 0.30 s per step.

## The bounded run

24 epochs was the configured ceiling. The run was bounded at six for time, and the six are what
`artifacts/best.pt` holds. The curve below is what a longer run has to be judged against: it is
tile-level on `val` at IoU 0.5, best over the score grid, and every epoch's row comes from
`train.py`'s own `tile_curve` — epochs 2 to 5 were measured by the run itself, epochs 0 and 1 were
re-derived from `epoch-00.pt` and `epoch-01.pt` because they were trained before the grid was made
dense (a coarse grid put their best F1 at a point it did not sample, reporting 0.02 where the model
was at 0.53).

| Epoch | Loss | Best F1 | at score | Precision | Recall | Mean IoU | Recall at precision ≥ 0.9 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0 | 6.11 | 0.531 | 0.030 | 0.457 | 0.632 | 0.861 | 0.000 |
| 1 | 3.76 | 0.735 | 0.030 | 0.616 | 0.910 | 0.889 | 0.000 |
| 2 | 3.55 | 0.687 | 0.030 | 0.712 | 0.663 | 0.888 | 0.000 |
| 3 | 3.42 | 0.714 | 0.020 | 0.608 | 0.864 | 0.889 | 0.000 |
| 4 | 3.35 | 0.737 | 0.020 | 0.622 | 0.903 | 0.893 | 0.000 |
| 5 | 3.30 | 0.744 | 0.030 | 0.756 | 0.732 | 0.891 | 0.000 |

The loss fell by half in the first epoch and then slowly; F1 rose to about 0.74 by epoch 1 and
oscillated between 0.69 and 0.74 for the rest of the run. The model was still moving when the run
stopped, but slowly: the last epoch improved on the best by 0.007. There is no operating point on
the curve where precision reaches 0.9 with a useful recall, at any epoch; the closest is epoch 5's
0.05 point at precision 0.86 and recall 0.29.

## Metrics

`artifacts/metrics.json` holds the per-epoch curve above and the whole-page measurement below. The
commands that produce them:

```sh
export HF_HOME=$PWD/cache/huggingface           # the checkpoint is cached under cache/
uv run python models/detector/train.py --config models/detector/config.yaml --epochs 6
uv run python models/detector/train.py --config models/detector/config.yaml --test \
    --weights models/detector/artifacts/best.pt
uv run python models/detector/export_onnx.py --parity 10
```

`--test` measures a trained checkpoint only: it stops unless `--weights <checkpoint>` or
`--resume <checkpoint>` names one, so a run that forgot the weights cannot report an untrained
network. It chooses the score threshold on `val` first, then measures `test` at IoU 0.5 with the
pages put back together after tile merging.

The shipped checkpoint is epoch 5 of the six, `artifacts/best.pt`. The score chosen on `val` is
**0.02**, where `val` gives precision 0.740, recall 0.947, F1 0.831 over 295 pages. On the 477 pages
of `test`:

| Group | Precision | Recall | F1 | Mean IoU | TP | FP | FN |
| --- | --- | --- | --- | --- | --- | --- | --- |
| overall | 0.700 | 0.928 | 0.798 | 0.875 | 115,293 | 49,463 | 8,903 |
| woodblock (printed) | 0.639 | **0.968** | 0.770 | 0.864 | 35,974 | 20,287 | 1,194 |
| unknown | 0.750 | 0.903 | 0.819 | 0.881 | 68,134 | 22,722 | 7,328 |
| manuscript | 0.634 | 0.967 | 0.766 | 0.875 | 11,185 | 6,454 | 381 |

Recall by decile of truth box area, smallest first:

| Decile | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| upper area (px²) | 2,535 | 3,432 | 4,465 | 5,538 | 6,552 | 7,650 | 8,976 | 10,804 | 13,932 | — |
| recall | 0.812 | 0.878 | 0.923 | 0.941 | 0.941 | 0.943 | 0.952 | 0.956 | 0.955 | 0.982 |

### Acceptance

The card asks for recall ≥ 0.95 and precision ≥ 0.95 at IoU 0.5 on the printed `test` books. It is a
joint target: raising precision by lowering recall does not meet it, and vice versa.

Measured on the whole `test` split at two suppressions, the second chosen on `val` by the sweep the
card asks for (`models/detector/sweep_nms.py`, results in `models/detector/nms-sweep.json`):

| Whole test split (477 pages) | `nms` 0.5, as shipped | `nms` 0.2, chosen on val |
| --- | --- | --- |
| precision | 0.6998 | **0.8575** |
| recall | **0.9283** | 0.8824 |
| F1 | 0.7980 | **0.8698** |
| false positives | 49,463 | 18,210 |
| printed books: precision / recall | 0.6394 / 0.9679 | **0.8350** / 0.8993 |

The suppression is the largest single improvement measured on this card: the duplicate and split false
positives it removes were most of the gap, and the val and test curves agree on the direction (val F1
0.8995 at 0.2 against 0.8309 at 0.5). It is still short of the joint target, and the trade is explicit
— **recall at the measured setting is 0.8824 overall and 0.8993 on the printed books, both below the
card's 0.95**, where the shipped setting met the recall bar (0.9283 and 0.9679) and missed precision
badly. Neither column meets both halves, so the card's acceptance is not reached either way.

What the two settings together show is where the remaining gap is. Requiring recall 0.95 puts the
operating point back near where precision collapses, and three quarters of the false positives there
sit on ink CODH never annotated (see Failure cases), so precision at that recall cannot approach 0.95
by thresholding alone. The honest reading is that the missing ruby and decoration annotations, not the
suppression and not the number of epochs, are what stand between this detector and the card's target;
that is a data problem, and the section below says what obtaining it would take.
- Mean IoU of the matches, 0.86-0.88, says the misses are not badly placed boxes; the model finds
  the characters and then reports more of them than CODH annotates.

### Failure cases

Every false positive of the test measurement, at the shipped operating point (49,463 of them):

| Kind | Count | Share |
| --- | --- | --- |
| overlaps no annotated box at all (IoU < 0.1) | 36,790 | 74.4% |
| overlaps an annotated box but below the match (IoU 0.1-0.5): a duplicate or a split | 12,673 | 25.6% |

- Median area of a false positive 4,627 px² against 6,498 px² for a true positive; only 4.1% of the
  false positives are under 1,000 px², so they are not speckle.
- False positives against annotated boxes: woodblock 20,287 to 37,168, unknown 22,722 to 75,462,
  manuscript 6,454 to 11,566. The printed books are the worst per character, which is where the
  target is missed.
- The worst pages are all in うすゆき物語 (bid 200021063), 278 to 299 false positives a page.
- Small characters are found less often: recall falls from 0.98 in the largest decile to 0.81 in the
  smallest, boxes under about 50 px a side.
- CODH does not annotate ruby, punctuation or decoration, and 74% of the false positives sit on ink
  with no box near it. The card excludes detections inside known ruby line boxes from the metrics;
  T20's split carries no ruby boxes, so that exclusion is not in these numbers and the true
  precision on annotated ink is higher than 0.70. The exclusion cannot be applied to this data at
  all: the four `test` books are CODH books, whose import carries units and no lines, and no table in
  the repository holds a line layout for them — `work/honkoku-lines` covers different books and
  shares no page with the 477 test pages. Ruby line boxes for these books would have to be obtained
  from CODH or annotated; until then every precision figure here is a floor, and the size of the
  floor is unknown.

### The longer schedule: measured, and not finished

Two epochs of the configured 24-epoch schedule were trained into `models/detector/artifacts-24e/` and
then stopped and measured at the user's direction. The shipped artifact is untouched: the pilot's run
names `artifacts/detector.onnx` and its unit ids are fingerprinted to `rtdetr_r18vd-6e`.

Measured on the whole validation split for the operating point and the whole `test` split at IoU 0.5:

| Whole test split (477 pages) | shipped, 6 epochs | epoch 1 of 24 |
| --- | --- | --- |
| operating point chosen on val | 0.02 | 0.03 |
| precision | **0.6998** | 0.5692 |
| recall | **0.9283** | 0.8947 |
| F1 | **0.7980** | 0.6958 |
| mean IoU | **0.8753** | 0.8682 |
| true / false / missed | 115,293 / 49,463 / 8,903 | 111,120 / 84,103 / 13,076 |

On a fixed 25-page subset of `val`, measured with the same detections, the two epochs of the new run
are `f1 0.7554` (epoch 0) and `f1 0.6971` (epoch 1), against `f1 0.8570` for the shipped final
checkpoint. So within the new run the second epoch is worse than its first, and both are behind the
shipped model. **What this establishes is that this particular run is not an improvement, not that 24
epochs cannot be**: the run stopped after two of twenty-four, and a curve that dips at epoch 1 can
pass its starting point later. The dip is real and repeatable; whether it is a property of the
schedule or of this run's first two epochs is not something two epochs can tell.

#### Why the two runs are not the same schedule

The stored `training.epochs` is 24 in both checkpoints, because it comes from the YAML, but the
scheduler's horizon comes from the resolved `--epochs`. The shipped run was invoked with `--epochs 6`
and resumed twice, and the checkpoints say so in their own state:

| | shipped `best.pt` | `24e/epoch-01.pt` |
| --- | --- | --- |
| checkpoint epoch | 5 | 1 |
| `scheduler.last_epoch` | 16,938 | 5,646 |
| `_last_lr` | `[0.0, 0.0]` | `[9.8562e-05, 9.8562e-06]` |

With 45,162 training tiles at batch 8 and `grad_accumulation` 2, an epoch is 2,823 optimizer steps, so
six epochs is 16,938 steps and 24 epochs is 67,752. The step counts in the table are what the
checkpoints recorded; the loss of the shipped run's last epoch is 3.3010, and the experiment's second
is 3.7636. The earlier stored curves for the shipped run's epochs 0 and 1 were re-derived after its
grid changed, so its per-epoch rows and the experiment's are not the same measurement and are not
compared here.

That the horizons differ is arithmetic. Whether the horizon is *why* the F1s differ is not established
by these numbers: the two runs also resume differently, were measured at different times, and differ
in every one of the 2,823 steps after the first epoch. The concrete thing the difference cost is
provenance, which is fixed — `train.py` now records the resolved runtime (resolved and configured
epochs, accumulation, scheduler steps an epoch and in total, warmup, precision, seed, relative paths)
in both `metrics.json` and every checkpoint, and `tests/test_detector_runtime.py` pins it.

#### What is repeatable, and what is not yet known

Measured, not assumed: one checkpoint loaded once, one fixed batch of four tiles, `model.eval()`, the
batch run twice — raw logits and `pred_boxes` are **bit-identical** (max absolute difference 0.0, equal
SHA-256 over both arrays). Re-scoring the same cached detections twice gives the same F1 to four
decimals. So the scoring path and the forward pass repeat for that checkpoint, input and precision;
what is *not* established is repeatability across a fresh process for the full pipeline, which would
need the same measurement run twice end to end.

What the evidence still supports trying, in order: a denser score grid or a calibrated head, since the
whole operating range of the shipped model lies between 0.005 and 0.05; a val-chosen sweep of `nms`
rather than of the score, for the quarter of false positives that are duplicates or splits of a real
character; and finer tiles or more queries for the smallest decile, the weakest one. More epochs of
this schedule is no longer first on that list, and finishing it would need the machine to itself for
about 41 hours.

`models/detector/compare_checkpoints.sh <checkpoint>` runs this measurement for any checkpoint into
`models/detector/eval-<name>-<hash>/`, with the shipped baseline checksummed before and after, and
refuses to run while a trainer is live. The epoch-1 report is kept at
`models/detector/eval-epoch-01-37e7513273c8/`.

## The longer schedule was run, and it did not help

A second run of the configured 24-epoch schedule was trained into `models/detector/artifacts-24e/`,
two epochs of it, and then stopped and measured at the user's direction. The shipped artifact stays
where it is: the pilot's run names `artifacts/detector.onnx` and its unit ids are fingerprinted to
`rtdetr_r18vd-6e`, so the experiment never touched it.

**Epoch 1 is worse than the shipped six-epoch model on every headline measure**, measured on the whole
validation split for the operating point and the whole `test` split at IoU 0.5:

| Whole test split (477 pages) | shipped, 6 epochs | epoch 1 of 24 |
| --- | --- | --- |
| operating point chosen on val | 0.02 | 0.03 |
| precision | **0.6998** | 0.5692 |
| recall | **0.9283** | 0.8947 |
| F1 | **0.7980** | 0.6958 |
| mean IoU | **0.8753** | 0.8682 |
| true / false / missed | 115,293 / 49,463 / 8,903 | 111,120 / 84,103 / 13,076 |
| woodblock precision / recall | 0.6394 / **0.9679** | 0.5479 / 0.8832 |
| manuscript precision / recall | 0.6341 / **0.9671** | 0.5402 / 0.8628 |

The false positives are what moved: 84,103 against 49,463, while true positives fell by 4,173. The
model at epoch 1 finds slightly fewer characters and claims far more that are not annotated, so its
precision is 0.13 lower and its F1 0.10 lower. Recall by box size decile says the same thing in
detail — epoch 1 is better only on the smallest boxes (0.854 against 0.812 in decile 1) and worse in
every decile above the third, by 0.14 in the largest.

What this does not establish is that 24 epochs would not help: the run stopped after two, and a curve
that is worse at epoch 1 can still pass the six-epoch result later. What it does establish is that
this schedule is not the cheap win the six-epoch curve suggested — the six-epoch run's own best F1
rose monotonically to 0.744 on `val`, while this run's `val` F1 went 0.646 at epoch 0 and 0.570 at
epoch 1, backwards. Two runs of the same schedule from the same seed disagreeing this early is itself
the finding, and the honest next step is to find out why — a fixed data order, a scheduler restarted
per epoch, or a warmup that never completes — before spending 41 more hours on it.

What the evidence still supports trying, in order: a denser score grid or a calibrated head, since the
whole operating range of the shipped model lies between 0.005 and 0.05; a val-chosen sweep of `nms`
rather than of the score, for the quarter of false positives that are duplicates or splits of a real
character; and finer tiles or more queries for the smallest decile, the weakest one. More epochs of
this schedule is no longer first on that list.

`models/detector/compare_checkpoints.sh <checkpoint>` runs this measurement for any checkpoint into
`models/detector/eval-<name>-<hash>/`, with the shipped baseline checksummed before and after; the
report is rendered by `models/detector/compare_report.py`, which `tests/test_detector_report.py`
covers without a GPU. The epoch-1 report is kept at `models/detector/eval-epoch-01-37e7513273c8/`.

Its rate depended on what else held the machine: 0.97 to 2.26 s a step, epoch 0 taking 1 h 42 min
against the shipped run's 30 min because another project held all 16 cores for part of it. At that
rate the 24 epochs were about 41 hours, which is why the run was bounded rather than left to finish.

## Serving

```python
from kuzushiji_atlas.detect import Detector

# 0.02 is the score chosen on val for this checkpoint; the default 0.3 would report almost nothing.
detector = Detector("models/detector/artifacts/detector.onnx", score=0.02, nms=0.5, max_per_tile=1500)
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
