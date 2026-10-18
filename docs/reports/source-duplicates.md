# Source duplicates: one written character cut by both `ar:` and `hk:`

`atlas review source-duplicates` (`src/glyph_atlas/review/source_duplicates.py`) withholds the worse of
two shown crops that cut one written character twice, once from the ainu-records transcription (`ar:`)
and once by detect-align (`hk:`), under one label. Measured on 2026-10-17 on
`work/ainu-characters-20260929-shift`, with the site's reviews of that day.

## How many

A pair is the same ink when the boxes share half the smaller box's area or more. `hk:` crops on `ar:` ink,
by the alignment repair's status of the `hk:` crop:

| `hk:` status | shown | same label | other label |
| --- | ---: | ---: | ---: |
| released (withheld release) | yes | 877 | 429 |
| confirmed | yes | 25 | 405 |
| none | yes | 90 | 19 |
| applied | yes | 30 | 16 |
| uncertain | no | 1,335 | 3,026 |

The withheld release made most of the shown duplicates: before it, about 145 shown `hk:` crops repeated
an `ar:` crop with the same label.

Pairs with another label are mostly one box holding two characters and the other holding one of them
(an `ar:` box over トテ, a `hk:` box over アン). They are a box defect of one side, and this pass leaves them.

## The rule

Only pairs whose two crops are both shown, both still `machine`, and unreviewed on the site count:
844 pairs, 7 more skipped as reviewed. Neither source cuts the better box by rule. Of 40 random pairs, the
`ar:` box was the worse cut in about 8 (it took in a neighbour, 八 with 九, 一 with 日, or held a
fragment), the `hk:` box in a few. The served classifier reads each crop against the label, and the
crop it reads less surely is withheld; a tie or two unread crops withhold the `hk:` copy.

| withheld | pairs |
| --- | ---: |
| the `ar:` copy | 738 |
| the `hk:` copy | 106 |

## By eye

Of 40 random pairs whose `ar:` copy is withheld, the kept `hk:` crop was as good or tighter in every one.
Of 40 whose `hk:` copy is withheld, most were near ties at 0.97–0.99; the rest were `hk:` boxes taking in
a neighbour (ウ with ン, a tall き, 分 in a run of characters).

## Not covered

- The 869 shown pairs with different labels, and the 75 shown `hk:` and 237 `ar:` boxes that hold two or
  more boxes of the other source. They need a crop repair.
- Many clean `ar:` crops read near 0 for their own label while the `hk:` crop of the same ink reads 0.98.
  Where that decides a pair, the shown copy is still a good crop of the character.
