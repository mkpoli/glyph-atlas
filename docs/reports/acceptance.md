# Acceptance

What each task card asked for and what the run measured, on 2026-09-11. Commands run from the
repository root with `.venv/bin/atlas`; the tables are the ones under `work/`, which are not
committed, so a reader can rerun each line.

## Datasets

| Dataset | Documents | Pages | Lines | Units | Notes |
| --- | ---: | ---: | ---: | ---: | --- |
| `work/codh-full` | 44 | 6,151 | – | 1,086,287 | 4,328 distinct code points, 742 pages with no coordinate row |
| `work/honkoku-lines` | 4,140 | 79,086 | 1,169,304 | – | per-licence counts equal the source's own splits |
| `work/ndl-minhon` | 1,599 | 32,822 | 641,632 | – | 66,537 in v1, 575,095 in v2 |
| `work/honkoku-data` | 7,584 | 246,657 | – | – | 246,659 page texts |
| `work/kokatsuji` | 1 | 340 | 3,400 | 36,869 | 13,896 identified, 6,252 ambiguous, 16,721 unassessed |
| `work/hilab` | 1 | – | – | 325,261 | one document for the dataset as published |

Every directory passes `atlas tables validate`.

## Cards

### T01 Table store and validation

Acceptance: `atlas import codh cache/codh/200006663.zip`, then `atlas tables validate work/codh`
passes and `stats` reports 121 units.

Measured: 121 units, validation clean; `tests/test_tables.py` covers 49 cases, among them an empty
table keeping every column, a dangling `page_id`, a conflicting duplicate in `merge`, and a scan
over a sharded directory. A defect found later in the run: `scan` with a column subset left out
fields the model requires and failed validation on all 1,086,287 CODH rows; the projection now adds
them back (commit `af89960`).

### T02 Downloads, IIIF images and the image cache

Acceptance: `atlas images fetch work/codh/pages.parquet --document codh:200006663` stores 10 images
with checksums and sizes in the index.

Measured: `atlas images info` filled 2,095×3,022 and 2,114×2,994 from the live `info.json`; a fetch
stored sha256 `fae3dd7a…` (680,464 bytes) under `cache/images/fa/`; `atlas images crop` wrote JPEGs
of exactly 120×140 and 90×110. 75 tests, all against the local HTTP fixture.

### T03 Koji markup parser

Acceptance: `scripts/check_koji.py` over 10,000 random Honkoku-Lines rows reports `plain` equal to
`plain_text` for at least 99.5%.

Measured: 10,000 of 10,000 (100.0000%), and 1,169,304 of 1,169,304 over the whole corpus in 1m43s.
96 tests. The rule was derived from the data; three claims in `koji_notation.md` disagree with it
and the module docstring says which.

### T04 Reference tables

Acceptance: the three data files committed with headers; tests pass offline.

Measured: `data/vocab/mj-hentaigana.tsv` 299 rows / 286 with a code point; `kanji-equivalents.tsv`
2,026 rows (1,221 compatibility, 639 shinji-kyuji, 166 itaiji); `equivalence-policies.yaml` with
`align-v1` and `strict`. The card's own tests pass: `candidates("か")` has 13 entries starting at
`U+304B`, `jibo("U+1B098") == "子"`, `readings("U+1B098") == ["ね", "こ"]`. The 新旧字体 relation is
not in the MJ main table as the card assumed; it comes from the MJ縮退マップ and the header says so.

### T05 Remote zip reader

Acceptance: `scripts/check_remotezip.py` lists 634,782 entries within three requests.

Measured: 634,782 entries, 3 requests, 11.6 s, central directory 64,661,368 bytes; a second listing
costs one HEAD. 11 tests including a zip64 fixture with 65,536 members and a server that ignores
ranges.

### T06 Rights vocabulary and resolver

Acceptance: every distinct `image_license` / `image_license_url` pair in Honkoku-Lines' `items.tsv`
resolves to a non-unknown licence except `(unspecified)`.

