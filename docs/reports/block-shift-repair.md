# Block-shift repair: Ainu record crops filed under a neighbour's character

`atlas review shift-repair` (`src/glyph_atlas/review/shift_repair.py`) relabels a crop when its block
of the Ainu records is one or two places out of step with its text. This page records what it was
measured against on 2026-09-29.

## The defect

The Ainu records label a block by cutting its OCR or transcription text into as many ink boxes as it
has characters. One bad cut moves every later label, so a crop showing 金 is filed under 表, the
character before it. Reviewers corrected 57 crops of such blocks on the site by 2026-09-29, most of
them to a character one or two places away in the same block.

## The rule

The served classifier reads each crop, and for each offset −2…+2 the probability that the crop shows
the label written at that offset is kept. A Viterbi pass over each block picks one offset per crop and
pays 4 nats for each change of offset. A crop is relabelled with the character at its offset when the
offset is not zero, the crop shows that character with probability 0.8 or more, at least two crops of
its run show theirs with 0.4 or more, and both sides of its box are 10 pixels or more. Crops a person
reviewed on the site or in the dataset are left alone.

## Measured

Against the site's reviews (577 events, 7,407 crops passed in Quick review rounds), over the 109,768
crops of `work/ainu-characters-20260927-repair`:

| reviewed crops the rule relabels | count |
| --- | ---: |
| takes the reviewer's correction, or its 新字・旧字 or katakana form | 40 |
| names another character (古 → 金, reviewer 令) | 1 |
| reviewer confirmed the label as it stood | 2 |
| passed in a Quick review round | 5 |

It finds 40 of the 57 corrections. Several crops passed in rounds show the proposed character (葉 → 言,
高 → 慢, 自 → 由, 者 → な), so a passed crop is weak evidence that its label is right.

On unreviewed crops the rule proposes 10,052 relabels (10,029 `ar:`, 23 `hk:`; offsets −1: 5,414,
+1: 4,079, −2: 338, +2: 221). Of 40 drawn at random, 28 show the proposed character plainly, about 9 are
too small or damaged to tell and about 3 show another character. The label before was wrong on nearly
all 40.

## What is not covered

- A character the transcription itself misreads (令 read as 金) is copied from the text as it stands.
- Crops of a block whose text has more or fewer characters than it has boxes at the shifted place, and
  single misplaced crops, stay with their labels: an offset needs two crops of its run to carry it.
- Classifier confidence alone was tried first: with no block context, only about half of the crops it
  confidently reads as another character took that character on review.
