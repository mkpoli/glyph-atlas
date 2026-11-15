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
of one or none was cut wrong. A reviewer who drew a box again before matching it confirmed the
box drawn, and where that differs from the first as two cuts of a character do, the first was cut
wrong. The site's export of 2026-11-15 judges 970 extracted crops this way; 819 are on 664 pages
of 232 books of the Honkoku extraction, 816 cut right and 3 cut wrong, and two boxes drawn again
add the box they replaced to the wrong ones. The others are on Wikisource and NDL pages.
Such a page knows the boxes of those characters and nothing of the others on it, so a candidate
box is labelled only where a reviewed box decides it, and a placement only for a reviewed
character.

One book in four is held out by its id: 547 crops of 167 books are fitted on, 269 crops of 65
books measure the result (`measured-held.json`). The rows of the reviewed pages together weigh a
tenth of CODH's (`fit --reviewed 0.1`); at three tenths 97.4% of the boxes published on CODH's test
books reached IoU 0.7 in an earlier fit.

| | CODH alone | with reviewed crops |
| --- | ---: | ---: |
| CODH test, characters published at IoU ≥ 0.7 | 60.7% | 62.6% |
| CODH test, published boxes at IoU ≥ 0.7 | 97.7% | 97.5% |
| CODH test, characters published where 98% of boxes reach IoU 0.7 | 55.7% | 57.4% |
| held-out reviewed crops published | 246 of 269 | 249 of 269 |

Every reviewed crop is one an earlier gate had published, so the held-out crops are easier than
the collection's pages, and five cuts known to be wrong are too few to teach what one looks like.
On the 213 held-out pages, whose other characters no one has judged, the gate fitted on CODH alone
accepts 91,222 placements and this one 94,591.

The reviewed crops of Wikisource (`build-reviewed --queue ... --fetch`, 104 crops of Chinese prints
on 89 pages, and two of one NDL page) were added in one fit and are left out: on the held-out Honkoku crops that fit
published 242 of 269, on CODH's test books 62.0% at IoU ≥ 0.7, and of 45 held-out Wikisource crops
40, where the gate fitted on CODH alone publishes 41. Two fits from the same crops, the pages
detected again, differ by about 0.7 of a point on CODH's test books, so smaller differences than
that say nothing.

## Held-out books

160 pages of the four `test` books, 44,194 characters, the whole pipeline run page by page
(`models/benchmark/results/cuts-*-test.json`). `consensus` is the gate this cutter replaces: the
alignment of whole detector boxes, published when the classifier and NDLkotenOCR both read the
character in the crop.

| | consensus | lattice |
| --- | ---: | ---: |
| characters published, box at IoU ≥ 0.5 | 37.1% | 63.3% |
| published boxes at IoU ≥ 0.5 | 99.7% | 99.8% |
| published boxes at IoU ≥ 0.7 | 97.3% | 97.5% |
| seconds a page | 27.6 | 3.6 |

By book, share of characters published at IoU ≥ 0.5:

| book | production | consensus | lattice |
| --- | --- | ---: | ---: |
| うすゆき物語 | printed | 35.7% | 58.3% |
| 料理物語 | printed | 33.4% | 59.6% |
| 源氏物語 | handwritten | 32.4% | 53.4% |
| 物類称呼 | printed | 42.8% | 75.8% |

Other operating points on the same pages (`measured-test.json`), share of characters published
against the share of published boxes at IoU ≥ 0.7:

| precision at IoU 0.7 | 0.95 | 0.97 | 0.98 | 0.99 |
| --- | ---: | ---: | ---: | ---: |
| characters published | 76.3% | 67.0% | 57.4% | 37.1% |

With one line in four given a fault (a character or two dropped from the transcription, added to
it or exchanged) and one character in twenty unreadable to the classifier, 59.5% of the characters
on the page are published with a box at IoU ≥ 0.7, 97.5% of the published boxes. Of the 2,247
unreadable characters 623 are published, 98.2% of them at IoU ≥ 0.7.

A transcriber's line box is longer than its ink. With every line box lengthened at both ends by
three quarters of a character (`cuts.py run --slack 0.75`), 62.2% are published at 99.8% (IoU 0.5).

## Small writing

The detector finds a character best between 50 and 120 px a side, and the smallest tenth of its
test boxes least often (`models/detector/README.md`). Of 489 pages drawn at random from those the
collection had cut, 197 have a median box under 50 px and hold 47% of the characters. The cutter
had published 28.6% of the characters on the pages between 30 and 40 px and 43.4% on those between
40 and 50, against about 50% on pages of larger writing.