Measured: 27 distinct pairs; 26 resolve, and the fully blank pair — the upstream's `(unspecified)` —
stays unknown. 51 statements, 136 tests. Terms pages from which the source derives no identifier
resolve to `restricted` with the holder named for follow-up.

### T10 Import the whole CODH くずし字データセット

Acceptance: 1,086,326 character units, 6,151 pages, 44 documents, 4,328 distinct `unicode`;
`atlas tables validate` passes.

Measured: **1,086,287** character units, **6,151** pages, **44** documents, **4,328** distinct code
points, validation clean. The 39-unit difference is upstream: `200004148` holds 38,565 coordinate
rows against 38,572 on its book page and `200021802` 19,575 against 19,607, and the published total
does not describe these archives (the individual book pages sum to 1,077,799 while their CSVs hold
1,077,760 rows). No book ships a `_report.csv`, so report units are zero. 742 pages carry no
coordinate row: 挿絵, covers, 見返し and blank leaves, which the source itself lists as excluded.
Eight boxes in three books reach past their page image and are cut at the edge with the upstream
coordinates kept in `upstream["box"]`. Every page image is registered in the content-addressed
cache.

### T11 Import the 古活字データセット

Acceptance: 36,869 units, 341 pages.

Measured: 36,869 units, **340** pages. The archive's `unzip -l` prints 341 entries for
`001/page/` because it counts the directory itself; the archive holds 340 images and the CSV names
exactly those. 13,896 units identified, 6,252 ambiguous, 16,721 unassessed (2,301 single-character
blocks carry a 字母 no code point of the tables has, mostly because the dataset writes 新字体 where
the tables hold 旧字).

### T12 Import the 東京大学史料編纂所 くずし字データセット

Acceptance: 325,261 units, 5,887 distinct `unicode`, 47,477 units with hiragana or katakana script.

Measured: **325,261** units, **5,896** distinct code points, **47,477** kana units. The archive has
5,896 `U+XXXX` folders; the root subtree holds 5,886 and the full tree adds ゟ U+309F and nine
single-crop kanji. `data/sources/hi-lab-kuzushiji.yaml` records the corrected figure. The kana count
takes both kana blocks whole, iteration marks included, as the card's figure does.

### T13 Import Honkoku-Lines

Acceptance: 1,169,304 lines, 4,140 documents, 79,086 pages, counts by `image_rights.licence` equal
to the card's provider table.

Measured: exactly those counts. PDM 1.0 603,739 lines / 1,297 documents and CC BY 4.0 414,772 /
2,453 equal the source file's `lines_pdm` and `lines_cc_by`; their sum is `lines_bundled`; the split
counts match the source's train/val/test exactly. Zero koji mismatches.

### T14 Import the NDL古典籍OCR学習用データセット

Acceptance: 523,283 lines (66,537 v1, 456,746 v2), 32,822 pages, skipped rows counted.

Measured: **641,632** lines (**66,537** v1, **575,095** v2), **32,822** pages, 0 rows skipped. The
20240207 archive holds 575,095 v2 rows against the 456,746 its release note states; the 2023 release
matches its own note on both versions exactly, so the note does not describe this archive. One
upstream row has four collinear points and carries no box, with its points kept in `meta.points`.

### T15 Import みんなで翻刻データ v3

Acceptance: 70 projects imported; `work/coverage.tsv` with the number of pages that have text and no
lines.

Measured: 70 project directories, 55 with entries, 7,584 documents, 246,657 pages and page texts.
Coverage: 128,236 pages with text, 87,185 reached by a line dataset, 47,681 with text and no line —
of which **23,718 are on entries a line dataset reaches** and 23,963 on the 1,761 entries none
reaches. 1,739 entry manifests failed upstream (gallica 429, honkoku.org 404, khirin-a 500, DNS
failures) and are retried on a later run; the coverage file says so above its header.

### T16 Rights reconciliation

Acceptance: the report's per-licence line counts equal the card's provider table, and every
difference between the upstream field and the manifest is listed.

