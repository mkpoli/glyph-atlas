# Datasheet template

The sections below follow the datasheet for datasets. Every value the text needs is written as a
placeholder: a name between double braces, as in the table at the end. `atlas export` fills every
placeholder from `COUNTS.md`, `MANIFEST.json` and the source registry `data/sources/*.yaml`, and
writes the result to `out/<version>/datasheet.md`; an unfilled placeholder fails the export. The
table at the end names every value a filled datasheet requires, the placeholder that carries it and
the file it comes from.

## Motivation

### Purpose

Glyph Atlas (字形大圖譜) is a character-shape dataset of pre-modern writing across the Sinosphere:
Chinese characters and the scripts written with them in China, Korea, Japan and Vietnam. One record is one
graphic occurrence, in most cases one written character on a page image: its rectangle, the
transcriber's string, a diplomatic reading, the Unicode code points including hentaigana, the 字母 of
kana forms, a variant key for kanji, and the document's production type, genre, register and date.

The dataset is built for training and evaluating character detection, segmentation and
classification on pre-modern Japanese material, and for reading the 字母 and code point of a kana
form and the variant key of a kanji as written. It records the 字母 and code point of each kana form,
kanji variants as written, and bibliographic metadata for filtering by period, genre and script
style.

### Creator, maintainer and funding

The dataset is created and maintained by mkpoli at https://github.com/mkpoli/glyph-atlas. The
annotations and the compilation are released under CC BY-SA 4.0; the code under MIT. Funding is not
recorded in the release metadata.

## Composition

### Instances

One record is one graphic unit: a character, a ligature, a mark, a gap or an unresolved run, with the
granularity stated on the record (`char`, `sequence` or `block`). A unit on a page carries the
rectangle on the full-size image, the line and the neighbouring units, and the document's
bibliographic fields; a standalone crop carries its archive path and checksum, with `page_id` null
and no rectangle.

The release holds {{counts.units}} units in {{counts.lines}} lines, {{counts.pages}} pages and
{{counts.documents}} documents. Units by upstream source: {{counts.units_by_source}}.

### Labels

Units by script (`hiragana`, `hentaigana`, `katakana`, `kanji`, `symbol`, `latin`, `unknown`):
{{counts.units_by_script}}. Units by classification state (`unassessed`, `identified`, `ambiguous`,
`unencoded`, `unidentified`): {{counts.units_by_classification}}.

### Rights carried on a record

Every record carries the licence of its image and of its text, their holders, an attribution string
and the URL of the page that states the terms. Units by image licence and by text licence:
{{counts.units_by_rights}}. A document whose text is eligible and whose image is not keeps its
records with coordinates and receives no crops.

### Missing information

Fields are filled independently, so partial records are expected. Units carrying a `reading`, a
`unicode` value, a 字母 resolved from the character layer and a `variants` entry:
{{counts.units_by_label_coverage}}. A unit whose form is identified and has no code point keeps
`unicode` null and a local shape id; the 字母 follows from the code point, so it is present exactly
when `unicode` names a kana the character layer has one for. Documents by production type, genre and
dating coverage: {{counts.documents_by_metadata}}.

### Splits

Lines and units by evaluation split: {{counts.splits}}. Splits are assigned by item, so two scans of
one print fall in the same split. `data/pilot/items.tsv` names the pilot items with their groups,
holders and production types.

### Sensitive content

The material is published and archival writing from open collections. Records reproduce historical
text as written, with a diplomatic reading and document metadata; the dataset adds no modernisation.

## Collection process

### Acquisition

Records are imported from the upstreams registered in `data/sources/`. The sources present in this
release, with version, release date, licence, DOI and attribution string: {{sources.table}}. The
imports keep the upstream identifiers, so a page is not counted twice across sources.

Upstream inputs are pinned by checksum, revision or commit. The input datasets of this build, with
their row counts and file checksums: {{release.inputs}}.

### Sampling

Pages and lines come from the upstream datasets. Character units come from the upstream character
datasets and from the detection and alignment pipeline run over those pages. The pages the pipeline
is measured on are the pilot items in `data/pilot/items.tsv`: about ten items whose image licence is
PDM 1.0 or CC BY 4.0, split into calibration and held-out pages. The release is not a sample of the
upstream datasets; it covers the records its filters admit.