The extraction therefore detects such a page a second time, enlarged until its median box is 81 px,
at most twice and never so far that its ninth-decile box passes 118 px, and joins the boxes of
both (`Detector.boxes_sized`). CODH's `test` pages shrunk to half their size stand in for small
writing whose boxes are known (`cuts.py run --shrink 0.5`, with `--cutter lattice-own` for the
detector at the page's own size; `results/cuts-lattice*-test-shrink0.5.json`):

| half-size pages | detector at the page's own size | detector on the enlarged page |
| --- | ---: | ---: |
| characters with a box | 87.6% | 94.9% |
| characters published, box at IoU ≥ 0.5 | 42.2% | 60.7% |
| published boxes at IoU ≥ 0.5 | 99.8% | 99.8% |

At their full size the same pages give 63.3% and none of them is enlarged, 料理物語 not even at half
size. On 24 pages of small writing from 19 books of the collection the cutter accepts 7,355
placements from the page at its own size and 8,588 from the enlarged one, more on every page; of
160 of the new ones looked at, nearly all frame their character, and the few placements it no
longer accepts are mostly good ones too. `frame.json` and `gate.json` were fitted before this, on
the detector's boxes at each page's own size; `fit.py collect` now takes the boxes the extraction
takes, so the next fit sees them.

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

Most of what the cutters repair comes from the size of the writing. The registers set their main
entries in characters about 75 px a side and their notes in two half-size columns, as 割注 is set,
about 40 px a side; eight tenths of the boxes of the detector's test books are between 50 and 120 px
and it finds the smallest tenth least often (`models/detector/README.md`). On the page enlarged one
and a half times (`atlas review propose-characters --scale 1.5`, `Detector.boxes_at`) the notes are
about 60 px and the main entries about 110:

| page enlarged 1.5 times | detector alone | `split_boxes` | `segment` |
| --- | ---: | ---: | ---: |
| right | 89.1 | 83.0 | 83.3 |
| one character, cut | 2.3 | 6.8 | 7.0 |
| one character, lost or joined | 0.4 | 0.4 | 0.7 |
| several characters, wrong count | 8.2 | 9.8 | 8.9 |

The detector alone is then right about 93.5 in 100 on the four later pages, and either cutter after
it cuts the large characters apart. Some characters it finds at neither size and does find on the
page enlarged twice: `--fill 2` adds those boxes where no other box is, 45 on the eight pages, of
which about 36 are on a character and the rest on a stain, a ruled line or bare paper. A fill from
the page enlarged 1.25 times adds 37, about 24 of them on a character, most of which the fill from
twice the page also finds. Other enlargements were scored for the detector alone in a
scratch run that left no result file: 78.2 right at 1.25 times, where the notes are still 50 px,
80.9 at 1.75 and 83.3 at 2, where the main entries pass 120 px and come out in parts, and 80.7 with
each page enlarged until its median box is the detector's median.

`--ink` then drops the boxes that hold none of the page's ink (`glyph_atlas.pigment`). The 1722
register is ruled and sealed in red and stained brown, and the detector boxes some of that. In
optical density the page's ink and its red lie along different directions: the density of a pixel
aside from the ink's direction, against its density along it, is about -0.02 for ink and about
0.45 for the red, with almost nothing between 0.1 and 0.2, and the 1904 register has ink alone. A
pixel's density is shared out between the two directions, so a stroke under a seal still counts
as ink. On the eight pages 41 of 5,435 boxes hold none and go: red rulings, stains, parts of
seals, bare paper and one character showing through from the back of the leaf. The counted boxes
score the same, since none of them is such a box. Writing in another colour than the page's ink,
vermilion or a browned second hand beside red, has no ink to show, so it is an option.

Repairs of the boxes themselves were tried on the same pages and are not in the code. Growing a
box to the strokes of which it holds most brings a cut last stroke back and, in other boxes, takes
in the stroke of a neighbour that touches; looked at box by box the result is mixed. Dropping a
box that lies inside a larger one as a part of its character removes fragments and, in about 7 of
26, a whole character that stands in a box of two (四 in 四十). The box refiner, trained on Japanese
books, shrinks 長 and 壹 to their upper half and moves a box of two characters onto one of them:
86.9 right, or 88.4 with one box kept where two land together. Joining two boxes when most of the
smaller one's ink lies in the larger joins a fragment with its character and, in about a quarter
of 86 joins looked at, a character with the box of two it stands in. Joining pairs that overlap by
a third of the smaller: of 83 such pairs about half are two parts of one character and half a
character beside a box of two. So a box of two characters, a character in two boxes and a box that
stops short of a stroke are left as the detector drew them.

At the page's own size what is left is boxes of several characters that stay whole. Three things
were tried against the same boxes and are not in the code:

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
- 2SC1815J (@2SC1815J), post on X of 2026-10-31 about そあん (soan) プロフェッショナル版 v2.0,
  https://x.com/2SC1815J/status/2116527795329495151. A figure of old-type images whose pixels are
  plotted by an ink component and a redness component of optical density, with a line dividing ink
  from stain; the caption says brown stains and 朱点 are removed well and show-through is not. The
  post gives no procedure, so `glyph_atlas.pigment` takes the idea and not a method from it.
