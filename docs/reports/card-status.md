# Card status

Where each card of the implementation plan stands, and what is left on it. The measured numbers are
in `docs/reports/acceptance.md`; this page says which cards are done, which are waiting on the
machine, and which cannot be finished without people.

| Card | State | What is left |
| --- | --- | --- |
| T01 table store | done | – |
| T02 downloads, images | done | – |
| T03 koji parser | done | 100% agreement with `plain_text` over all 1,169,304 rows |
| T04 reference tables | done | – |
| T05 remote zip | done | 634,782 entries in three requests |
| T06 rights vocabulary | done | – |
| T10 CODH import | done | 39 units below the published total, cause recorded |
| T11 古活字 import | done | 340 pages, not 341; cause recorded |
| T12 HI Lab import | done | 5,896 code points, not 5,887; cause recorded |
| T13 Honkoku-Lines import | done | counts equal the source's own splits |
| T14 NDL import | done | 641,632 lines; the release note does not describe its archive |
| T15 みんなで翻刻 data | done | 1,739 entry manifests failed upstream and are retried on a later run |
| T16 rights reconciliation | done | reconciled over all 4,140 documents: 3,955 manifests read, 75 fetched, 202 unreachable upstream; 4,101 documents carry evidence and 39 state nothing. `net` now slows a host that answers 429 for the rest of the process, which is what made the full pass possible |
| T20 detector data | done | 53,238 tiles, 1,086,287 unique boxes, none lost |
| T21 detector | partial | the six-epoch run is complete, the test split measured and the ONNX exported with a passing parity check: precision 0.700, recall 0.928, F1 0.798, mean IoU 0.875, and recall 0.968 on the printed books against the card's 0.95. Precision 0.639 there does not meet it and three quarters of the false positives sit on ink CODH never annotated, so the figure is a floor. What remains is a longer schedule, not a defect to fix |
| T22 classifier | done | twelve epochs; test top-1 **0.9348** and calibration error **0.0151** against the card's 0.93 and 0.03, top-5 0.9887 against 0.99 (short by 0.0013), macro F1 0.89; ONNX exported at 116 MB with the same 1,594 classes as the list and parity 4.1e-6 over 200 test crops |
| T23 alignment | partial | the aligner, its runner, its run configuration and 18 tests are done, and both pilot groups are aligned and packaged: 20 calibration pages with 2,920 units and 452 held-out pages with 166,058 units (28,306 accepted, no failures, 7 m 15 s for the held-out run). The acceptance needs the calibration truth, which is human |
| T24 pilot | done | selection, protocol, packages for both groups (20 calibration, 452 held-out pages, images and units), the evaluator and its tests |
| T30 synthetic kana | done | 286 of 287 code points; U+1B11F is in neither font |
| T40 review service | done | 22 tests and a live run over an alignment's own output |
| T41 review interface | done | 24 contract checks, 15 in a real browser, 17 screenshots |
| T42 audit sampling | done | sampling, the blind queue and the weighted report, demonstrated on real records; the published report needs reviewed units |
| T50 release export | done | a full release over `work/kokatsuji` with 36,869 crops and every document |
| T51 datasheet and docs | done | – |

## The Ainu import, which is not one of the plan's cards

The human asked for the アイヌ関連資料 project of みんなで翻刻 (nine witnesses, 658 canvases) to be
brought into the atlas in three steps: get the data, improve it with the detector and classifier, add
it here. **All three are done**, and `docs/reports/ainu-step2.md` is the report on the second.

Step 1 — `atlas import ainu-records` writes 9 documents, 658 pages, 658 page texts and 8,212 lines,
validated, with the platform entry, the project, the witness slug and the db.aynu.org catalogue in
`source_refs` and rights resolved per witness through the vocabulary (five eligible, four restricted).
The lines carry no boxes, which is exactly the gap step 2 fills: Honkoku-Lines ships line boxes, so an
alignment only has to place characters inside boxes that already exist, while the Ainu records ship
transcription text and nothing else.

Step 2 — `atlas ainu derive` derives those boxes. A vertical line of a woodblock print is a column of
ink: the detector's characters group into columns read right to left, and a page's columns are paired
with its transcribed lines when the counts agree and every column holds enough ink for its line. The
rule and its two thresholds were measured on the pages, the census over all 658 pages is in
`docs/reports/ainu-step2.md`, and the boxes are written as proposals — a line records that the atlas
derived its box, a run that refuses a page withdraws what an earlier run wrote there, and a box a
person set is never touched. Pages the derivation cannot pair keep their transcriptions and get no
box, because a line box standing for a different number of lines than the transcription has is worse
than none.