Measured: all eleven per-licence line counts and document counts equal T13's import exactly; 2,129
disagreeing evidence rows are listed with their causes (NDL manifests state an API help page rather
than a rights statement; three holders' terms pages are absent from the vocabulary; the 史料編纂所
and 地震研究所 manifests state a reuse page the vocabulary records as restricted; 国立公文書館's
holder row disagrees with its manifest).

### T20 Training data for the character detector

Acceptance: 6,151 pages processed; unique boxes 1,086,326 minus the dropped-by-clipping count.

Measured: **6,151** pages, **1,086,287** unique source boxes with **0** dropped by clipping (a box
always keeps at least half its area in the tile holding its centre), 193,810 clipped parts dropped
under the 40% rule, 53,238 tiles of which 2,661 are empty and kept under the 5% cap, 7.80 GB on
disk. Train 45,162 tiles / 917,309 boxes, val 2,713 / 44,782, test 5,363 / 124,196.

### T21 Character detector

Acceptance: recall ≥ 0.95 and precision ≥ 0.95 at IoU 0.5 on the printed test books, manuscript
numbers reported.

Status: the framework spike rejected mmdetection on four reproduced blockers and accepted RT-DETR
through `transformers`; `detect.py` serves the exported ONNX model over the T20 geometry. The run is
bounded at six epochs of the configured 24 (0.30 s/iteration x 5,646 iterations x 24 would be 31
hours). The curve is measured on a dense score grid, because the head's scores live between 0.005 and
0.05 at the start of training and a fixed threshold reads zero there. Dense curve, val split:

| epoch | best F1 | at score | precision | recall | mean IoU |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 0 | 0.531 | 0.030 | 0.457 | 0.632 | 0.861 |
| 1 | 0.735 | 0.030 | 0.616 | 0.910 | 0.889 |
| 2 | 0.687 | 0.030 | 0.712 | 0.663 | 0.888 |
| 3 | 0.714 | 0.020 | 0.608 | 0.864 | 0.889 |
| 4 | 0.737 | 0.020 | 0.622 | 0.903 | 0.893 |
| 5 | 0.744 | 0.030 | 0.756 | 0.732 | 0.891 |

The peak does not climb monotonically: the optimum trades recall for precision at the same F1, while
the training loss keeps falling. That is calibration rather than capability, and it is why the curve,
not one threshold, is what the run reports.

The test split, measured once from the checkpoint with the best dense-curve F1 (epoch 5) at the score
chosen on val (0.02), IoU 0.5, over 5,363 tiles and 477 pages:

| measure | value |
| --- | ---: |
| precision | 0.700 |
| recall | 0.928 |
| F1 | 0.798 |
| mean IoU of matches | 0.875 |
| true positives | 115,293 |
| false positives | 49,463 |
| false negatives | 8,903 |

By production type: manuscript (433 tiles) precision 0.634, recall 0.967; woodblock (2,187) precision
0.639, recall 0.968; the `unknown` stratum (2,743) precision 0.750, recall 0.903. Recall by box size
runs from 0.812 in the smallest decile to 0.982 in the largest, so the boxes the detector misses are
the small ones.

The card's acceptance is precision and recall of 0.95 at IoU 0.5: **recall 0.968 on the printed books
meets it and precision 0.639 does not.** The precision figure is a floor rather than a settled number,
because 74.4% of the false positives sit on ink CODH never annotated — by the card's own note that
includes ruby — and the split carries no ruby boxes to exclude them with; the remaining 25.6% are a
duplicate or a split of a real character, which is what a tighter NMS and the alignment's own scoring
are for. Mean IoU of 0.86 to 0.88 says the boxes it reports are well placed, so the miss is extra
detections rather than bad geometry. The run was bounded at six epochs of the
configured 24 for time — at 0.30 s a step the full schedule is about 11.6 hours of GPU — and the honest
reading is that the head had not finished calibrating: the whole operating range still sits between
score 0.005 and 0.05 while the matched boxes have a mean IoU of 0.89, and the last epoch improved the
best F1 by 0.007. The ONNX export passes its parity check (79 detections matched over ten tiles, none
beyond 1 px) and serves a real 2,011×3,203 page in 1.13 s on CUDA. What
keeps this from being the end of the matter is that the pilot's acceptance is a different measurement:
joint precision of *accepted* units at a coverage, where the alignment's own accept threshold decides
which units are published. A detector with 0.93 recall and 0.70 precision is a workable front end for
that, because the alignment's match probability and the classifier's agreement are what reject a
detection; whether they are enough is what T23's acceptance will show, and it has not been measured.

