# Plan

The archive covers historical Japanese writing across works, genres, and collections. The first
Hokkaido works are a pilot. Source acquisition continues across Honkoku and Japanese Wikisource;
coverage is measured separately for discovered works, collected transcriptions, scanned pages,
extracted characters, and human-reviewed forms. The resource survey below dates to 2026-09-11.

## 1. The dataset

One record per graphic occurrence: in most cases one written character on a page image, and
otherwise a standalone crop without page placement or a block holding several characters, with
the granularity stated on the record. A record holds the rectangle on the full-size image, the transcriber's string, a diplomatic reading, Unicode code points (hentaigana code points
for kana forms that Unicode encodes), the 字母 of kana, a variant key for kanji written in a form
other than the transcribed one, the line and the neighbours, and the document's production type,
genre, register and date. Every record also carries the licence of its image and of its text, and
how it was produced: imported from an existing dataset, aligned by machine, or reviewed by a person.

Images are addressed by IIIF region URL and checksum. Crops are materialised for releases only where
the image licence allows redistribution; for other images the release carries the coordinates and
the URL.

## 2. Existing resources

| Resource | Instances | Classes | Per-character boxes | 字母 | Document metadata | Licence | State |
| --- | ---: | ---: | --- | --- | --- | --- | --- |
| CODH 日本古典籍くずし字データセット v2 | 1,086,326 | 4,328 | yes | no, modern kana only | 書誌ID only | CC BY-SA 4.0 | unchanged since 2019-11-11 |
| 東京大学史料編纂所 くずし字データセット | 325,261 | 5,887 | crops only, no coordinates | no | in the 電子くずし字字典 database only | CC BY 4.0 | 2023-03-27 |
| CODH 古活字データセット | 36,869 blocks from 1 work | n/a | block boxes, some 連彫 blocks hold several characters | yes | none | CC BY 4.0 | 2023-10-03 |
| Honkoku-Lines v2.0 (橋本雄太) | 1,169,304 lines, 18.2M characters | n/a | line boxes | no | holder and image licence per line, bibliography per item | text CC BY-SA 4.0, images per holder | 2026-08-05 |
| NDL古典籍OCR学習用データセット（みんなで翻刻加工データ） | 523,283 lines | n/a | line boxes | no | attribution per book | CC BY-SA 4.0 | 2024-02-07 |
| みんなで翻刻データ v3 | 46.87M characters | n/a | none | no | manifest URL per entry | CC BY-SA 4.0 | growing |
| 国語研変体仮名字形データベース | about 42,000 at launch, 15 works by 2025 | n/a | crops | yes | none | unstated | 2020– |
| Kuzushiji-MNIST / 49 / Kanji | 70,000 / 270,912 / 140,424 | 10 / 49 / 3,832 | pre-cropped | no | none | CC BY-SA 4.0 | derived from CODH v1 |

Sources: https://codh.rois.ac.jp/char-shape/ · https://lab.hi.u-tokyo.ac.jp/datasets/kuzushiji ·
https://codh.rois.ac.jp/omt/dataset/ · https://huggingface.co/datasets/yuta1984/honkoku-lines ·
https://github.com/ndl-lab/ndl-minhon-ocrdataset · https://github.com/yuta1984/honkoku-data ·
https://cid.ninjal.ac.jp/hentaiganaDB/ · https://github.com/rois-codh/kmnist

