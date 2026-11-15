# Cutter

`glyph_atlas.cut` cuts a transcribed line into its characters. The transcription gives the
characters and their count; the detector's boxes, parts of them and the ink between them are the
places a character may take; the classifier and the box refiner score each place. Two small models
here decide the rest: `frame.json` scores how well a candidate box frames a character, and
`gate.json` accepts a placement for publication.

Both are logistic regressions over features the alignment computes, readable as JSON. The
character of an accepted crop is the transcriber's, so a form the classifier does not know is
accepted on how its box sits in its line.

## Fitting

```sh
python models/benchmark/cuts.py build                 # work/cut-bench: CODH's val and test books
python scripts/fetch_reviews.py work/reviews/reviews.json
python models/benchmark/cuts.py build-reviewed work/reviews/reviews.json   # the collection's reviewed pages
python models/cutter/fit.py collect --split val --pages 60
python models/cutter/fit.py collect --split fit
python models/cutter/fit.py fit                       # frame.json, gate.json
python models/cutter/fit.py collect --split test --pages 40
python models/cutter/fit.py measure --split test      # measured-test.json
python models/cutter/fit.py collect --split held
python models/cutter/fit.py measure --split held      # measured-held.json
python models/benchmark/cuts.py run --cutter lattice --split test --pages 40
```

The models are fitted on CODH's two `val` books (115 pages, 16,754 characters) and on the crops of
the collection that a reviewer matched to their character on the site, and measured on CODH's four
`test` books, none of which the detector, the classifier or the refiner was trained on. A
placement is right when its box reaches IoU 0.7 with the box of its own character. `gate.json`
accepts at the probability that gave 0.985 precision on `val`.

## Reviewed crops

A crop a reviewer matched to its character was cut right, and one reported as two characters, part
of one or none was cut wrong. The site's export of 2026-11-15 judges 819 crops of the extraction
this way, 816 right and 3 wrong, on 664 pages of 232 books. Such a page knows the boxes of those
characters and nothing of the others on it, so a candidate box is labelled only where a reviewed
box decides it, and a placement only for a reviewed character.

One book in four is held out by its id: 547 crops of 167 books are fitted on, 269 crops of 65
books measure the result (`measured-held.json`). The rows of the reviewed pages together weigh a
tenth of CODH's (`fit --reviewed 0.1`); at three tenths 97.4% of the boxes published on CODH's test
books reached IoU 0.7.

| | CODH alone | with reviewed crops |
| --- | ---: | ---: |
| CODH test, characters published at IoU ≥ 0.7 | 60.7% | 63.2% |
| CODH test, published boxes at IoU ≥ 0.7 | 97.7% | 97.5% |
| CODH test, characters published where 98% of boxes reach IoU 0.7 | 55.7% | 57.1% |
| held-out reviewed crops published | 246 of 269 | 249 of 269 |

Every reviewed crop is one an earlier gate had published, so the held-out crops are easier than
the collection's pages, and three reported cuts are too few to teach what a wrong one looks like.

## Held-out books

160 pages of the four `test` books, 44,194 characters, the whole pipeline run page by page
(`models/benchmark/results/cuts-*-test.json`). `consensus` is the gate this cutter replaces: the
alignment of whole detector boxes, published when the classifier and NDLkotenOCR both read the
character in the crop.

| | consensus | lattice |
| --- | ---: | ---: |
| characters published, box at IoU ≥ 0.5 | 37.1% | 64.0% |
| published boxes at IoU ≥ 0.5 | 99.7% | 99.8% |
| published boxes at IoU ≥ 0.7 | 97.3% | 97.5% |
| seconds a page | 27.6 | 1.8 |

By book, share of characters published at IoU ≥ 0.5:

| book | production | consensus | lattice |
| --- | --- | ---: | ---: |
| うすゆき物語 | printed | 35.7% | 59.6% |
| 料理物語 | printed | 33.4% | 60.0% |
| 源氏物語 | handwritten | 32.4% | 53.6% |
| 物類称呼 | printed | 42.8% | 76.1% |

Other operating points on the same pages (`measured-test.json`), share of characters published
against the share of published boxes at IoU ≥ 0.7:

| precision at IoU 0.7 | 0.95 | 0.97 | 0.98 | 0.99 |
| --- | ---: | ---: | ---: | ---: |
| characters published | 76.4% | 66.7% | 57.1% | 36.6% |