### T22 Coarse character classifier

Status: the manifests are built on the full CODH tables — 917,309 train, 44,782 val and 124,196
test crops over 1,594 classes (1,593 code points with at least 20 train crops, plus `other`), with
1,086,287 crops on disk, 1,077,621 cut from the materialised tiles and 8,666 from page images. The
preprocessing is grey, padded to square and resized to 96x96. A smoke-trained model exported to
ONNX agrees with PyTorch on 200 test crops to 1.9e-6 and top-1 for all 200. Training is queued
behind the detector (12 epochs, about nine minutes an epoch, 1.2 GiB peak) and its metrics will be
filled in here.

### T23 Alignment

Acceptance: on the held-out pilot pages, joint precision of accepted units ≥ 0.95 at coverage ≥ 0.80
on printed main text.

Status: the aligner, its runner (`atlas align`), the pilot run configuration and 12 tests are in
place, and the whole path has been exercised on a real calibration page with ONNX models: the
detector returned 551 boxes on the page, the runner aligned both lines and wrote 22 units with boxes
and detector scores. The acceptance itself needs three things that do not exist yet: the trained
classifier, the calibration truth (T25, human) and the pilot run.

One dependency is worth stating plainly, because it decides whether M2 can be measured at all: the
pilot pages come from Honkoku-Lines, which carries line boxes and text but no character boxes, so
the character boxes can only come from this detector. The CODH books do carry their own boxes, but
none of them is a pilot page, so a detector that cannot find characters leaves the pilot without a
prediction to score. If the six-epoch run does not reach a usable detector, the honest outcome is
that M2's measured pilot is blocked on training, not that the measurement is skipped.

### T24 Pilot selection, packages, evaluator

Acceptance: `data/pilot/items.tsv` and the protocol committed; `atlas pilot export` produces
packages for the calibration pages.

Measured: `data/pilot/items.tsv` selects 10 items and 472 pages across both eligible licences and
six hosts, and now carries the production type its manifests state, which gives the pilot two
manuscripts alongside the prints; `docs/implementation/pilot-protocol.md` is committed with the
interface's known limits. `atlas pilot export --group calibration` wrote 20 packages holding 212
lines with their images, and `atlas pilot images --group calibration` fetched those 20 pages (4.3 MB
each on average). For the held-out group `atlas pilot images --per-item 10` fetched 100 pages (64
new, 36 already in the cache, none failed) and their packages are written the same way.

### T30 Synthetic hentaigana renderings

Acceptance: every code point in `hentaigana.tsv` rendered with at least one font.

Measured: 286 of 287 code points, 114,400 renderings over two fonts, 2.1 GB. U+1B11F is in neither
font's cmap and the script reports it rather than drawing a `.notdef` box.

### T40 Review service

Acceptance: a box move, a split, a merge, a reading change and a 字母 choice round-trip through
`POST /reviews`, `apply` and `replay`.

Measured: 22 tests over a fixture dataset including a stale `base_revision`, an idempotent repeat, a
split then a merge, a replay that repairs a state a crash left behind, and a store reopening on
tables another writer rewrote. Driving the live service over a calibration package that `atlas align`
had just written: `/queue` listed both lines with their unit counts, `/lines/{id}/units` returned
units with boxes, readings and revisions, posting a reading change answered 200 with event
`rv00000001` at revision 1, an idempotent repeat answered `duplicate: true` without a second event,
and a stale `base_revision` was refused with 409. `/units/{id}/candidates` returned the 13 kana forms
of か with their 字母 and reference URLs, and for a kanji unit the single entry that is the unit
itself, marked `current` — the 字母 list belongs to kana, and the response's `classification` field
is what tells the interface which case it has.

