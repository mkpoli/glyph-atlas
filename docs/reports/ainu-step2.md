# The アイヌ関連資料 line-box derivation

The plan's step 2 for the Ainu records. Step 1 imported the みんなで翻刻 project's アイヌ関連資料
witnesses as documents, pages, page texts and transcribed lines; the lines have no boxes, because the
platform transcribes a page and the atlas has nothing to align a character to. This step gives those
lines boxes, places characters in them with the trained detector and classifier, and records what it
could not do.

The work is `src/kuzushiji_atlas/ainu.py` (the rule and the dataset write),
`scripts/ainu_columns.py` (the census), `atlas ainu derive` (the command) and
`tests/test_ainu.py` + `tests/test_ainu_report.py` (24 tests).

## The rule

A vertical line of a woodblock print is a column of ink. The detector finds the characters; the
characters group into columns read right to left; a page's columns are paired with its transcribed
lines when the two counts agree, right-to-left against `Line.seq`.

Two thresholds decide the grouping, and both were measured on real pages:

- **A step.** Within a column, one detection's centre follows the last by at most `0.5` of the page's
  median character width (`GAP_RATIO`). A larger step ends the run.
- **A merge.** Two runs are one column when their centres are closer than `0.9` of that width
  (`MERGE_RATIO`), because a line whose characters lean or thin out pauses by more than a step without
  ending.

Measured on 蝦夷紀行 page 2 (the page the rule was fixed on): 20 transcribed lines, 308 detections,
median character width 30 px, ink columns whose white space is 8 to 22 px, line centres 50 px apart.
The rule gives 20 columns for the 20 lines. Two other rules were measured and rejected:

| rule | what it gives | why it fails |
| --- | --- | --- |
| facing-edge white space below 0.9 width | 3 columns for 20 lines | the white space between two columns is 12 px, narrower than the threshold, so all lines merge |
| facing-edge white space below 0.3 width | 10 columns for 10 lines on page 1, 8 for 20 on page 3 | it works on the page it was tuned on and not on its neighbour |
| every pair across runs below 0.9 width | a median of 0.20 columns a transcribed line over the census | a wandering character of one column sits close to a run it does not belong to |

The census over all 658 pages with the shipped rule is what the rule is held to. Its input is the
dataset's cached page images, its output is `work/ainu-records/columns-mean.tsv` (one row a page,
including the effective parameters and the reason a page was not paired) and
`work/ainu-records/detections.jsonl` (the detections it computed, so another rule can be scored
against the same detections without running the detector again):

```
.venv/bin/python scripts/ainu_columns.py --all \
    --cache work/ainu-records/detections.jsonl --out work/ainu-records/columns-mean.tsv
```

The detections are cached under the model file's hash and the score, so a run at another operating
point or with another export does not silently reuse them.

## What is written

`atlas ainu derive work/ainu-records [--limit N] [--no-align] [--cache FILE]`:

1. detects every page (or reads the detections from `--cache`),
2. pairs the pages it can and sets each paired line's box to the union of its column's ink, recording
   `meta["derivation"] = {"source": "ainu-derive", "method": "ainu-ink-columns-v1"}` and
   `match_method = "ainu-ink-columns-v1"` on the line,
3. withdraws the box a previous derivation wrote on any page this run refuses, so a stricter run
   leaves nothing of a looser one behind, and never touches a box a person set,
4. writes `columns.tsv` beside the dataset with one row a page, including the reason a page was not
   paired and the effective parameters of the run,
5. unless `--no-align`, runs the aligner over the directory with the pilot run's detector and
   classifier, which writes the character units into the derived containers.

## The gate, and the defect it fixes

A count match is not evidence. On 蝦夷紀行 page 2 the rightmost column holds four detections for a
line of twenty characters, because the leaf is torn at its edge; the counts agree and the pairing is
wrong. The first version of the gate averaged the whole page, which a page-wide mean cannot catch: four
lines of twenty characters against columns of 20, 20, 20 and 2 detections averages 0.78 detections a
character, over the floor, while the first line's own column stands at 0.10. The gate is now per
column — `MIN_DETECTIONS_PER_CHARACTER = 0.5` against each column and the line paired with it — and
`tests/test_ainu.py::test_one_thin_column_refuses_the_page` is the regression.

