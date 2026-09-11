# Schema

Tables are Parquet files under `out/<release>/`; editorial logs are JSON Lines. The Pydantic models in
`src/kuzushiji_atlas/schema.py` are the reference; this page explains the fields.

## Identifiers

Ids are strings, assigned once. Imported records take a deterministic id from the upstream identity,
so that re-importing a source changes nothing (CODH: `codh:{bid}:{image}:{block}:{char}`). Units that
the alignment pipeline creates take `{line_id}:{run}:{seq}`, where `run` is a short hash of the model
versions and the configuration, so a rerun with the same inputs produces the same ids. Units created
by a reviewer take `{line_id}:m{n}` with `n` counting up per line. When a unit is re-segmented, the
old record stays with `active` false and `split_into` or `merged_into` filled; its review state is
left as it was, since retirement says nothing about whether its labels were wrong.

## Tables

### sources

One row per upstream, from `data/sources/*.yaml`: `id`, `name`, `publisher`, `kind`, `url`, `licence`,
`attribution`, `version`, `doi`, `released`.

### documents

One physical exemplar (a copy, a manuscript, an archival document).

| Field | Meaning |
| --- | --- |
| `id`, `title` | |
| `source_refs` | upstream ids by source: NIJL 書誌ID, NDL PID, みんなで翻刻 entry id, HI record id |
| `holder`, `shelfmark` | holding institution and its call number |
| `production` | `manuscript`, `woodblock`, `movable-type`, `mixed`, `unknown` |
| `genre` | one or more ids from `data/vocab/genre.yaml` |
| `text_register` | `wabun`, `kanbun`, `kanbun-kundoku`, `sorobun`, `mixed`, `unknown` |
| `dating` | list; each with `literal` as written (文政3), `start` and `end` years, `kind` (composition, copying, publication, impression), `evidence` |
| `hands` | free-text notes on scribes where known |
| `image_rights`, `text_rights` | licence, holder, attribution string, evidence URL, date checked |

### pages

| Field | Meaning |
| --- | --- |
| `id`, `document_id`, `seq` | |
| `canvas` | IIIF canvas id |
| `image` | IIIF image service base, or the URL of the full-size image |
| `width`, `height`, `sha256` | of the full-size image as fetched |
| `transcription` | source id, entry id and revision of the text used |

Coordinates of lines and units are pixels on this image. A page that is one half of a photographed
spread is still one page; the spread relation is recorded through `canvas`.

### lines

| Field | Meaning |
| --- | --- |
| `id`, `page_id`, `seq` | `seq` follows reading order |
| `box`, `vertical` | |
| `role` | `main`, `ruby`, `warigaki`, `note`, `marginal`, `title`, `other` |
| `text_raw` | the transcription line as written, markup included |
| `text` | plain text after markup removal |
| `match_method`, `match_confidence` | how the box was assigned to the text; the confidence is a similarity score in [0, 1], never a calibrated probability |
| `meta` | upstream fields with no column of their own (a split label, a detector score) |

### units

One graphic unit: a character, a ligature, a mark, a gap.

| Field | Meaning |
| --- | --- |
| `id`, `document_id`, `page_id`, `line_id`, `seq` | `document_id` is always set; `page_id` is null for a standalone crop |
| `box` | rectangle on the page image; null for a standalone crop |
| `crop`, `crop_sha256` | URL or archive path of a standalone crop image, and its checksum |
| `granularity` | `char`, `sequence` (an unresolved run), `block` (a type block holding several characters) |
| `kind` | `char`, `ligature`, `iteration-mark`, `voicing-mark`, `punctuation`, `gap`, `unreadable` |
| `text_source` | the transcriber's string for this unit |
| `reading` | diplomatic reading, historical spelling kept |
| `unicode` | code point sequence, `U+1B002` or `U+304B U+3099`; null when no code point fits |
| `classification` | `unassessed`, `identified`, `ambiguous` (several candidates remain), `unencoded` (identified, no code point exists), `unidentified` |
| `script` | `hiragana`, `hentaigana`, `katakana`, `kanji`, `symbol`, `latin`, `unknown` |
| `jibo` | 字母 as one kanji |
| `variants` | list of `{scheme, id, version}` with scheme `mj`, `ivs`, `glyphwiki` or `local`; several may apply |
| `candidates` | scored alternatives `{unicode, p, jibo}` when `classification` is `ambiguous` |
| `antecedent_ids` | for an iteration mark, the units it repeats, across a line break if needed |
| `group_id` | 連綿 group |
| `voicing` | mark present on the page: `none`, `dakuten`, `handakuten` |
| `method` | `import`, `detect-align`, `manual` |
| `confidence` | `detection`, `segmentation`, `text`, `jibo`, and the model that produced them |
| `review` | `machine`, `transcriber`, `reviewed`, `double-reviewed`, `adjudicated`, `disputed`, `rejected` |
| `upstream` | source id and upstream identifier |
| `active` | false once a split or merge retired the unit |
| `split_into`, `merged_into` | segmentation history |
| `meta` | fields with no column of their own, such as the audit sample the unit belongs to (`sample`, `p`, `stratum`, `predicted`, `hidden`) |

`unicode` and `reading` answer different questions. A hentaigana form of か derived from 可 has
`reading` か, `unicode` U+1B019 (KA-3), `jibo` 可, `script` hentaigana. U+1B01A (KA-4) derives from 可
as well, so a record of this pair also carries a local shape id in `variants`. The modern spelling is
derived at export.

### page_texts

Whole-page transcriptions for pages without line records: `page_id`, `source`, `revision`,
`text_raw`.

### groups

連綿 groups: `id`, `page_id`, `box`, `unit_ids`.

### reviews

Append-only log: `id`, `target_type`, `target_id`, `field`, `old`, `new` (JSON values, so a box or a
list can be recorded), `role` (model, transcriber, reviewer, adjudicator), `actor`, `evidence`, `at`.
When several reviewers work at once, the log is written by one service and exported; clients never
append to a shared file.

## Vocabularies

`data/vocab/genre.yaml`, `data/vocab/style.yaml` and `data/vocab/holders.yaml` hold the controlled
values with Japanese labels. `data/vocab/hentaigana.tsv` lists every hentaigana code point with its
readings and 字母, generated from Unicode's NamesList.txt by `scripts/build_hentaigana_table.py`. A new value enters through a pull request that states its source.

## Exports

`atlas export` writes a release directory. Options select the licence set (`--licence CC-BY-SA-4.0`
keeps records whose image and text rights compose into that licence), the review states, and whether
crops are materialised. Every release directory holds `ATTRIBUTION.md`, generated from the rights
fields of the records it contains.
