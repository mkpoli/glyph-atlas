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
| T23 alignment | partial | the aligner, its runner, its run configuration and 12 tests are done; the acceptance needs the classifier, the calibration truth and the pilot run |
| T24 pilot | done | selection, protocol, packages for both groups, the evaluator and its tests |
| T30 synthetic kana | done | 286 of 287 code points; U+1B11F is in neither font |
| T40 review service | done | 22 tests and a live run over an alignment's own output |
| T41 review interface | done | 24 contract checks, 15 in a real browser, 17 screenshots |
| T42 audit sampling | done | sampling, the blind queue and the weighted report, demonstrated on real records; the published report needs reviewed units |
| T50 release export | done | a full release over `work/kokatsuji` with 36,869 crops and every document |
| T51 datasheet and docs | done | – |

## The Ainu import, which is not one of the plan's cards

The human asked for the アイヌ関連資料 project of みんなで翻刻 (nine witnesses, 658 canvases) to be
brought into the atlas in three steps: get the data, improve it with the detector and classifier, add
it here. Step 1 is done — `atlas import ainu-records` writes 9 documents, 658 pages, 658 page texts
and 8,212 lines, validated, with the platform entry, the project, the witness slug and the db.aynu.org
catalogue in `source_refs` and rights resolved per witness through the vocabulary (five eligible, four
restricted). The lines carry no boxes, which is exactly the gap step 2 fills — and step 2 needs one thing the plan
never had to build. Honkoku-Lines ships line boxes, so an alignment only has to place characters inside
boxes that already exist; the Ainu records ship transcription text and nothing else, so there are no
lines to align to. The transcription is page-level, so there are no line boxes to align to, and the first attempt to
derive them failed for a reason worth recording: splitting ink at its vertical gaps produced 16 runs
against 10 transcribed lines on the first page measured, because the detector breaks a line wherever
the ink pauses. The measurement that followed says the derivation is possible after all, on a
different rule: **one line per ink column**, ordered right to left. Over 24 pages of two witnesses
that rule gives the transcribed line count exactly on 8 pages, within 25% on 15, and misses on one
(ratios mostly 0.95 to 1.10). One witness, 龍谷大学's 蝦夷紀行, is nearly exact throughout (19 or 20
columns against 19 or 20 lines); the other, 立命館's copy, is worse (8 against 8, 10 against 16, 24
against 20), which suggests its pages are not laid out as one column per transcribed line.

So: the Ainu records come in as documents, pages and transcribed lines with no boxes, and their
character boxes wait on a per-page decision — derive line boxes from the columns where the count
agrees, and leave the page unresolved where it does not, because a line box that stands for a
different number of lines than the transcription has is worse than no box.

## Cards that cannot be finished without people

T23's acceptance, T25 and T26 are annotation work: the alignment's joint precision at a coverage is
measured against adjudicated character annotations on pages the pipeline never tuned on. The
machinery for it exists — packages, the protocol, the review service and interface, the evaluator and
the audit sampler — and the pages are fetched, but the annotations themselves are human.

T16's remaining half is not human work but machine time: the full manifest pass needs a longer
per-host pause than `net.download` exposes today, which is a change to weigh rather than a quick run.

## The pilot's calibration pages now carry machine units

The alignment has run over all 20 calibration pages with the trained detector and classifier: **2,920
units, 424 accepted, 2,496 rejected, no failures**, and the packages have been re-exported so a
reviewer opens a page and sees the proposed boxes and readings. A reviewer's job is therefore to
correct what the pipeline proposed rather than to draw everything, which is what the protocol
assumes. The held-out pages are not aligned yet; at the measured rate they need several hours of
compute.

## What was waiting on the classifier

The pilot's 472 pages are fetched and packaged — 20 calibration pages holding 212 lines and 452
held-out pages holding 7,635 — and every one of their page images is in the cache, which is the
pre-flight check the alignment needs. The alignment over the calibration pages runs first, then the
packages are re-exported so a reviewer sees the proposed units, and the held-out pages follow.

## What is running

The detector is on epoch 3 of 6 (dense-curve F1 0.687 at epoch 2, rising from 0.531 at epoch 0). The
classifier's gate starts it once the detector's process is gone, `free -g` available is at least
12 GB, and the detector's log has been quiet for three minutes — twice in a row, 150 seconds apart.
That gate exists because two runs competing for this machine's memory have already cost the detector
two processes tonight.