The gate is a screening statistic. It cannot prove that a column is the line it was paired with, nor
that the reading order is right, and a character the detector missed lowers it without the pairing
being wrong. Only a reviewer settles a page.

## Measured limits

The census is the honest statement of what the derivation can and cannot do, and it is a filter for
which pages to propose on, not a verdict. Measured over all 658 pages, 9 witnesses, 40 m 41 s
(3.34 s a page, detection included):

- 477 pages carry a body transcription (four lines or more); the other 181 carry a title, a shelfmark
  or nothing and are not evidence either way.
- Of the 477 body pages, **117 have exactly as many ink columns as transcribed lines** and 329 are
  within a quarter; the median ratio is 1.13.
- The per-column evidence gate leaves **53 pages** the derivation will write boxes for. The 64 pages
  the gate refuses are the ones whose weakest column holds 0.04 to 0.47 detections for each character
  of its line — most of one witness, whose pages are torn at the leaf edge — while every paired page's
  weakest column stands at 0.50 or better (median 0.71). The threshold is a floor on the evidence and
  not a tuning knob: 113 pages would pair if the gate averaged the page instead of each column, and
  the page that motivated the gate is among them.
- The per-witness spread is wide: 龍谷大学's 蝦夷紀行 12 exactly of 15 (median 1.00), 立命館's copy 34
  of 98 (1.07), one witness 51 of 56 (1.00) whose "lines" are catalogue entries, and one 0 of 38
  (2.56) whose transcription covers a fraction of what its pages show.

Two further limits are worth stating plainly.

**A count match can still be wrong.** A page whose column count equals its line count may pair the
wrong column with the wrong line, and a page with a thin column can pass the gate on luck. The derived
boxes are therefore proposals: they are recorded as derived, a reviewer is the one who settles them,
and the pages the derivation refuses keep their transcriptions with no box rather than a guessed one,
which is what the plan asked for.

**Unit-level alignment is weaker here than on the pilot, and the models show it.** The pilot's aligner
places characters inside line boxes that Honkoku-Lines ships, on printed books the classifier was
trained on; the Ainu witnesses are cursive manuscripts at a different scale and hand. Measured over
the whole derivation, with the detections cached:

| stage | result |
| --- | --- |
| pages derived | 658 in 1 m 16 s (the first run, which had to detect, took 16 m 35 s) |
| line boxes written | 788 on 53 pages |
| character units placed | 10,872, of which 8,487 carry a box |
| accepted | **1,000** (9.2%), every one of them with a box inside its own line box |

The accepted share is 9.2 percent against the pilot's 17, and the reason is the models rather than the
alignment: on one 立命館 line of 25 characters the detector finds 439 characters on the page and only
27 whose centre falls in that line's column, at scores of 0.03 to 0.05 against the 0.02 cut, and the
classifier's probability for the right reading is near zero. The characters and readings that were
accepted are ordinary running text — の, に, り, を, る lead the list — which is what a coarse
classifier does best. Everything is recorded as a machine proposal with its confidences, and nothing
in this step claims it is right: a model trained on these manuscripts, or a reviewer, is what would
make the unit level real, and the derived line boxes are what such a run needs.

One defect found while measuring this is worth recording, because it was invisible in the counts. The
aligner built its detector without a score, so it used the library's default of 0.3 while the
derivation had found the boxes at 0.02. On printed pages, where detections score well above both, the
difference never showed; on these cursive columns every detection sits near the cut, so the aligner
found nothing and wrote 10,872 boxless units while reporting 59 accepted. The operating point is part
of the run configuration now (`Run.score`), the fingerprint leaves it out so the pilot's existing unit
ids do not move, and a test pins both.

## What this step does not do

- It does not annotate anything: T23's joint precision, T25's calibration truth and T26's held-out
  measurement are human work, and the derived boxes are the containers those annotations need.
- It does not decide the reading order or the transcription's correctness; it pairs a page's columns
  with its lines by count, right to left, and refuses the page when the evidence is thin.
- It does not touch the pages it refuses beyond leaving them unresolved, with their transcriptions
  and no box.
