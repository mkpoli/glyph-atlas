# Withheld release: crops whose kept box shows their own character

`atlas review withheld-release` (`src/glyph_atlas/review/withheld_release.py`) lifts the alignment
repair's withheld flag from a crop when the box it kept shows the character it is filed under. This page
records what it was measured against on 2026-10-16, on `work/ainu-characters-20260929-shift`.

## The withheld crops

38,674 published crops carried `quiz: false` (37,153 `hk:`, 1,521 `ar:`), and Browse and the character
pages listed them under labels the repair had not vouched for. By the reason the note gives:

| reason | published |
| --- | ---: |
| the ink at the corrected position scores 8.5 nats or more worse than another crop of the line | 17,761 |
| the ink at the corrected position scores less than 8.5 nats worse | 15,491 |
| two characters would share a box, and the classifier does not separate the two crops | 3,196 |
| the block's text has no character for the box (`ar:`, a stroke, a dot or part of a neighbour) | 1,272 |
| two characters would share a box, and the classifier backs one | 358 |
| `ar:` ink that ainu-records reads as two different characters | 171 |
| a line phase shift, a crop issue, a stack of two characters, or a person's report on the line | about 425 |

Most `hk:` reasons are about the *corrected* position: the repair scored the ink that the corrected
reading order gives the character, found it worse than another crop of the line, and declined to move
the character there. The unit kept its old box, and the repair never scored that box.

## The rule

The served classifier (`models/classifier/artifacts/best.pt`) reads every crop of each line or block
holding a withheld crop. For each crop it keeps the probability that the crop shows its own label and
the labels written one and two places either side of it. A withheld crop is released when it shows its
own label with probability 0.9 or more and with no less than any neighbour's, and both sides of its box
are 10 pixels or more. Released, its note says the pass stands behind the box (`reliable`, `quiz`) and
keeps the note it replaced under `before`; `--undo` puts that note back.

Never released: a crop a person reviewed (the store's events and the ids the site holds reviews for), a
crop whose review state is neither `machine` nor `rejected`, a note that records a person's report, a
crop issue or a merged box, and a note whose status is not `uncertain` (the `ar:` crops ainu-records
contests, stacks of two characters).

## Measured

On the dataset, with the site's reviews of 2026-10-16 (1,394 events, 505 `ar:`/`hk:` crops protected):

| | units |
| --- | ---: |
| withheld and read | 36,428 |
| released | 21,938 |
| kept withheld: the crop does not show its own label with 0.9 | 14,490 |
| skipped: a report, crop issue or contest | 1,484 |
| skipped: reviewed | 223 |
| skipped: a side under 10 pixels | 25 |

All 21,938 are published `hk:` crops; 16,736 published crops stay withheld.

**Against the site's verdicts.** 44 released crops have a verdict on the site. 43 agree with their
label: 42 confirmed, and one corrected to せ, which is the crop's label (its text reads セ). The other
was reported as two merged characters, which the rule now leaves to a person.

**By eye.** Of 48 releases drawn at random among unreviewed crops, 46 plainly show their label, 1
(a cursive 主) is uncertain and none is wrong. A strokes-only draw of 48 (ノ, 一, ヘ) showed whole
written characters, no fragments.

## What is not covered

- The 16,736 crops still withheld. Their crop shows its own label with less than 0.9, or a neighbour's
  label better. A neighbour's label is not adopted: of 26 such relabels drawn at random, about 17
  showed the neighbour's character, and the rest were fragments or other characters (ヲ→ノ, い→と).
- The 1,272 `ar:` boxes with no character in their text. They hold strokes, dots and parts of
  neighbours, and need a state that leaves them out of the character galleries.
- A crop's label being right does not settle its line: the corrected order the repair proposed for the
  other characters of the line is not applied.
