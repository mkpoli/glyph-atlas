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
| T21 detector | partial | the six-epoch run is complete and the test split measured: precision 0.700, recall 0.928, F1 0.798, mean IoU 0.875 at the val-chosen score. Recall meets the card's 0.95 bar on the printed and manuscript books, precision does not, and the acceptance page says so. The ONNX export and its parity check remain |
| T22 classifier | running | manifests and crops built on the full tables; 12 epochs on the card now |
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
lines to align to. Measured on one page: 10 transcribed lines against 195 detected character boxes, so
the line boxes have to be derived from the detected characters before anything can be aligned. That
derivation is the next piece of work, and it comes before the alignment, not with it.

## Cards that cannot be finished without people

T23's acceptance, T25 and T26 are annotation work: the alignment's joint precision at a coverage is
measured against adjudicated character annotations on pages the pipeline never tuned on. The
machinery for it exists — packages, the protocol, the review service and interface, the evaluator and
the audit sampler — and the pages are fetched, but the annotations themselves are human.

T16's remaining half is not human work but machine time: the full manifest pass needs a longer
per-host pause than `net.download` exposes today, which is a change to weigh rather than a quick run.

## What is running

The detector is on epoch 3 of 6 (dense-curve F1 0.687 at epoch 2, rising from 0.531 at epoch 0). The
classifier's gate starts it once the detector's process is gone, `free -g` available is at least
12 GB, and the detector's log has been quiet for three minutes — twice in a row, 150 seconds apart.
That gate exists because two runs competing for this machine's memory have already cost the detector
two processes tonight.