With one line in four given a fault (a character or two dropped from the transcription, added to
it or exchanged) and one character in twenty unreadable to the classifier, 60.0% of the characters
on the page are published with a box at IoU ≥ 0.7, 97.5% of the published boxes. Of the 2,247
unreadable characters 627 are published, 98.1% of them at IoU ≥ 0.7.

A transcriber's line box is longer than its ink. With every line box lengthened at both ends by
three quarters of a character (`cuts.py run --slack 0.75`), 62.9% are published at 99.8% (IoU 0.5).

## Collection pages

40 pages drawn at random from the pages the collection had extracted, 9,515 transcribed characters:
the consensus gate had published 2,831 crops, the gate fitted on CODH alone accepts 4,961, and 2,429 of them overlap
no published crop. Of 120 of those new crops drawn at random and looked at one by one, three were
doubtful (a narrow box that may hold part of its character, a box that reaches into its neighbour).

## Pages without a transcription

`glyph_atlas.segment` cuts the columns of a page that has no transcription with `frame.json` alone:
a box becomes its parts when the parts are likelier characters than the whole, read first by the
size of its column's characters and then by its own shape. It is not wired into
`atlas review propose-characters`, whose `--split` runs the geometric pass of
`character_proposals.split_boxes`.

`models/benchmark/counts.py` scores both on 250 detector boxes of the Kyujanggak land registers of
1722 and 1904 (서울대학교 규장각한국학연구원) whose characters were counted by eye, once, by the author
of the pass and not by a reviewer. A box is right when the cutter makes as many boxes of it as it
holds characters, and a box joined with another is part of a box for each. Of 100 detector boxes
that hold whole characters on these pages:

| | detector alone | `split_boxes` | `segment` |
| --- | ---: | ---: | ---: |
| right | 73.2 | 76.1 | 84.2 |
| one character, cut | 0.0 | 1.7 | 1.1 |
| one character, lost or joined | 0.0 | 1.2 | 0.6 |
| several characters, wrong count | 26.8 | 21.1 | 14.2 |

On the four pages drawn after `segment` was settled (113 boxes) it is right about 86.1 in 100,
`split_boxes` about 75.9.

What is left is boxes of several characters that stay whole. Three things were tried against the
same boxes and are not in the code:

- Cutting every box left whole into strips down and across it, each read without the size of the
  writing around it. On the first sample it got 9 more of the 40 boxes of several characters right
  than `segment` and cut 15 more of the 97 single characters: the halves of 宮, 畓 or 昌 are
  characters to the classifier.
- The same strips kept only for boxes the classifier cannot read whole. Tuned on the first sample
  it was right about 3.1 more boxes in 100 than `segment`; on the second 0.9 more, three more
  boxes of several characters and two more single ones cut.
- The size of a box against its neighbours as a sign of how many characters it holds. The registers
  mix sizes within a column: the single characters of the sample run from 0.5 to 2.7 times the
  size of the characters around them, the uncut boxes of several from 0.8 to 4.9.

The detector also draws 3 boxes in 100 inside a larger one, which `segment` leaves out. Of 16
looked at, 12 were a fragment of the larger box's character.

The cover title, a box of two characters eight times the page's character size, is cut by
`split_boxes` and moved onto one character by `segment`. A page takes about 45 s on a CPU.

## Limits

- The full boxes of a page come from two printed books. Manuscripts, Chinese prints and Korean
  books enter the fit only through reviewed crops, and are measured through the 源氏物語 pages, the
  held-out reviewed crops and the collection sample above.
- CODH draws a box loosely around some characters and tightly around others, so part of the
  shortfall at IoU 0.7 is a difference of convention. Of 48 accepted boxes under 0.7 looked at,
  about half framed their character as well as CODH's box.
- A character the detector missed is found only between two detections of its column or between a
  detection and the end of the line box, in a line of one column.

## Sources

- Peng, Jin, et al., "PageNet: Towards End-to-End Weakly Supervised Page-Level Handwritten Chinese
  Text Recognition", IJCV 2022, https://arxiv.org/abs/2207.14807. Character boxes learned from line
  transcriptions; read through an extraction of the paper.
- Wang et al., "Weakly Supervised Learning for Over-Segmentation Based Handwritten Chinese Text
  Recognition", ICFHR 2020. Candidate segments joined along a path through a lattice; known from a
  search summary only, the paper was not read.
- Baena, Kalleli, Aubry, "General Detection-based Text Line Recognition", NeurIPS 2024,
  https://arxiv.org/abs/2409.17095. Detection fitted from line labels; read through an extraction.
