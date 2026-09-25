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

Calli-Tongji is CC BY-NC 4.0, for research and education only. The checkpoint and anything it
predicts derive from it and stay out of this project's CC BY-SA releases. A style a reviewer
confirms after seeing a suggestion is the reviewer's judgement.

## Result

One run (seed 0, 20 epochs, 93 s on an RTX 5070 Ti), tested on 11 calligraphers the model never
saw (1,100 images; train 3,400 images of 34 calligraphers, val 500 of 5):

| | seal | clerical | regular | running | cursive |
| --- | --- | --- | --- | --- | --- |
| recall | 0.97 | 0.88 | 0.88 | 0.56 | 0.71 |

Accuracy 0.756, macro F1 0.799. Running script is the weak class: of its 300 test images, 60 go to
regular and 71 to cursive, the two scripts it lies between. By held-out calligrapher, accuracy runs
from 0.34 (皇象-草, whose 章草 keeps clerical features) to 0.98 (张旭-草); the running-script hands
score 0.48 (谭延闿), 0.54 (欧阳询) and 0.66 (王羲之).

The test holds one calligrapher each for seal (李阳冰) and clerical (吴让之), so those two recalls
say how the model does on one hand, not on the script.

## Limits

- The training images are binarised brush calligraphy. Crops from this project's scans are
  binarised the same way first, but paper, ink bleed and printing still differ from the training
  data; the teacher has not been measured on them.
- It knows only Chinese characters in the five brush scripts. Kana, Hangul and the print styles
  `ming` and `gothic` are outside it, and it gives one of its five classes for them all the same.
- The open subset has 50 calligrapher-style classes. The full dataset (317,574 images) is released
  on a named application to its team.