### Human review

Units by review state: {{counts.units_by_review}}. Review decisions are recorded in the append-only
`reviews.jsonl` log with the actor, the field, the old value, the new value and the evidence. Two
reviewers annotated the calibration pages and a third adjudicated the disagreements; pages, agreement
before adjudication, minutes per page and disagreement categories are reported in the pilot
calibration report. A blind random sample of accepted machine units gives the published precision,
reported in the audit report for the sample.

## Preprocessing, cleaning and labelling

### Layers

Four layers are recorded, each fillable on its own:

| Layer | Fields | Content |
| --- | --- | --- |
| Source | `text_source` | the transcriber's string for the unit, verbatim |
| Reading | `reading` | the diplomatic reading, historical spelling kept (けふ stays けふ) |
| Classification | `unicode`, `script`, `variants` | the code point, the script, the variant key |
| Normalisation | derived columns | modern kana and 新字, computed at export under a named policy |

The 字母 is not a field of a unit, because it is not a property of an occurrence: it belongs to the
character, and `data/vocab/characters.tsv` states it once per code point. A unit reaches it through
`unicode`. The same table states which characters are forms of one grapheme, so a search for ね
reaches ネ, the 変体仮名 of 年 and root, heat and 禰, and 𛄧, the alternate katakana Unicode 18.0
added. Three layers answer three questions: a **grapheme** is one shape as the writing system
distinguishes shapes, a **character** is one encoded identity, and the **字母** is what a form
derives from. 𛄧, ネ and 子 are one confusable shape and three characters, and a record that stored
子 for the first would no longer say which of them a source printed.

The transcription keeps its Koji markup in `lines.text_raw` and is stripped into `lines.text` for
matching and display.

### Labelling

The reading starts from the transcription. For a kana unit the classifier scores the code points of
that reading and the 字母 then follows from the chosen code point. Kanji are recorded as written, so 旧字
and 新字 stay apart in the classification layer. Voicing marks are recorded as present or absent with
their own rectangle, iteration marks link to the units they repeat, and units joined by continuous
strokes carry a 連綿 group id. A label is machine-assigned (`method=detect-align`) or set by a person
(`method=manual`). Units by method: {{counts.units_by_method}}.

### Derived columns

The normalisation policy {{release.normalisation_policy}} adds the derived columns at export:
`modern_kana` from the hentaigana table and `shinji` from the `shinji-kyuji` rows of the equivalence
table. The record fields keep the values they were given; the derived columns change with the
policy, and the policy name and version are written into `MANIFEST.json`.

### Cleaning and exclusions

The build admits the records its filters select: {{release.filters}}. Units with a rejected review
state and documents whose terms do not compose into the dataset licence are absent from the release.
Imported records keep the upstream identifiers and the upstream values.

### Quality

`lines.match_confidence` is a similarity score in [0, 1] for the box-to-text match. Accepted units
that no person has reviewed carry `review=machine` and are included when the build asks for machine
units. Precision of the accepted units is measured on a blind random sample and reported in the audit
report for the sample.

## Uses

### Tasks the dataset supports

- Training and evaluating character detection, segmentation and classification on pre-modern
  Japanese page images.
- Reading a kana form's 字母 and code point, and a kanji's variant key as written.
- Selecting a subset by period, genre, register, script style, holder or licence.
- Retrieving crops where the image licence allows redistribution and coordinates elsewhere.

### Limitations and risks

- A box can sit on the wrong ink: forced alignment places a box on a detected region when a
  transcription line is wrong or the reading order differs. The measured precision of the machine
  units is in the audit report for the sample.
- The rights of a record are those recorded at ingest from the holder's terms page. IIIF
  availability carries no reuse permission, and a user redistributing crops takes the licence
  carried on each record.
- The derived normalisations follow one named policy and differ between policies.
- Document metadata is absent where the upstream carries none, which limits filtering for those
  records.
- `match_confidence` is a similarity score; it is not a calibrated probability.