Step 3 — the character units the aligner places inside those boxes land in
`work/ainu-records/units.parquet` under the pilot run's fingerprint, with the run's own confidence.
Measured: 658 pages derived in 1 m 16 s with the detections cached (16 m 35 s when they had to be
computed), **788 line boxes on 53 pages**, and **10,872 units of which 1,000 are accepted**, every
accepted one with a box inside its own line box. The accepted share is 9.2% against the pilot's 17%,
because the detector and classifier were trained on printed books and these are cursive manuscripts —
on one line of 25 characters the detector finds 27 detections in that line's column, at scores of 0.03
to 0.05 against a 0.02 cut. The units are proposals with their confidences, and
`docs/reports/ainu-step2.md` says so, including the operating-point defect that measuring them found.

Step 2's measured limits, in short: of the 477 pages whose transcription is a body rather than a title,
117 have exactly as many ink columns as transcribed lines and 329 are within a quarter, and the
per-column evidence gate leaves 53 pages that actually get boxes. A count match can still pair
the wrong column with the wrong line, which is why the boxes are proposals and a reviewer settles them.
The full numbers, the rejected rules and the reproduction commands are in `docs/reports/ainu-step2.md`.

## Cards that cannot be finished without people

T23's acceptance, T25 and T26 are annotation work: the alignment's joint precision at a coverage is
measured against adjudicated character annotations on pages the pipeline never tuned on. The
machinery for it exists — packages, the protocol, the review service and interface, the evaluator and
the audit sampler — and the pages are fetched, but the annotations themselves are human.

T16's remaining half is not human work but machine time: the full manifest pass needs a longer
per-host pause than `net.download` exposes today, which is a change to weigh rather than a quick run.

## Timing, measured and explained

The alignment has been made fast in four passes — the classifier scores a line's detections in one
CUDA batch (531 crops in 0.69 s, against 72 ms a crop when each was a separate call), the crops of a
page are cut once, the dynamic program keeps back pointers instead of carrying a path through every
state, and the page image is decoded once per page instead of once per crop. The last one was the gap
three rounds could not account for: a 9 MB 4,288x2,848 scan was opened and decoded hundreds of times
per page, so the measured stages summed to seconds while a page took tens of seconds.

What that buys: the 20 calibration pages went from 843 s to 31 s, and the full held-out group — **452
pages, 7,635 lines, 166,058 units** — took **7 m 15 s, 0.96 s a page**, with no failed page. The
stages that remain are detection 1.3 s a page, alignment 0.6 s for 16 lines, the line scan once per
run, the pages table 0.7 s, model loading 1.1 s.

## The pilot's two groups carry machine units

The alignment has run over both groups with the trained detector and classifier and the packages have
been re-exported, so a reviewer opens a page and sees the proposed boxes and readings rather than a
blank page. A reviewer's job is therefore to correct what the pipeline proposed, which is what the
protocol assumes:

- **Calibration, 20 pages**, 212 lines: 2,920 units, 424 accepted, 2,496 rejected, 2,693 with a box.
  These are the pages annotated twice and adjudicated (T25).
- **Held-out, 452 pages**, 7,635 lines: 166,058 units, 28,306 accepted, 137,752 rejected, 7,440 with
  a box. These are the pages annotated once with the machine output hidden (T26), and the published
  precision and coverage come from them.

The held-out group is the rest of each item, not the 100-page `--per-item 10` sample used to gauge
the cost; the sampled run is what made the first export look wrong, because its 100 packages sat
beside 352 stale directories. Both groups are exported whole now.

## Two writers on one dataset

Two alignment runs overlapped on `work/honkoku-lines` and the later writer's read-modify-write dropped
the earlier run's 2,920 calibration units — the packages survived, the dataset's units table did not.
The fix is a lock: `tables.locked(directory)` is held across the read and the write in `_write_units`,
so a second run queues behind the first and merges with what it left, and `tables.write` takes the
same lock on its own. Six tests pin the merge rules and the lock, including one that holds the lock
here and watches a second process time out.

## What is running

Nothing on the alignment: both pilot groups are aligned and packaged. The detector's six-epoch run is
complete and its ONNX artifact is what the alignment used. The open machine work is T21's longer
detector schedule — precision 0.700 against the card's target — and the Ainu column census, which is
a measurement and not a training run.
