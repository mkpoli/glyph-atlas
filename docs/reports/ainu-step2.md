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
3b. fills a page's pixel size from its cached image when the import states none, because the review
   interface scales every box by it,
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
- The mismatch runs one way. Of the 360 body pages whose counts differ, **347 show more ink columns
  than transcribed lines and 13 show fewer** — and the 13 are spread over five of the nine witnesses
  rather than concentrated in one, so no single hand explains them. One extra column accounts for 72
  pages and two extra for 61, so 133 pages sit within two columns of pairing; 60 are ten or more out,
  and the largest is 28. The extra columns have no single cause: most are lone detections outside the
  text block, and a second leaf the transcription does not cover accounts for some of the rest, so no
  threshold rule over the current pipeline is proposed here. The measurement behind that is in "What
  the extra columns are" below. And 153 of the 658 pages carry no transcribed line to pair with at
  all, which no threshold can recover.
- The per-column evidence gate leaves **53 pages** the derivation will write boxes for. The 64 pages
  the gate refuses are the ones whose weakest column holds 0.04 to 0.48 detections for each character
  of its line — most of one witness, whose pages are torn at the leaf edge — while every paired page's
  weakest column stands at 0.50 or better (median 0.71). The threshold is a floor on the evidence and
  not a tuning knob: 114 pages would pair if the gate averaged the page instead of each column, and
  the page that motivated the gate is among them.
- The gate's exact value decides how many pages pair, and there is no natural break to choose it at.
  Over the 117 pages whose column count already matches their line count, the number that pairs is 115
  at a floor of 0.05, 107 at 0.10, 95 at 0.20, 77 at 0.30, 63 at 0.40 and 53 at 0.50: a smooth ramp
  from 0.48 downward, because the measured values run 0.04, 0.06, 0.08, 0.09 and so on without a gap.
  0.50 is a policy — a column has to hold about one detection for every two characters of its line —
  and the report should say so rather than imply the data chose it.
- The per-witness spread is wide: 龍谷大学's 蝦夷紀行 12 exactly of 15 (median 1.00), 立命館's copy 34
  of 98 (1.07), 蝦夷草紙 51 of 56 (1.00) on the densest transcription here at 413 characters a page,
  and one 0 of 38 (2.56) whose transcription covers a fraction of what its pages show.

### What the extra columns are

The count mismatch is the derivation's largest single reason to refuse a page, so it is worth knowing
what the extra columns are before writing a rule to remove them. `scripts/ainu_mismatch.py` measures
that against the cached detections, and the answer is that there is no single cause.

**They are not split lines.** A column that the segmenter had wrongly split would sit close to the
column it was split from. Of the 1,422 thin columns (two detections or fewer) on over-counting pages,
53 percent stand five or more median character widths from any body column and only 6 percent stand
under one; the median is 5.58. Their median height is 4 percent of the text block, and 73 percent are
under a tenth of it. These are lone detections out at the page edge, below the block, or over the
cradle and the colour patch — a detector running at a score of 0.02 firing on something that is not a
character, each one its own "column" by construction. One page of 藻汐草 was rendered with its columns
marked to confirm it: the kept columns cover the text block and the five candidates sit at the top of
the frame and off the bottom-left edge of the leaf. The remaining 28 percent do sit within two widths
of a body column, so a split character is the explanation for some minority of them and not the
mechanism behind the counts.

**Part of it is a spread the transcription only half covers.** The same page is a scan of two facing
leaves: 214 of its 460 detections and 11 substantial columns lie on the right leaf, which its
transcription does not cover at all, because the transcription is addressed per leaf while the image
holds both. The module's own region split is what separates them, so a count taken over the whole
image cannot agree with one leaf's line count. That is a modelling problem and not a threshold, so the
region split was measured as a repair too: of the 360 mismatched pages, 7 have a region whose own
columns match the line count and pass the evidence gate. Where the extra columns are a second leaf,
pairing already refuses the page, which is the safe outcome.

**A rule that removes them is worth more than it costs, but not by enough to ship on this evidence.**
Dropping thin columns that stand clear of the block, applied at every page, would newly pair 52 to 128
pages depending on the gap; at the same time it takes a column away from 5 to 8 of the 53 pages that
pair today, which is the outcome that matters more, because those pages' line boxes and unit ids
already exist. Applying it only where the count is already wrong costs those 53 nothing and would pair
**131** more pages — but 146 of the 162 pages it repairs need the *smallest* gap tried, which says the
line count is doing the choosing rather than the geometry. That is a page fitted to a known number, not
a rule that was measured, and the honest place for it is behind a reviewer looking at the page.

What follows from this is a note for whoever works on the derivation next: on this corpus the count
mismatch is a mixture, and no threshold over the current pipeline separates the mixture cleanly. The
pages where the transcription covers one leaf of a spread need the region split to be used in pairing;
the pages with lone edge detections need those detections excluded by position and not by count. Both
are changes to which columns are considered at all, and both want a human check on a handful of pages
first, which is the one thing this run cannot supply for itself.