The defect this driving found is worth recording: the store copies `lines` and `units` into SQLite on
first open and served that copy for ever, so a dataset whose units an alignment had just written
still reported zero units per line. The store now fingerprints those two tables, reloads them when
they change and replays the review events it holds; the one case it refuses is a store holding events
`reviews.jsonl` has never seen, where reloading would throw them away, and it says so.

### T41 Review interface

Acceptance: the browser script passes; the screenshots reviewed.

Measured: the Svelte application builds to 128 KB (103.8 kB JS, 11.8 kB CSS) and the service mounts
it at `/` with every API path unchanged. `tools/check.mjs` passes 24 of 24 checks over the HTTP and
event contract — 2 documents, a cached page served from `/images/{sha}`, an uncached page showing
its IIIF URL, open and leave timing, accept, box move, split with the inputs retired, 字母 with its
reference glyphs and the four-event chain, undo by compensating review, merge and its undo, a 409 on
a stale revision and one on a retired unit, an idempotent repeat, `POST /lines` and `POST /units`,
and 16 events written to `reviews.jsonl` by `atlas review apply`. `tools/browser-check.mjs` passes
15 of 15 in a real Chromium over the DevTools protocol: clicking a character selects its unit,
`a`/`s`/`j`/`z`/space act and repaint, a drag posts a box review, a 240-unit line renders 103 boxes
and 93 characters with the window at 101–193, and the page view draws a line box per line. 17
screenshots at 1440×900 and 400×820 in light and dark are committed under `apps/review/shots/`,
including 割書, a virtualised long line, the 字母 picker and the 409 dialog.

What the browser script does not cover: non-Chromium browsers, touch and IME input, screen readers,
and real upstream image servers.

### T42 Audit sampling

Acceptance: a report for the pilot run committed under `docs/reports/`.

Measured: sampling, the blind queue, the review events, `apply` and the weighted report work
together on 2,843 real units from 20 calibration pages; `docs/reports/audit-scratch-stub-s0-n200.md`
is a worked example and says it is not a measurement. The published report needs the trained
pipeline and human review.

### T50 Release export and T51 Documentation

T50's acceptance is `out/0.1/` for the pilot builds; the pilot has no reviewed units yet, so no such
release exists and the default filter refuses loudly rather than writing an empty directory. What is
measured instead is a full release over `work/kokatsuji`:

`atlas export work/kokatsuji --out out/verify-0.1 --review transcriber --include-machine --crops`
wrote **36,869 units, 3,400 lines, 340 pages, 1 document and 36,869 crops**, with `modern_kana` and
`shinji` appended to the units table under policy `export-v1` version 1. The release holds
`ATTRIBUTION.md`, `COUNTS.md`, `datasheet.md` (all 22 placeholders filled), `README.md` (dataset
card), `zenodo.json`, `CHECKSUMS.txt` with 36,878 entries, and `MANIFEST.json` naming the policy and
the input's tables. The first 4,000 checksums verify against the files on disk.

A HI Lab crop run needs `atlas import hilab --download` first: those crops have never been
materialised locally, and the export never reaches the network.

Exporting the アイヌ関連資料 records found a case the plan's own datasets never reach: they hold
documents, pages, lines and text but **no units**, because no alignment has run over their pages, and
the export refused them with a `FileNotFoundError` on a table that was never meant to exist. Such a
dataset now releases every line of a kept document, and a test pins it. Measured over
`work/ainu-records`: 9 documents, 520 pages, 8,212 lines, 520 page texts, every release document
written. A sample of 400 checksums drawn at random from the full kokatsuji release verifies with no
mismatch, and all 36,869 crops are on disk.

T51's deliverables are committed: `docs/datasheet-template.md` with 22 placeholders,
`README.ja.md`, `CITATION.cff` (validated) and `docs/reports/README.md`.

## What is not done

- The pilot's human annotation (T25, T26) is the part that sets the pace and needs people.
- The published precision and coverage of the pipeline therefore do not exist yet; the machinery
  that measures them does.
- T30's U+1B11F has no font; T50's release cannot contain reviewed units until T25 happens.