Outside Japan, the largest character-level historical sets are MTHv2 (1,081,663 characters, research
use only, https://github.com/HCIILAB/MTHv2_Datasets_Release) and AI Hub's Joseon Hanja set
(10,142,816 characters, access limited to Korean entities, https://aihub.or.kr/aidata/30753). Among
the sets examined, none of that size is under a CC licence without NC or ND.

Hobbyist merges exist on Hugging Face (DimV-Ai/kuzushiji-character-dataset-v1 combines CODH with
28,862 annotated characters from two 小城藩 manuscripts; Kotomiya07 republishes CODH in COCO and YOLO
form). Their rights documentation is thin.

## 3. What is missing

- 字母 per character instance. CODH files every hentaigana under the modern kana code point
  (https://www.nijl.ac.jp/pages/cijproject/info/img/dataset/kuzushiji-specification.pdf, pp. 4–5),
  and the みんなで翻刻 guidelines ask transcribers to write modern kana and 当用漢字 forms
  (https://wiki.honkoku.org/doku.php?id=guidelines). The 古活字 dataset records 字母, for one
  movable-type work. The NINJAL database records it for a handful of printed books, with no
  stated licence.
- Kanji variants as written. Version 2 of the CODH dataset merged 旧字 into 新字 in the 常用漢字 range.
- Document metadata. CODH carries only the 書誌ID; the HI Lab zip carries nothing; Honkoku-Lines
  carries holder, licence and an item bibliography, without dating intervals or genre.
- Character-level data from crowd transcriptions. NDL (2024) and Honkoku-Lines (2026) reached line
  level. 橋本雄太 named the character-level step as open in 2022
  (https://current.ndl.go.jp/ca2015). None of the releases examined goes further.
- Per-record rights across sources, so that a mixed dataset can be filtered to what a user may
  redistribute.

## 4. Sources by role

Imported as they are, with identifiers kept:

- CODH くずし字データセット: coordinates CSV per book, crops addressable through CODH's IIIF server
  (`/char-shape/iiif/{bid}/{bid}_{page}_{half}.tif/{x},{y},{w},{h}/full/0/default.jpg`).
- CODH 古活字データセット: type blocks with 文字 and 字母, the seed for 字母 classification; some blocks are
  連彫活字 carrying two or three kana.
- 東京大学史料編纂所 くずし字データセット: 325,261 crops without page coordinates. The record fields
  (文書名, 和暦年月日, 史料群名 and others) exist in the 電子くずし字字典 database, whose search API
  refuses anonymous requests; a route to them has to come from the institute.

Line-level inputs for the alignment pipeline:

- Honkoku-Lines: line boxes, transcription with markup, IIIF region URL, holder and image licence,
  splits by item. 603,739 lines on PDM images and 414,772 on CC BY 4.0 images are bundled; the rest
  are metadata plus URL.
- NDL古典籍OCR学習用データセット: line boxes on NDL and other images, for pages Honkoku-Lines does not
  cover.
- みんなで翻刻データ v3 and the platform API (https://wiki.honkoku.org/doku.php?id=api) for pages that
  neither line dataset covers, and for the revision of each transcription.

Reference tables:

- Unicode `NamesList.txt`, whose `derived from` notes give the 字母 of all 287 hentaigana-type code
  points (285 HENTAIGANA LETTER, U+1B001, U+1B11F). Unicode License v3.
- MJ文字情報一覧表 変体仮名編 Ver.002.01: 299 rows, 286 with code points, with 字母, 音価,
  戸籍統一文字番号, 学術用変体仮名番号 and the NINJAL URL. CC BY-SA 2.1 JP.
  https://moji.or.jp/mojikiban/mjlist/
- MJ文字情報一覧表 Ver.006.02: 58,862 rows, 58,859 with a corresponding UCS code point (対応するUCS),
  5,072 code points held by more than one MJ figure. CC BY-SA 2.1 JP. https://moji.or.jp/mojikiban/mjlist/
- 学術情報交換用変体仮名 (NINJAL): one reference glyph per code point with dictionary cross-references.
  Images CC BY-SA 2.1 JP, data CC BY 4.0. https://cid.ninjal.ac.jp/kana/
- IVD 2026-08-03, Moji_Joho collection, 11,392 sequences, whose glyph ids are MJ文字図形名. Unicode
  License v3. https://www.unicode.org/ivd/

Japanese Wikisource is included in full discovery. Main-namespace works and scan-backed Page
records are collected through resumable, paced requests. Text-only works remain searchable text
evidence. Character extraction requires a traceable image and usable alignment; a collected text
never counts as a character crop. Honkoku discovery covers every listed book, with one-book-at-a-time
acquisition and periodic rediscovery. Extraction rotates across works to broaden coverage before
finishing every page of the pilot.

Outside the current acquisition scope:

- 早稲田大学古典籍総合データベース and 慶應義塾大学 digital collections: reuse needs permission, and
  Waseda names partial use that hides the original as grounds for refusal.
- 木簡庫 / MOJIZO and the 電子くずし字字典 record images: retrieval services under all-rights-reserved
  terms.
- CHISE IDS: GPL-2.0-or-later, which cannot sit inside a CC BY-SA dataset.

## 5. Labelling

Browsing has three levels: a curated grapheme family (仮 = 假), separate written characters (仮 and
假), and the exact forms visible in source occurrences. Grouping never changes a character label
or a saved correction. 字母, readings, and registry variant identifiers remain attached metadata.

Four layers, each fillable on its own:

1. Source: the transcriber's string for the unit, verbatim.
2. Reading: the diplomatic reading, historical spelling kept (けふ stays けふ).
3. Classification: code points, script, 字母, variant key.
4. Normalisation: modern kana, 新字, voicing supplied by an editor. Computed at export from the
   layers above and a named policy; never stored on the record.

Kana. The reading starts from the transcription, which is evidence and not the diplomatic reading:
a transcriber may have written い for a form that reads ゐ on the page, so the candidate set includes
the historical spellings that the source's normalisation maps onto the transcribed kana. Unicode's names list and the MJ table give the code points that share that
reading (か has twelve, KA-KE included). A classifier and, where reviewed, a person choose among them from the
image, and the 字母 follows from the code point. For the 52 (音価, 字母) pairs that Unicode split across
several code points, the record carries the code point once the form is identified and a local
shape id where a finer distinction is needed; a shape Unicode never encoded gets `unicode` null,
its 字母 where known, and a local shape id. Katakana keeps its own script value; a 字母 may still be
recorded, as for the 子-shaped ネ.

Kanji. The transcriber's string stays in the source layer (国 when the guidelines asked for 当用漢字).
The classification layer records the character as written: `unicode` U+570B when the page shows 國,
and, when the written form matches a registered glyph, the Moji_Joho IVS or MJ文字図形名 on its own
registered base; otherwise a local variant id or nothing. 旧字 and 新字 retain distinct identities in
the classification layer. Cited orthographic pairs share a browsing family, and export normalisation
remains a separate policy.

Marks and joins. Voicing marks are recorded as present or absent with their own rectangle. 踊り字
keep their mark and link to the repeated span. 合字 (ゟ, ヿ, 𬼂 U+2CF02 for なり) are one unit with a
multi-character expansion. Units joined by continuous strokes form a 連綿 group with its own
rectangle; unit rectangles may overlap.

Documents. Production (manuscript, woodblock, movable type), genre (`data/vocab/genre.yaml`), register
(和文, 漢文, 漢文訓読, 候文), script style (`data/vocab/style.yaml`), dating as written plus an
interval and its kind (composition, copying, publication, impression), holder, shelfmark, hands.

## 6. Pipeline

1. Ingest. Importers write the tables in `docs/schema.md` from each source's own format and keep
   the upstream identifiers. Rights are recorded at ingest from the holder's terms page.
2. Images. Full-size page images are fetched over IIIF once, checksummed and cached outside git.
3. Lines. Honkoku-Lines and the NDL dataset supply line boxes and text for most pages. For pages
   without them, NDL古典籍OCR-Lite (RTMDet + PARSeq) detects lines and the transcription is matched to
   them by normalised edit distance, as in Honkoku-Lines.
4. Characters. A class-agnostic character detector, trained on the 1,086,326 CODH boxes, the
   single-character 古活字 blocks (連彫活字 blocks hold several characters and are left out) and later
   on reviewed pilot pages, proposes boxes inside each line.
5. Alignment. The transcription line, markup parsed, is aligned to the ordered detections by dynamic
   programming. The score is a character classifier's probability of the transcribed character for
   the detection, computed over an equivalence class (hentaigana forms of the same kana, 旧字 and
   新字, iteration marks). Insertions and deletions are allowed; units below threshold are kept,
   with a low confidence and a rejected state.
6. 字母. For each kana unit the classifier scores the code points of its reading. Training data:
   the 古活字 blocks, the NINJAL reference glyphs, reviewed pilot units, and synthetic renderings
   from the NINJAL and Noto Serif Hentaigana fonts, which are never counted as evidence.
7. Review. Reviewers see a line in its page context and can move a box, split or merge units, set
   the reading and the 字母, or reject. Model disagreement and rare forms are queued first; a random
   audit of accepted units runs separately and gives the published precision.
8. Release. Parquet tables, crops for redistributable images, an attribution file generated from the
   rights fields, a datasheet, a Zenodo DOI. Counts are published by provenance: imported, aligned,
   reviewed.

Evaluation splits are by item, so that two scans of one print never straddle a split.

## 7. Milestones

- M0. Repository, schema, source registry, importer for one CODH book. First contact with 橋本雄太,
  CODH and the 史料編纂所 (section 9). Done on 2026-09-11 except the contacts.
- M1. Foundation modules; CODH importer over all 44 books; Honkoku-Lines lines and rights; the review
  service and interface. HI Lab and 古活字 imports and the other importers can run in parallel since
  they touch nothing else.
- M2. Character detection and alignment on 100 to 200 complete pages from PDM and CC BY 4.0 items, a
  calibration tranche of 10 to 20 pages first; review time measured; joint box-and-label precision,
  coverage and split or merge errors published with the pilot as a benchmark.
- M3. Release 0.1: at least 50,000 reviewed new characters from ten or more exemplars, a smaller
  expert-reviewed 字母 subset, rights evidence per record. The 字母 classifier is trained on this
  release and is not a condition for it.
- M4. Source by source expansion under measured precision and coverage thresholds, the HI Lab and
  古活字 imports, a public review service separate from bulk processing; release 1.0.

## 8. Risks

- Plausible labels on the wrong ink. Forced alignment produces convincing boxes even when a
  transcription line is wrong or the reading order differs. Whole pages in the pilot, a random
  audit of accepted units, and rejected units kept as records.
- Rights found wrong after release. IIIF availability says nothing about reuse. Rights are recorded
  at ingest with the evidence URL, and unresolved holders stay out of release builds.
- Annotation ambition beyond review capacity. At ten seconds a unit, one million units need about
  2,800 hours. Fields are independent, partial records are published, and expert review is scoped
  to the 字母 subset and to disagreements.
- Duplicate ancestry. KMNIST, the Kaggle set and several Hugging Face uploads all derive from CODH;
  imports keep upstream identifiers so that a page is never counted twice.

## 9. To do outside the code

- Ask 東京大学史料編纂所 for the record table behind the HI Lab crops, or for coordinates.
- Ask 国語研 about the licence of the 変体仮名字形データベース and bulk access.
- Ask 京都大学附属図書館 whether a bulk crop package under its reuse terms is within what the terms
  intend.
- Read the ColBase terms in a browser; the page is a client-rendered application.
- Tell 橋本雄太 about the project at the start; Honkoku-Lines and the platform are the main
  upstream. Offer a character-level extension keyed to Honkoku-Lines line ids and reviewed
  alignment corrections. Ask about revision ids and the preferred correction format.
