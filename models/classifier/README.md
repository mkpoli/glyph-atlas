# Character classifier

Calibrated probabilities over code points for one character crop, used by the alignment to
score a detection against the character the transcription holds there. `glyph_atlas.classify`
serves the exported ONNX file; nothing else reads it.

The backbone is ConvNeXt-tiny through `timm`, started from `convnext_tiny.fb_in22k_ft_in1k`
(ImageNet-22k, fine-tuned on ImageNet-1k, Apache-2.0, 28.8M parameters) with its 1000-class head
replaced by one linear layer over the class list. The training data is CODH's own boxes: one crop per
unit, cut to the box CODH drew, from the pages the detector's training data build already cached
and the tiles it materialised.

## Data

`models/classifier/build_manifests.py` writes `work/classifier/{train,val,test}.parquet`: one row per
crop, with the unit id, the crop path, the label, the code point the unit actually is, the document,
the page, the split and the production type. It reads `work/codh-full` and the split table
`data/splits/codh.tsv`, which splits by book, so a hand, a block and a scan stay on one side of
the split. A unit inside a materialised tile is cut from the tile (200 KB against several megabytes
of full page); a unit outside every tile is cut from the staged page under `work/codh-full/images/`
or from the checksum cache through `glyph_atlas.images`.

Crops are grey JPEGs under `work/classifier/crops/<split>/<bucket>/<unit id>.jpg`, cut exactly to the
unit's box: the classifier is measured on CODH boxes and serves the detector's boxes, so no margin is added
and nothing is resized at this step. The preprocessing that resizes is `glyph_atlas.classify`,
called by both the training loader and the export, so a run and the served model cannot drift apart.

| split | crops | documents | distinct code points | `other` |
| --- | --- | --- | --- | --- |
| train | 917,309 | 38 books | 4,063 | 13,159 |
| val | 44,782 | 2 books | 938 | 299 |
| test | 124,196 | 4 books | 2,268 | 4,617 |
| all | 1,086,287 | 44 books | 4,328 | 18,075 |

1,077,621 of the 1,086,287 crops were cut from a materialised tile and 8,666 from a page image.

By script, the training crops are 603,589 hiragana, 285,389 kanji, 14,927 katakana, 13,403 symbols
and one Latin character; by production, 740,002 woodblock, 162,907 of unknown production and 14,400
manuscript. The test split is 37,168 woodblock, 11,566 manuscript and 75,462 unknown.

## Classes

A class is a code point with at least 20 crops in `train`; everything below that line, and every code
point of `val` and `test` the classifier was not trained on, is `other`. `classes.json` fixes the
order: descending number of training crops, then code point, with `other` last. It is the order of
the model's output, and `glyph_atlas.classify` reads it from beside the export (or one directory
above it, which is how `artifacts/classifier.onnx` finds it).

- 1,593 identification classes plus `other`, over 4,063 code points seen in training.
- 904,150 of the 917,309 training crops carry an identification class.
- 113 classes hold 1,000 crops or more, 695 hold 100 or more, 1,593 hold 20 or more; the median code
  point in train holds 10.
- The ten largest are `U+306E` の (35,527), `U+306B` に (32,310), `U+3057` し (32,145), `U+3066` て
  (26,087), `U+306F` は (22,473), `U+304B` か (22,398), `U+3092` を (22,123), `U+3068` と (21,841),
  `U+308A` り (20,627) and `U+306A` な (20,134).

`other` is a target during training and an abstention at inference, never an identification.
`Classifier.scores` returns it like any other class; `Classifier.score_set` counts it as the mass of
every code point the classifier was not trained on, so a set of code points that all lie outside the
class list scores at the abstention probability instead of at zero.

The class list is `models/classifier/classes.json`, which the build writes and the export and the
reader take. Every code point of `train` with its crop count, above and below the line, is in
`work/classifier/stats.json`.

## Preprocessing

Grey, aspect preserved by padding to a square, 96×96.

1. The crop is converted to one channel. A crop with transparency is put on a white ground first.
2. The grey crop is pasted centred on a white square of its longer side, so a wide character stays
   wide; nothing is stretched.
3. The square is resized to 96×96 with a bilinear filter.
4. The grey value is scaled to [0, 1], normalised with mean 0.449 and standard deviation 0.226 (the
   ImageNet statistics averaged over the three channels) and replicated to the three channels the
   backbone takes.

`classify.SIZE`, `classify.MEAN` and `classify.STD` are those numbers, and `train.py` refuses to
start when `config.yaml` disagrees with them.

## Hyperparameters

Every value is in `config.yaml`.

| Setting | Value |
| --- | --- |
| Model | `convnext_tiny.fb_in22k_ft_in1k` through `timm` 1.0.29, 28.8M parameters |
| Head | one linear layer over 1,594 classes |
| Input | 96×96 grey, aspect preserved, replicated to three channels |
| Batch | 64 |
| Precision | bfloat16 autocast |
| Optimizer | AdamW, head lr 1e-3, backbone lr 1e-4, weight decay 0.05, grad clip 1.0 |
| Schedule | cosine with 500 warmup steps |
| Epochs | 12 |
| Loss | cross entropy, label smoothing 0.1 |
| Sampling | class-balanced: every crop is drawn with weight `1 / crops of its class in train` |
| Augmentation | brightness and contrast jitter, ±15% each |