### The evidence gate, measured against the pages it decides

The gate is the other place the derivation refuses work, and unlike the count it can be checked
against every page it decides rather than only the ones it refuses. `scripts/ainu_evidence.py`
reproduces everything below from the cached detections. Two things came out of doing that.

**The floor admits a relative rule almost exactly.** The gate compares each column against a fixed 0.5
detections a character. Comparing the weakest column against the page's own middle column instead
decides the same 52 of the 53 pages that pair today, and its union with the absolute rule covers 64 of
the 117 count-matched pages: the two rules differ on 12. Eleven of those are pages the relative rule
would pair and the absolute one refuses, and they are two 龍谷大学 witnesses — 蝦夷草紙 第1冊 for ten of
them and 蝦夷紀行 第1冊 for the eleventh — whose weakest columns sit at 0.30 to 0.48 while their middle
column sits at 0.49 to 0.91. On those pages the page is not thin, one column is. The twelfth is the
reverse: a column at 0.50 on a page whose middle column is 1.25, which is exactly the column a relative
rule exists to catch. A relative floor is therefore a real candidate rather than a loosening, and it is
the one change here whose effect on the pages that already pair is measurable and small — 52 of 53
unchanged, with the one exception being a page the relative rule catches and the absolute one does not.

**The saved census cannot show the gate's own refusals.** `derive_page` clears `Derivation.evidence`
when the gate refuses and `page_row` reads that list, so `weakest_column` is empty for exactly the 64
pages a reader would look at to judge the threshold — the report's figures for them come from
recomputing against the cached detections, not from `columns.tsv`. Anyone tuning the gate should fix
that first: a census that cannot report the value it decided on cannot support a decision about it.
Recomputed, the refusals run 0.04 to 0.48 and the paired pages 0.50 to 1.32, so the two sets do not
overlap; and the number that pairs is 115 at a floor of 0.05, 107 at 0.10, 95 at 0.20, 77 at 0.30, 63
at 0.40 and 53 at 0.50, a smooth ramp with no break to choose the value at.

### A line's characters are right, but their boxes are not

The aligner's job is to put each transcribed character on the ink it names. It matches a line's tokens
to that line's detections with a monotone dynamic program — both sequences are walked forward together
— so the order the detections arrive in decides which character gets which box. The step before it
orders them with

    sorted(inside, key=lambda detection: (-detection.centre[0], detection.centre[1]))

which is meant to be columns right to left and, inside a column, top to bottom. It sorts on the raw
float x centre, so two detections whose x centres differ by a pixel are ordered by that difference and
the y key almost never decides anything. On these manuscripts most of a line's detections sit within a
few pixels of the same x, and the detector's own output is in no order at all, so the detections reach
the dynamic program in an order that is neither. It is faithful to what it is given.

The effect is measurable without any ground truth, because a line's reading order is known: a unit's
`seq` is its position in the line and a vertical line is read down the column, so the boxes of a line
should advance downward as `seq` advances. Measured over the corpus (`scripts/ainu_reading_order.py`):

| | |
| --- | --- |
| lines with two or more boxed units | 784 |
| boxes strictly advancing in `seq` order | **39** |
| lines with at least one step back up | **745** |

One line makes it concrete. `hk:9987c791…:25:L16` transcribes 普く天下万郡にもかゝる妙泉も又と類ひあるまし
and its 21 boxed units join to exactly that text, so the line and the reading are right. Their y
centres run 874, 855, 890, 792, 710, 776, 755, 560, 267, 352, 612, 293, 820, 650 … for consecutive
`seq` values, inside a column 770 px tall: the characters are paired with the line's detections in an
essentially arbitrary order. Quantising x into column buckets and sorting by y inside a bucket returns
the same detections in strictly increasing y — true at 0.5, 1.0 and 2.0 character widths — which is
what a line of vertical text should already have been.

What this does and does not touch. The line a character belongs to is right, the text of the line is
right, and the ink of the line is right; what is wrong is which character of the line each detection
is. So the line boxes the derivation writes are unaffected, the page and line levels are unaffected,
and a task that asks "does this character read い" still has a right answer. What is affected is every
consumer that treats a unit's box as *that character's* ink: the character crops in the review
workspace, any per-character bounding-box export, and character-level measurement of the alignment.
That is most of what M2's alignment acceptance would rest on, so this is worth fixing before any
character-level figure is quoted.

The fix belongs in `align.containers_of`, and this project already has the rule it needs: the Ainu
derivation groups detections into columns by a gap measured against the page's median character width
(`ainu.columns_of`), which is the same operation done properly. Sorting by column and then by y, using
a bucket no narrower than the character width, restores reading order on the pages checked.

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
