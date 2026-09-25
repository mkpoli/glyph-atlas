# Script-style teacher

A five-way classifier of script style (`seal`, `clerical`, `regular`, `running`, `cursive` in
`data/vocab/style.yaml`), trained on the open subset of Calli-Tongji
(`data/sources/calli-tongji.yaml`). It proposes a style for a crop; a person confirms it before it
enters the data.

    HF_HOME=cache/huggingface python models/style/train.py --zip cache/calli-tongji/Calli-Tongji.zip

The archive comes from ModelScope and needs a login there. `train.py` describes the split, the
model and the augmentation; `glyph_atlas.style_teacher` holds the preparation shared with its use
on crops. The checkpoint is written to `models/style/artifacts/best.pt` and not committed;
`metrics.json` is the report of the run below.

## Licence

Calli-Tongji is CC BY-NC 4.0, for research and education only. The checkpoint stays out of the
repository and out of this project's CC BY-SA releases. Whether a model's predictions are adaptations
of its training data under that licence is uncertain; until that is settled, the teacher's
suggestions are kept apart from the released data, and a style enters the data only when a person
confirms it.

## Result

One run (seed 0, 20 epochs, 116 s on an RTX 5070 Ti). Every calligrapher is in one split only:
train 3,200 images by 30 calligraphers, val 600 by 5, test 1,200 by 11.

| | seal | clerical | regular | running | cursive |
| --- | --- | --- | --- | --- | --- |
| recall | 0.84 | 0.87 | 0.86 | 0.74 | 0.48 |

Accuracy 0.735, macro F1 0.758. Cursive is the weak class: 149 of its 300 test images are called
running. By held-out calligrapher and script, accuracy runs from 0.43 (徐渭-草, 57 of 100 called
running) to 0.94 (欧阳询-楷); the three cursive hands score 0.43 (徐渭), 0.48 (赵构) and 0.53
(孙过庭). `metrics.json` lists every held-out hand with what the model called its images.

The test has one calligrapher for seal (赵之谦) and one for clerical (伊秉绶), so those recalls say
how the model does on one hand, not on the script. The checkpoint is chosen on a val set of one or
two hands per script, and the figures come from a single seed and a single split.

## On this project's crops

`suggest.py` runs the teacher over a deterministic sample of one corpus's Han crops and writes
`work/style-suggestions/{corpus}/`: `suggestions.jsonl` and a contact sheet. Nothing is written to
the dataset tables. Two runs on 2026-09-26, with no crop reviewed yet:

HNG (1,473 crops, share 0.03), by HNG's own source category:

| category | crops | regular | running | cursive |
| --- | --- | --- | --- | --- |
| 石経 | 90 | 98% | 1% | 0% |
| 版本・刊本 | 499 | 92% | 4% | 2% |
| 写本 | 884 | 73% | 25% | 2% |

The documents with the largest share of running and cursive calls are autographs and Japanese
manuscripts: 明恵自筆華厳信種義 86% of 22 crops, 図書寮本日本書紀 67% of 42, 兼方本日本書紀 56%
of 32, 親鸞自筆教行信証 50% of 20. S2067 華厳経 (513) is at 53% of 19, which its hand does not
explain.

CODH (999 crops, share 0.003): regular 19%, running 46%, cursive 35%, much the same for printed and
handwritten books.

Looking through the sheets, not a measurement: the most confident regular calls on both corpora
and the most confident cursive calls on CODH look right. The running calls on HNG include many
bold or heavily inked regular characters (大, 王, 周, 国) at 0.93 or more, and the cursive calls on
HNG are mostly damaged or noisy scans with low probability. The per-document shares track the hands
better than any single call. Measuring precision needs a reviewed sample.

## Limits

- The training images are binarised brush calligraphy. Crops from this project's scans are
  binarised the same way first, but paper, ink bleed and printing still differ from the training
  data; the teacher has not been measured on them.
- It knows only Chinese characters in the five brush scripts. Kana, Hangul and the print styles
  `ming` and `gothic` are outside it, and it gives one of its five classes for them all the same.
- The open subset has 50 calligrapher-style classes. The full dataset (317,574 images) is released
  on a named application to its team.