A character mirrored or rotated is a different character, so neither is used, as in the detector. The sampler
draws 917,309 crops an epoch with replacement, so a class at the cut-off with 20 crops and a class
with 35,527 are drawn equally often.

## Calibration

Temperature scaling on the `val` crops, which are the served frequency and not the balanced one the
sampler draws. The temperature minimises the negative log-likelihood on `val` with LBFGS over
`log T`; it is stored in the checkpoint and baked into the exported graph, so the served `probs` are
the calibrated ones. The expected calibration error is the top-1 confidence against the top-1
accuracy over 15 equal-width bins, before and after.

## Metrics

12 epochs over 917,309 crops, 73 minutes on the card, then the temperature fitted on `val` and `test`
measured once. `models/classifier/artifacts/metrics.json` holds every number this section quotes.

The `val` curve, over the 44,483 val crops whose class is in `classes.json` (of 44,782):

| epoch | loss | top-1 | top-5 | accuracy, all crops | macro F1 | ECE |
| --- | --- | --- | --- | --- | --- | --- |
| 0 | 1.5095 | 0.8713 | 0.9871 | 0.8659 | 0.8610 | 0.0597 |
| 1 | 1.1548 | 0.8892 | 0.9850 | 0.8855 | 0.8766 | 0.0715 |
| 2 | 1.1247 | 0.9172 | 0.9918 | 0.9130 | 0.8741 | 0.0831 |
| 3 | 1.1102 | 0.8914 | 0.9880 | 0.8877 | 0.8790 | 0.0593 |
| 4 | 1.0981 | 0.9040 | 0.9916 | 0.9003 | 0.8922 | 0.0893 |
| 5 | 1.0900 | 0.9158 | 0.9886 | 0.9132 | 0.8891 | 0.0570 |
| 6 | 1.0833 | 0.9132 | 0.9935 | 0.9106 | 0.8944 | 0.0629 |
| 7 | 1.0778 | 0.9268 | 0.9936 | 0.9241 | 0.9004 | 0.0695 |
| 8 | 1.0737 | 0.9307 | 0.9902 | 0.9292 | 0.9060 | 0.0673 |
| 9 | 1.0712 | 0.9344 | 0.9893 | 0.9330 | 0.9095 | 0.0638 |
| 10 | 1.0697 | 0.9327 | 0.9882 | 0.9318 | 0.9023 | 0.0637 |
| 11 | 1.0693 | **0.9351** | 0.9882 | 0.9342 | 0.9034 | 0.0663 |

The best epoch by val top-1 is the last one, so `best.pt` is epoch 11.

Calibration, fitted on the 44,782 val crops as they come: temperature 0.8685, negative
log-likelihood 0.4422 → 0.3968, expected calibration error 0.0661 → 0.0219 over those crops and
0.0214 over the 44,483 identified ones. The model is
overconfident untempered, which is what fitting on the served frequency and not the balanced one
corrects; the temperature is stored in the checkpoint and baked into the exported graph.

`test`, at that temperature. The identified crops are the 119,579 whose class is in `classes.json`
and the other 4,617 are `other`, which is an abstention and not an identification:

| | identified crops (119,579) | all crops (124,196) |
| --- | --- | --- |
| top-1 | **0.9348** | 0.9140 |
| top-5 | 0.9887 | — |
| macro F1 | 0.8900 | — |
| expected calibration error, 15 bins | **0.0151** | 0.0354 |
| mean top-1 confidence | 0.9492 | |

By production type, over the identified crops of each:

| production | identified crops | top-1 | top-5 | macro F1 | ECE |
| --- | --- | --- | --- | --- | --- |
| manuscript | 11,554 | 0.8980 | 0.9848 | 0.8697 | 0.0294 |
| woodblock | 37,023 | 0.9139 | 0.9824 | 0.8537 | 0.0291 |
| unknown | 71,002 | 0.9517 | 0.9926 | 0.9044 | 0.0179 |

Against the target: top-1 0.9348 is above the 0.93 asked for and the calibration error 0.0151 is below
the 0.03. Top-5 0.9887 falls 0.0013 short of 0.99, which is 128 of the 119,579 crops. The curve says
the run had not finished converging — macro F1 was still rising at the last epoch and top-1 gained
0.004 in the last four — so a longer schedule is the first thing to try, and a second is the
confusions below.

Where it misses, from `test`:

- Hiragana against their katakana twin, which is the largest single confusion: は read as ハ 811
  times of 2,964, ハ as は 178 of 250, み as ミ 160 of 803 and ミ as み 110 of 193. The two scripts
  write the same sound in different shapes; 66% of the training crops are hiragana against 1.6%
  katakana, and 40 of the 1,593 classes are katakana against 73 hiragana and 1,464 kanji.