## Distribution

### Files

`out/<version>/` holds the release:

- `documents.parquet`, `pages.parquet`, `lines.parquet`, `units.parquet`, `groups.parquet` and
  `page_texts.parquet`, with `units` and `lines` sharded by document.
- `crops/<bucket>/<id with ':' replaced by '_'>.jpg`: the materialised crops, {{counts.crops}}.
- `ATTRIBUTION.md`, `COUNTS.md`, `datasheet.md`, `CHECKSUMS.txt` and `MANIFEST.json`.

The build command is recorded in `MANIFEST.json` and reproduced here:

```
{{release.command}}
```

### Licence and attribution

The dataset licence is CC BY-SA 4.0 (`LICENSE-DATA`); it covers the annotations and the compilation
made here. The code is MIT (`LICENSE`). Upstream images and texts keep their own terms, carried on
each record and registered in `data/sources/`: {{sources.licences}}. `ATTRIBUTION.md` gives the
attribution string per source and per holder, and credits contributors under the name they chose.

### Version and identifier

Release {{release.version}}, built {{release.date}}. Each version is deposited on Zenodo and has its
own DOI; the concept DOI covers the versions and is the identifier in `CITATION.cff`. A deposited
version is immutable, and a later version supersedes it.

## Maintenance

### Contact

Maintainer: mkpoli. Errata, correction requests and withdrawal requests go to
https://github.com/mkpoli/glyph-atlas/issues.

### Corrections and withdrawal

A holding institution, a transcriber or a contributor who finds a record that misstates rights,
misattributes a source, or reproduces material they did not license may open an issue or write to
the maintainer. Records named in a request are removed from the next release build while the request
is checked, and the release notes list what was removed and why.

### Updates

A new version is a new directory under `out/` with its own `MANIFEST.json` and `CHECKSUMS.txt`; the
tables of a released version do not change. The upstream pins of each version are recorded in
`MANIFEST.json`, and the source registry carries the pin of each upstream.

## Placeholders

| Placeholder | Value | Filled from |
| --- | --- | --- |
| `{{release.version}}` | release version string | `MANIFEST.json` |
| `{{release.date}}` | build date | `MANIFEST.json` |
| `{{release.command}}` | export command line | `MANIFEST.json` |
| `{{release.filters}}` | licence set, review states and crop mode of the build | `MANIFEST.json` |
| `{{release.normalisation_policy}}` | normalisation policy name and version | `MANIFEST.json` |
| `{{release.inputs}}` | input datasets with row counts and file checksums | `MANIFEST.json` |
| `{{sources.table}}` | one row per source in the release: id, name, publisher, version, release date, licence, DOI, pin, attribution string | `data/sources/*.yaml` |
| `{{sources.licences}}` | the upstream licences and holders the records carry, with the evidence URLs | `data/sources/*.yaml` |
| `{{counts.documents}}` | documents | `COUNTS.md` |
| `{{counts.pages}}` | pages | `COUNTS.md` |
| `{{counts.lines}}` | lines | `COUNTS.md` |
| `{{counts.units}}` | units | `COUNTS.md` |
| `{{counts.units_by_source}}` | units by upstream source | `COUNTS.md` |
| `{{counts.units_by_method}}` | units by `method` (`import`, `detect-align`, `detect`, `manual`) | `COUNTS.md` |
| `{{counts.units_by_review}}` | units by `review` state | `COUNTS.md` |
| `{{counts.units_by_script}}` | units by `script` | `COUNTS.md` |
| `{{counts.units_by_classification}}` | units by `classification` | `COUNTS.md` |
| `{{counts.units_by_label_coverage}}` | units carrying `reading`, `unicode`, a 字母 from the character layer and `variants` | `COUNTS.md` |
| `{{counts.units_by_rights}}` | units by image licence and by text licence | `COUNTS.md` |
| `{{counts.documents_by_metadata}}` | documents by production type, genre and dating coverage | `COUNTS.md` |
| `{{counts.crops}}` | materialised crops by bucket | `COUNTS.md` |
| `{{counts.splits}}` | lines and units by evaluation split | `COUNTS.md` |
