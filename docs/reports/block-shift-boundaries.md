# Block-shift boundaries: two crops claiming one character

`atlas review shift-repair --boundaries` (`src/glyph_atlas/review/shift_boundary.py`) settles the
edges of the runs that `atlas review shift-repair` moved. This page records what it was measured
against on 2026-10-04. Nothing was applied.

## The defect

The block-shift repair relabels a run of Ainu record crops whose block text is cut one or two boxes
out of step. Where the run starts or ends, the crop just outside it keeps its old label, which is now
the label the run's first or last crop took, so two neighbouring crops claim the same character of
the text. In `ar:` blocks, neighbouring crops with the same label were 0.08% of pairs before the
repair (40 of 53,074 pairs in `work/ainu-characters-20260927-repair`) and 6.37% after it (3,383
pairs in `work/ainu-characters-20260929-shift`). Of those 3,383, 3,312 are two crops claiming one
place of the text and 71 a character the text itself writes twice.

A shift starts where one box is an extra cut (a stroke of a neighbouring character, a dot, half of
a character cut in two) or where a character got no box. The crop beside the run is then either that
extra box, or a character that belongs one place further along the run, with the extra box a little
further on.

## The rule

For each pair claiming one place, the window runs out to the nearest crop on each side that shows
its claimed character with probability 0.5 or more, that a person settled, or to the block's end.
The crops inside are aligned to the characters between those two crops, in order: each takes the
next character, is an extra box, or a character is left without a box. A crop pays −log of the
probability that it shows the character it takes (at least 0.01, and 0.05 for a character the
classifier has no class for); an extra box pays −log of a prior that grows as the box shrinks against
the block's median area (0.02 at full size, 0.5 below a fifth of it); a character left without a box
pays −log 0.02. The cheapest alignment is taken when the next one costs at least 1 nat more. Where no
other alignment is possible, it is taken only if every box it withholds is under 0.6 of the median
area. A crop the classifier did not read anchors its window.

A crop that takes another character is relabelled as the block-shift repair does. An extra box is
kept with its label and withheld: its `alignment_repair` note is `withheld`, `quiz: false`, with the
reason "the block's text has no character for this box: it holds a stroke, a dot or part of a
neighbouring character". The site already shows such a crop as withheld with that reason and keeps
it out of Quick review. Both are model events in the dataset's journal with the evidence that chose
them. Crops with a review on the site (`work/shift-leftovers/protect.txt`, 213 ids), with a review
event in the dataset, with any review state but `machine`, or whose label two readings of the ink
confirmed (`alignment_repair` `confirmed` and reliable) are never changed. A box another pass already
withheld keeps that pass's note. On a rerun, a box this pass withheld keeps its place in the text and
is otherwise passed over, until a person reviews it.

## Measured

Dry run over `work/ainu-characters-20260929-shift` (109,768 crops in 9,015 blocks, 27,409
protected):

| windows | count |
| --- | ---: |
| settled | 2,413 |
| left unsure (the next alignment within 1 nat, or a full-size box withheld by force) | 751 |
| a protected crop in the pair | 102 |
| both crops show the character | 57 |
| too wide, or blocked by a label the text does not hold | 25 |

The settled windows relabel 2,131 crops and withhold 1,272 as extra boxes (940 of them under 0.35 of
the block's median area; 3 more were already withheld by another pass). Only 113 of the relabels have
classifier probability 0.3 or more for the new character; the rest follow from the alignment.
Neighbouring `ar:` crops with the same label fall from 3,383 to 960 pairs (1.85%), counting across a
withheld box.

Against the site's reviews (`work/site-reviews-20261004T063038Z`), with the protection lifted so
that the reviewed crops are judged too:

| reviewed `ar:` crops the rule changes | count |
| --- | ---: |
| relabelled to the reviewer's correction (or its katakana or 旧字 form) | 33 |
| relabelled where the reviewer reported a bad crop or a wrong reading | 14 |
| relabelled to another character than the reviewer's | 0 |
| withheld where the reviewer reported a bad crop | 14 |
| withheld where the reviewer confirmed the label (one after redrawing the box) | 2 |

Of the 49 `ar:` wrong-character reports, 22 take the reviewer's character and none another; 29 sit
next to an identical label, and 20 of those take the reviewer's character. The rest are in windows
it leaves unsure (8) or cannot fit (3), or are no part of a doubled pair (15).

Of 40 decisions drawn at random among unreviewed crops (28 relabels, 12 withheld boxes), all 40 are
right by eye on the page, 5 of them only at a larger scale: two halves of one character (請, 故),
ク against り, よ and へ in cursive. None is plainly wrong.

## What is not covered

- The 751 unsure windows stay as they are, and so does a pair in which both crops show the character.
- A crop is moved at most along its own block's text; a character the text misreads stays misread.
- A crop that is half of a character is withheld; the other half keeps the character's label and its
  partial box until a person redraws it.
