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

The card asks for recall ≥ 0.95 and precision ≥ 0.95 at IoU 0.5 on the printed `test` books.

- **Recall on the printed books is 0.968, above the target.** The other groups are 0.903 (unknown)
  and 0.967 (manuscript).
- **Precision is 0.639 on the printed books, far below the target**, and 0.700 overall.
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

### What a longer run should test

A second run of the full 24-epoch schedule is running into `models/detector/artifacts-24e/` (the
shipped artifact stays where it is, because the pilot run names it and its units are fingerprinted to
it). It checkpoints an epoch at a time, so its `epoch-00.pt`, `metrics.json` and the table above can be
compared as they appear.

Its rate depends on what else holds the machine. Measured at the start of the run: **1.35 to 2.26 s a
step** with the load average at 26 to 31 and the GPU at 0%, because another project on this workstation
was running `vite build` and `svelte-check` across 16 cores. At the rate the six-epoch run achieved on
an idle machine (0.30 s a step) the 24 epochs are 11.6 hours; at the rate measured under that load they
are several days. The run is left to checkpoint an epoch at a time rather than killed, and the numbers
it produces are only comparable with the table above if the machine is quiet while it trains.

The run was bounded at six of the configured 24 epochs, and the curve says it was still moving:
the loss fell from 6.11 to 3.30, the best F1 from 0.531 to 0.744, and the last epoch still improved
on the best by 0.007. The confidence scale is the clearest sign of what more training buys: the best
F1 sits at score 0.02-0.03 in every epoch, while the matched boxes have a mean IoU of 0.89, and
RT-DETR's class head is trained to predict that IoU. The head has not caught up with the boxes yet.

In order of what the evidence supports:

1. **More epochs on the same schedule.** 24 epochs is about 11.6 hours at the measured 0.30 s a step.
   The curve is monotone after epoch 2, so this is the cheapest thing to try, and it is what the
   configured ceiling was for.
2. **A denser score grid or a calibrated head**, since the whole operating range of the shipped
   model lies between 0.005 and 0.05.
3. **Tightening the suppression rather than the score**, for the quarter of false positives that are
   duplicates or splits of a real character: a val-chosen sweep of `nms`, not of the test numbers.
4. **Smaller characters**, the weakest decile: finer tiles or more queries, at more VRAM and time.

The production type comes from the `production` field every tile carries, cross-checked against
`data/splits/codh.tsv` (`bid, title, production, split`, written by `scripts/build_codh_split.py`),
which `train.py` refuses to disagree with. The `test` split holds three groups, not two:

| Group | Books | Pages | Tiles | Unique boxes |
| --- | --- | --- | --- | --- |
| woodblock | 200021063 うすゆき物語, 200021802 料理物語 | 161 | 2,187 | 37,168 |
| unknown | brsk00000 物類称呼 | 238 | 2,743 | 75,462 |
| manuscript | 200010454 源氏物語 | 78 | 433 | 11,566 |

`unknown` is not a third kind of book: it is the stratum whose NIJL IIIF manifest does not exist, so
neither the CODH book page nor a manifest states whether the book is a woodblock print or a
manuscript. Its numbers are reported apart rather than folded into the printed group, because a
detector's behaviour on it cannot be attributed to either production.

`epoch-NN.pt` checkpoints were deleted once the dense curves were in `metrics.json`; `best.pt` and
`last.pt` remain.

`export_onnx.py` writes one self-contained ONNX file with the legacy exporter, named `logits` and
`pred_boxes`, and compares PyTorch against it on ten tiles of `val`. The comparison is between the
two sets of detections rather than between query indexes: the decoder's queries have no fixed
meaning, and the encoder picks its `topk` tokens from scores that trace to within about 1e-4 but not
exactly, so a near tie can send one query to a different reference point on either side. A pair
counts as the same box above IoU 0.99, and the check passes when no pair differs by more than 1 px
and at most two detections (or 2%, whichever is larger) are on one side only. On the shipped
checkpoint, over ten tiles of `val`, it matches 79 detections, none beyond the 1 px tolerance, with
a largest corner difference of 0.44 px and one detection the PyTorch side had and the export did
not (`logs/export-t21.log`). The export is 80,845,313 bytes, opset 17, with the two outputs the
detector decodes.

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