- The iteration marks against each other: ゝ read as ヽ 119 of 766, and く as 〱 176 of 1,907.
- `other` read as 〇 2,259 times of 4,617. A circle is what a classifier that has no class for a
  glyph reaches for, and 4,617 test crops are of code points below the training cut-off.
- Rare and 旧字 forms read as `other`: 總 as `other` 104 of 179, 州 as `other` 94 of 381.

```sh
export HF_HOME=$PWD/cache/huggingface           # the checkpoint is cached under cache/
uv run python models/classifier/train.py --config models/classifier/config.yaml
uv run python models/classifier/train.py --config models/classifier/config.yaml --test
uv run python models/classifier/export_onnx.py --parity 200
```

## Cost

Measured on the workstation (RTX 5070 Ti, 16 GB, driver 580.97), 96×96 crops, ConvNeXt-tiny,
bfloat16:

| | |
| --- | --- |
| One training step, batch 64 | 0.034 s |
| Peak VRAM, batch 64 | 1.21 GiB |
| Training crops per second | ~1,880 |
| One epoch over 917,309 crops | 6–7 minutes |
| Export | 116,269,614 bytes, 1,594 classes at opset 17 |
| Training, 12 epochs | 73 minutes |

`python models/classifier/train.py --profile` reproduces the step time and the peak.

## Framework spike

The classifier needed its own spike line, because its framework is not the detector's. What
was checked on this machine on 2026-09-11:

- `timm` 1.0.29 installs from PyPI as a wheel under Python 3.14.0; there is no source build, which is
  where the detector's mmdetection attempt failed.
- `train.py`, `train.py --test` and `export_onnx.py` all refuse to run when the classification head
  and `classes.json` disagree on the number of classes. The export reads the width back out of the
  `probs` output of the file it just wrote and prints it: 1,594 against 1,594 names. A width of
  1,329 against a 1,594-name list would name every probability wrongly while the numbers looked
  ordinary, and `Classifier` would be the first thing to notice.
- `timm.create_model("convnext_tiny.fb_in22k_ft_in1k", pretrained=True, num_classes=1594)` returns a
  28.8M-parameter ConvNeXt-tiny with a fresh `head.fc` over the class list. The checkpoint comes from
  the Hugging Face hub (`timm/convnext_tiny.fb_in22k_ft_in1k`, Apache-2.0) and lands under
  `cache/huggingface` with `HF_HOME` set, as the conventions ask.
- One training step at batch 64 on 96×96 bf16 takes 0.035 s and peaks at 1.21 GiB, so the
  16 GB budget is not the binding constraint and the run leaves the card to other work.
- The legacy ONNX exporter writes one self-contained file (116,269,614 bytes at 1,594 classes, opset
  17) with the inputs and outputs `classify.Classifier` reads, and `onnx.checker` and `onnxruntime`
  accept it.
- On 200 test crops the exported graph and the PyTorch model agree to 4.1e-6 in probability with the
  same top-1 answer on every crop. With cuDNN's TF32 convolutions left on, the same comparison shows
  2.4e-4, which is the reduction order of the reference and not a property of the export; the parity
  check turns TF32 off and says so in the code.

At 96×96 the last ConvNeXt stage is 3×3, so the model is far from the 224×224 it was pretrained at.
That is what the preprocessing fixes, and the metrics below are measured at it.

## HI Lab crops

`train_with_hilab.py` is a separate experiment on the 東京大学史料編纂所 くずし字データセット,
whose crops are individual characters with no page placement and no book to split by. It writes its
own manifests, `work/classifier/hilab-{train,val}.parquet` (a 90/10 holdout by numeric id), fine-tunes
the baseline on the CODH training crops plus the HI Lab ones, and measures the CODH test split and
the HI Lab holdout. Its classes are the baseline's, so a HI Lab crop of a code point the baseline was
not trained on is an `other` crop, exactly as it would be at inference. The baseline's manifests and
`classes.json` are never touched by it.

Not run: the HI Lab archive is imported as a listing only, the crops are not extracted, and this machine
has no `cache/hilab/all/characters/`. The script says so and exits rather than measuring nothing.
Extracting them (`atlas import hilab --download`, 4.0 GB over range requests) is what the experiment
waits for.

## Serving

```python
from glyph_atlas.classify import Classifier

classifier = Classifier("models/classifier/artifacts/classifier.onnx")
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
tall crop. The model in the test is a few hundred bytes; the real export is 115 MB.

## Licence

The code is MIT, as the repository. The base checkpoint `timm/convnext_tiny.fb_in22k_ft_in1k` is
Apache-2.0, and the training data is CODH's CC BY-SA 4.0 (or PDM) ground truth on public-domain and
CC BY-SA page images. The weights are intended to be released under CC BY-SA 4.0, the licence of the
data they were trained on, as the detector's weights are. Whether trained weights are a derivative work of
their training data is not settled in any jurisdiction, so the release states the intent, names the
data, and carries the attribution the export step generates. Weights go to a GitHub release, not to git.
