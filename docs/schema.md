# Schema

Tables are Parquet files under `out/<release>/`; editorial logs are JSON Lines. The Pydantic models in
`src/kuzushiji_atlas/schema.py` are the reference; this page explains the fields.

## Identifiers

Ids are opaque strings, assigned once. Imported records take a deterministic id from the upstream
identity, so that re-importing a source changes nothing (CODH: `codh:{bid}:{image}:{block}:{char}`).
Records created by the pipeline take a random id. When a unit is re-segmented, the old record stays
with `split_into` or `merged_into` filled and its review state set to `rejected`.

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
| `dating` | `literal` as written (文政3), `start` and `end` years, `kind` (composition, copying, publication, impression), `evidence` |
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
| `match_method`, `match_confidence` | how the box was assigned to the text |

### units

One graphic unit: a character, a ligature, a mark, a gap.

| Field | Meaning |
| --- | --- |
| `id`, `page_id`, `line_id`, `seq` | |
| `box` | rectangle on the page image |
| `kind` | `char`, `ligature`, `iteration-mark`, `voicing-mark`, `punctuation`, `gap`, `unreadable` |
| `text_source` | the transcriber's string for this unit |
| `reading` | diplomatic reading, historical spelling kept |
| `unicode` | code point sequence, `U+1B002` or `U+304B U+3099`; null when no code point fits |
| `script` | `hiragana`, `hentaigana`, `katakana`, `kanji`, `symbol`, `latin`, `unknown` |
| `jibo` | 字母 as one kanji |
| `variant` | `{scheme, id}` with scheme `mj`, `ivs`, `glyphwiki` or `local` |
| `group_id` | 連綿 group |
| `voicing` | mark present on the page: `none`, `dakuten`, `handakuten` |
| `method` | `import`, `detect-align`, `manual` |
| `confidence` | `detection`, `segmentation`, `text`, `jibo`, and the model that produced them |
| `review` | `machine`, `transcriber`, `reviewed`, `double-reviewed`, `adjudicated`, `disputed`, `rejected` |
| `upstream` | source id and upstream identifier |
| `split_into`, `merged_into` | segmentation history |

`unicode` and `reading` answer different questions. A hentaigana form of か derived from 可 has
`reading` か, `unicode` U+1B045, `jibo` 可, `script` hentaigana. Its modern spelling is derived at
export.

### groups

連綿 groups: `id`, `page_id`, `box`, `unit_ids`.

### reviews

Append-only log: `unit_id`, `field`, `old`, `new`, `role` (model, transcriber, reviewer, adjudicator),
`evidence`, `at`.

## Vocabularies

`data/vocab/genre.yaml`, `data/vocab/style.yaml` and `data/vocab/holders.yaml` hold the controlled
values with Japanese labels. A new value is added by a pull request that also states its source.

## Exports

`atlas export` writes a release directory. Options select the licence set (`--licence CC-BY-SA-4.0`
keeps records whose image and text rights compose into that licence), the review states, and whether
crops are materialised. Every release directory holds `ATTRIBUTION.md`, generated from the rights
fields of the records it contains.
