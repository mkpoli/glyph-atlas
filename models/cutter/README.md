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
python models/cutter/fit.py collect --split val --pages 60
python models/cutter/fit.py fit                       # frame.json, gate.json
python models/cutter/fit.py collect --split test --pages 40
python models/cutter/fit.py measure --split test      # measured-test.json
python models/benchmark/cuts.py run --cutter lattice --split test --pages 40
```

The models are fitted on CODH's two `val` books (115 pages, 16,754 characters) and measured on its
four `test` books, none of which the detector, the classifier or the refiner was trained on. A
placement is right when its box reaches IoU 0.7 with the box of its own character. `gate.json`
accepts at the probability that gave 0.985 precision on `val`.

## Held-out books

160 pages of the four `test` books, 44,194 characters, the whole pipeline run page by page
(`models/benchmark/results/cuts-*-test.json`). `consensus` is the gate this cutter replaces: the
alignment of whole detector boxes, published when the classifier and NDLkotenOCR both read the
character in the crop.

| | consensus | lattice |
| --- | ---: | ---: |
| characters published, box at IoU ≥ 0.5 | 37.1% | 61.2% |
| published boxes at IoU ≥ 0.5 | 99.7% | 99.8% |
| published boxes at IoU ≥ 0.7 | 97.3% | 97.7% |
| seconds a page | 27.6 | 2.2 |

By book, share of characters published at IoU ≥ 0.5:

| book | production | consensus | lattice |
| --- | --- | ---: | ---: |
| うすゆき物語 | printed | 35.7% | 54.1% |
| 料理物語 | printed | 33.4% | 58.5% |
| 源氏物語 | handwritten | 32.4% | 52.4% |
| 物類称呼 | printed | 42.8% | 75.0% |

Other operating points on the same pages (`measured-test.json`), share of characters published
against the share of published boxes at IoU ≥ 0.7:

| precision at IoU 0.7 | 0.95 | 0.97 | 0.98 | 0.99 |
| --- | ---: | ---: | ---: | ---: |
| characters published | 76.4% | 66.3% | 55.7% | 35.2% |

With one line in four given a fault (a character or two dropped from the transcription, added to
it or exchanged) and one character in twenty unreadable to the classifier, 57.6% of the characters
on the page are published with a box at IoU ≥ 0.7, 97.6% of the published boxes. Of the 2,247
unreadable characters 649 are published, 97.8% of them at IoU ≥ 0.7.

A transcriber's line box is longer than its ink. With every line box lengthened at both ends by
three quarters of a character (`cuts.py run --slack 0.75`), 60.1% are published at 99.8% (IoU 0.5).

## Collection pages

40 pages drawn at random from the pages the collection had extracted, 9,515 transcribed characters:
the consensus gate had published 2,831 crops, this cutter accepts 4,961, and 2,429 of them overlap
no published crop. Of 120 of those new crops drawn at random and looked at one by one, three were
doubtful (a narrow box that may hold part of its character, a box that reaches into its neighbour).

## Pages without a transcription

`glyph_atlas.segment` cuts the columns of a page that has no transcription with `frame.json` alone:
a box becomes its parts when the parts are likelier characters than the whole, read first by the
size of its column's characters and then by its own shape. It is not wired into
`atlas review propose-characters`, whose `--split` runs the geometric pass of
`character_proposals.split_boxes`.

Both were run on the Kyujanggak land registers of 1722 and 1904 (서울대학교 규장각한국학연구원). 47
detector boxes at least 1.3 times longer than wide were drawn at random from 24 pages and read by
eye, once, by the author of the pass and not by a reviewer: 22 hold one character, 23 hold two or
three, 2 hold none.

| | `split_boxes` | `segment` |
| --- | ---: | ---: |
| boxes of two or three characters cut | 4 of 23 | 17 of 23 |
| of those, every cut between two characters | 4 | 16 |
| boxes of one character cut | 0 of 22 | 0 of 22 |
| box moved onto one of its characters, the other left unboxed | 0 | 2 |

Boxes wider than long, the unboxed ink `segment` adds (93 boxes on one page of 654 detections) and
blocks of four small characters in one box were not measured. The cover title, a box of two
characters eight times the page's character size, is cut by `split_boxes` and moved onto one
character by `segment`. A page takes about 45 s on a CPU.

## Limits

- The models are fitted on two printed books. Manuscripts, Chinese prints and Korean books are
  measured only through the 源氏物語 pages and the collection sample above.
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
