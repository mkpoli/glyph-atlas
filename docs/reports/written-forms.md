# Written forms: crops that look written as a variant of their label

`atlas review written-forms` (`src/glyph_atlas/review/written_forms.py`) ranks the crops filed under
one character by how far they lie towards the crops of a variant of it, in the similar-crop index.
This page records what the ranking showed on 2026-11-23. Nothing was relabelled or loaded to the
site.

## Inputs

- The similar-crop index `work/similar/0a82edb62d862c2b` (classifier-penultimate-v1, built
  2026-09-27, 503,013 crops), with each crop's character and book from the local copy of the site's
  database (`work/atlas-v3/out/atlas.sqlite`, read 2026-11-23).
- The variant tables behind `refs.variants`: 111 pairs stated by two tables or more have 15 crops
  under each character. 34,421 of the 55,760 crops under those characters were scored, 36,060
  scores in all, since a character may stand in two pairs; the rest have too few crops in other
  books. 498,667 of the index's 503,013 crops are held by the site under a character and take part.

## The rule

For a pair, the mean vector of each character's crops, taken from every book but the crop's own,
gives a direction from the crop's label to the variant. The score is the crop's place along it,
zero at the midpoint of the two means. HI Lab is one book to the site, so its crops are scored
with the means of the other sources alone.

## By eye

No set of crops with a recorded written form was at hand to score the ranking against, so it was
read on contact sheets of HI Lab crops (くずし字データセット, 東京大学史料編纂所, CC BY 4.0): the 18
highest and 18 lowest scores under a label.

| label → variant | HI Lab crops scored | highest scores | lowest scores |
| --- | ---: | --- | --- |
| 国 → 國 | 768 | all written 國 (或 inside the frame) | cursive 国 |
| 島 → 嶋 | 110 | all written 嶋 (山 beside 鳥) | 嶋 again, then five written 島 |
| 余 → 餘 | 98 | all written 餘 (食 on the left) | 余 |
| 与 → 與 | 282 | the first 6 written 與, then cursive 与 | 与 |
| 数 → 數 | 248 | several written 數 among cursive | cursive 数 |
| 時 → 时 | 341 | abbreviated and cursive 時 mixed with full 時; no clear 时 | full 時 |
| 両 → 兩 | 332 | one cursive shape throughout; 両 and 兩 are not told apart | 両 in a squarer hand |
| 万 → 萬 | 182 | 万, none written 萬; every score is below zero | 万 |

Where a label holds crops plainly built as the variant, they come first. Where the two forms are
one cursive shape (両, 時), the ranking follows how fully the character is written out. Under 島
nearly every HI Lab crop is written 嶋, so the lowest scores are the few written 島.

## Books

A book's mean score for a label is kept. In the 古活字 print (`codh-omt:`) the crops of 万 average
0.44 towards 萬, against −0.20 and −0.56 in two manuscript sources: CODH files a 旧字 under its
新字, and the print sets 萬.

## What the ranking is not

- **A verdict.** The two means come from the labels the pass doubts. Under 国, where many crops are
  written 國, the means sit close and the scores are small (highest 0.16); zero separates nothing,
  and for the character with few crops the sign of a score means little.
- **A measure.** Against the labels a logistic fit on the same vectors scores an AUC of 0.52 for
  国 and 國 on held-out books, with the crops it ranks first plainly written 國: the labels are what
  is wrong, so they cannot grade it.
- **Written forms only.** The tables also pair look-alikes (玉 and 王, 陣 and 陳, 土 and 士); a high
  score there is a crop of the other character. The report lists each pair's tables.
- **HNG as a test.** HNG files each 字体 of a character on its own row (統合ID `01639`, `01639b`).
  Scored the same way across its sources, 158 characters with two rows give a median AUC of 0.51,
  several near 0. On sight the second row is not one shape across sources (of the five 國 crops on
  row `01639b`, one is 囯 and four are 國), so the rows as imported do not serve as a test of form.

## han2ids as a closed choice

han2ids (v0.5) is trained on rendered Mincho and Song faces only and does not read handwriting:
decoding freely, 0 of 383 HI Lab crops and 2 of 148 HNG crops gave the label's description
(2026-11-20). What follows is a narrower test, which of two given descriptions a crop fits, and
it does not change that. The
choice is the log-likelihood of the variant's description less the label's, each less its
likelihood on a blank image, on the crop fitted to its ink with an 8% margin.

On the crops read by eye above (the highest-scored 18 under a label, written as the variant,
against the lowest, written as the label):

| pair | descriptions | written as the variant, prefers it | written as the label, prefers the variant | AUC |
| --- | --- | ---: | ---: | ---: |
| 国 / 國 | ⿴囗玉 / ⿴囗或 | 18 of 18 | 4 of 18 | 0.98 |
| 島 / 嶋 | ⿹{47}山 / ⿰山鳥 | 11 of 18 | 0 of 5 | 0.98 |
| 余 / 餘 | ⿱𠆢⿱一朩 / ⿰飠余 | 17 of 18 | 0 of 18 | 0.99 |
| 与 / 與 | ⿹{22}一 / ⿳⿴𦥑{23}一八 | 6 of 6 | 5 of 18 | 0.96 |

These crops are the extremes of the ranking above: the ones most plainly and squarely written,
which is where a model trained on type has the best chance. The figures say the two agree on such
crops and nothing about ordinary handwriting or the middle of a ranking.

Over every HI Lab crop of six pairs (1,969 crops), read on sheets of its highest and lowest 18:

- **国 → 國** (768 crops): the highest are written 國, and so are the highest among crops the
  feature ranking put in its lower half. It finds 國 the first ranking missed. Rank correlation
  with the feature score 0.34.
- **余 → 餘** (98): the highest are written 餘; rank correlation 0.78.
- **万 → 萬** (182): it prefers 萬 for 167 crops, and none is written 萬. Its zero is no threshold
  either.
- **時 → 时, 両 → 兩, 数 → 數**: no reading by eye settles what its order follows.

Where the two forms are built differently (a frame around 或 against 玉, 食 on the left) and the
crop is written squarely, the choice ranks them; its sign cannot be trusted, and on 万 it is plainly
wrong. It is no reader of handwritten crops and is not part of the command. Using it here waits on
han2ids being trained on real crops.
