# Character classifier

Calibrated probabilities over code points for one character crop. The alignment scores a detection
against the transcription with it, the extraction accepts a crop by it, and the review panel shows
its answers first. `glyph_atlas.classify` serves the exported ONNX file.

The backbone is CAFormer-S18 through `timm`, started from `caformer_s18.sail_in22k_ft_in1k`
(ImageNet-22k, fine-tuned on ImageNet-1k, Apache-2.0, 31.3M parameters with the head), with its
1000-class head replaced by one over the class list. CAFormer is a MetaFormer: two convolution
stages, then two attention stages. `models/benchmark/README.md` compares it with the other backbones
tried. The training data is CODH's own boxes and the HI Lab crops.

## Data

`build_manifests.py` cuts one crop per CODH unit to the box CODH drew and writes
`work/classifier/{train,val,test}.parquet`, split by book from `data/splits/codh.tsv`, so a hand, a
block and a scan stay on one side. `build_combined.py` adds the 東京大学史料編纂所 くずし字データセット
(`atlas import hilab --download`) and writes `work/classifier-combined/{train,val,test}.parquet` and
the class list. HI Lab records no book; its held-out tenth is `models/benchmark/bench.py`'s
`hilab_split`, drawn by blocks of 200 ids, and goes to `test` only.

Crops are grey JPEGs cut exactly to the box, with no margin and no resizing; the preprocessing that
resizes is `glyph_atlas.classify`, called by the training loader and the export alike.

| split | crops | source |
| --- | ---: | --- |
| train | 1,204,964 | 917,309 CODH (38 books), 287,655 HI Lab |
| val | 44,782 | CODH (2 books) |
| test | 161,802 | 124,196 CODH (4 books), 37,606 HI Lab |

By script, the training crops are 641,768 hiragana, 528,034 kanji, 18,929 katakana, 16,232
symbols and one Latin character, over 6,245 code points.

## Classes

A class is a code point with at least 5 crops in `train`; everything below that line, and every code
point the classifier was not trained on, is `other`. `classes.json` fixes the order: descending
number of training crops, then code point, with `other` last. It is the order of the model's
output, and `glyph_atlas.classify` reads it from beside the export (or one directory above it,
which is how `artifacts/classifier.onnx` finds `models/classifier/classes.json`).

- 3,428 identification classes plus `other`; 3,264 of them are kanji.
- 1,199,293 of the 1,204,964 training crops carry an identification class.
- 172 classes hold 1,000 crops or more and 992 hold 100 or more.

`other` is a target during training and an abstention at inference, never an identification.
`Classifier.score_set` counts it as the mass of every code point the classifier was not trained on,
so a set of code points that all lie outside the class list scores at the abstention probability
instead of at zero.

## Preprocessing

Grey, aspect preserved by padding to a square, 128×128.

1. The crop is converted to one channel. A crop with transparency is put on a white ground first.
2. The grey crop is pasted centred on a white square of its longer side, so a wide character stays
   wide; nothing is stretched.
3. The square is resized to 128×128 with a bilinear filter.
4. The grey value is scaled to [0, 1], normalised with mean 0.449 and standard deviation 0.226 (the
   ImageNet statistics averaged over the three channels) and replicated to the three channels the
   backbone takes.

The size is `config.yaml`'s; the export fixes it in its input, and `Classifier` reads it from there.

## Hyperparameters

Every value is in `config.yaml`.

| Setting | Value |
| --- | --- |
| Model | `caformer_s18.sail_in22k_ft_in1k` through `timm` 1.0.29, 31.3M parameters |
| Head | the checkpoint's MLP head, its last layer over 3,429 classes |
| Input | 128×128 grey, aspect preserved, replicated to three channels |
| Batch | 64 |
| Precision | bfloat16 autocast |
| Optimizer | AdamW, head lr 1e-3, backbone lr 1e-4, weight decay 0.05, grad clip 1.0 |
| Schedule | cosine with 500 warmup steps |
| Epochs | 10 |
| Loss | cross entropy, label smoothing 0.1 |
| Sampling | class-balanced: every crop is drawn with weight `1 / crops of its class in train` |
| Augmentation | brightness and contrast jitter, ±15% each, drawn anew every epoch |

A character mirrored or rotated is a different character, so neither is used. Box jitter, a
slight rotation, shear and stroke-width changes were tried and lowered accuracy on every held-out
set.

## Calibration

Temperature scaling on the `val` crops, which are the served frequency and not the balanced one the
sampler draws. The temperature minimises the negative log-likelihood on `val` with LBFGS over
`log T`; it is stored in the checkpoint and baked into the exported graph, so the served `probs` are
the calibrated ones.

## Metrics

10 epochs over 1,204,964 crops, 3 h 26 min wall-clock with validation, beside other work on the
card. The `val` curve, over the 44,716 val crops whose class is in `classes.json`:

| epoch | loss | top-1 | top-5 | macro F1 | ECE | seconds |
| --- | --- | --- | --- | --- | --- | --- |
| 0 | 1.8211 | 0.8643 | 0.9853 | 0.8329 | 0.1365 | 953 |
| 1 | 1.3323 | 0.8626 | 0.9888 | 0.8643 | 0.0940 | 936 |
| 2 | 1.2815 | 0.8536 | 0.9844 | 0.8621 | 0.0943 | 938 |
| 3 | 1.2501 | 0.8939 | 0.9874 | 0.8773 | 0.0992 | 928 |
| 4 | 1.2235 | 0.8975 | 0.9916 | 0.8916 | 0.0663 | 904 |
| 5 | 1.2018 | 0.8937 | 0.9925 | 0.8923 | 0.0772 | 879 |
| 6 | 1.1841 | 0.9037 | 0.9947 | 0.9056 | 0.0642 | 898 |
| 7 | 1.1720 | 0.9292 | 0.9966 | 0.9121 | 0.0700 | 1387 |
| 8 | 1.1650 | 0.9322 | 0.9964 | 0.9134 | 0.0722 | 1365 |
| 9 | 1.1619 | **0.9334** | 0.9962 | 0.9090 | 0.0729 | 2242 |

Calibration on the 44,782 val crops: temperature 0.8239, negative log-likelihood 0.3782 → 0.3033,
expected calibration error 0.0727 → 0.0280.

The held-out sets of `models/benchmark/` (top-1 / top-5, percent), against the ConvNeXt-tiny
trained on CODH alone that this model replaces:

| set | crops | this model | previous |
| --- | ---: | --- | --- |
| `codh-test`, all | 45,981 | **92.3 / 97.1** | 88.5 / 92.8 |
| `codh-test`, kanji | 33,981 | **93.0 / 97.2** | 87.5 / 91.4 |
| `codh-test`, hiragana | 11,314 | 93.2 / 99.5 | 94.4 / 99.6 |
| `hilab-test`, all | 37,606 | **80.9 / 91.7** | 46.7 / 59.8 |
| `hilab-test`, kanji | 31,967 | **82.9 / 91.6** | 43.9 / 55.9 |
| `hilab-test`, hiragana | 4,767 | 68.6 / 91.0 | 71.4 / 88.2 |
| `atlas-reviewed` | 168 | **89.9 / 95.2** | 83.3 / 86.3 |
| `atlas-reviewed`, corrected | 49 | **69.4 / 85.7** | 55.1 / 63.3 |

Hiragana lose 1.2 points at top-1 on `codh-test` and 2.8 on `hilab-test`; the cause has not been
measured.

```sh
export HF_HOME=$PWD/cache/huggingface           # the checkpoint is cached under cache/
uv run python models/classifier/build_manifests.py
uv run python models/classifier/build_combined.py
uv run python models/classifier/train.py --config models/classifier/config.yaml
uv run python models/classifier/export_onnx.py --parity 200
```

## Cost

Measured on the workstation (RTX 5070 Ti, 16 GB), 128×128 crops, CAFormer-S18, bfloat16, sharing
the card with the extraction worker:

| | |
| --- | --- |
| One training step, batch 64 | 0.05–0.07 s |
| Peak VRAM, batch 64 | 3.0 GiB |
| One epoch over 1,204,964 crops | 15–37 minutes, depending on what else shares the card |
| Export | 125,551,320 bytes, 3,429 classes at opset 17 |

The exported graph and the PyTorch model agree on 200 val crops to 2.2e-6 in probability, with the
same top-1 answer on every crop.

## Serving

```python
from glyph_atlas.classify import Classifier

classifier = Classifier("models/classifier/artifacts/classifier.onnx")  # 128×128, from the export
scores = classifier.scores(crop)                  # {"U+304B": 0.87, ..., "other": 0.0004}
same_kana = classifier.score_set(crop, {"U+304B", "U+304C"})
```

`scores` returns one probability per class of `classes.json`, and the values sum to 1. `score_set`
sums the probabilities of the code points asked for, which is what the alignment scores a detection
with: hentaigana forms of one kana and 旧字/新字 pairs are several code points. A code point outside
the class list contributes the abstention probability, added once however many unknown code points
the set holds.

The session runs on the CUDA provider when `onnxruntime` offers it and on the CPU provider
otherwise, and calls `onnxruntime.preload_dlls()` before a CUDA session, which this machine needs
because cuDNN comes from the `nvidia-cudnn-cu13` package — the same arrangement as `Detector` in
`src/glyph_atlas/detect.py`.

## Tests

`tests/test_classify.py` needs no trained checkpoint, no GPU and no network: it builds a tiny ONNX
classifier from random weights in `tmp_path`, in the input and output layout of the real export, and
checks that ten fixture crops give distributions summing to 1, that `score_set` over the whole class
list is 1, that an unknown code point scores the abstention probability, that a model without a
`probs` output is softmaxed, and that the preprocessing keeps the aspect ratio of a wide and of a
tall crop, and that the served size is read from the export. The model in the test is a few hundred
bytes; the real export is 126 MB.

## Licence

The code is MIT, as the repository. The base checkpoint `timm/caformer_s18.sail_in22k_ft_in1k` is
Apache-2.0. The training data is CODH's CC BY-SA 4.0 (or PDM) ground truth on public-domain and
CC BY-SA page images, and the 東京大学史料編纂所 くずし字データセット under CC BY 4.0. The weights are
intended to be released under CC BY-SA 4.0, the licence of the data they were trained on, as the
detector's weights are. Whether trained weights are a derivative work of their training data is not
settled in any jurisdiction, so the release states the intent, names the data, and carries the
attribution the export step generates. Weights go to a GitHub release, not to git.
