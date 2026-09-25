# Schema

Tables are Parquet files under `out/<release>/`; editorial logs are JSON Lines. The Pydantic models in
`src/glyph_atlas/schema.py` are the reference; this page explains the fields.

## Three layers

The hierarchy is grapheme → character → form.

| Layer | What it is | Where it lives |
| --- | --- | --- |
| Grapheme | A curated family, such as 仮 and 假 | `Character.grapheme` points to its representative |
| Character | The written identity and its code point | `data/vocab/characters.tsv`, one row per code point |
| Form | Exact ink in a source occurrence | `Unit`, its crop, placement and optional variant identifiers |

仮 (`U+4EEE`) and 假 (`U+5047`) share a family represented by 仮. Their individual
characters and occurrence counts remain separate. The 271 supported one-to-one
Japanese old/new pairs are cited in `graphemes.yaml`; broader alignment equivalences
and shared pronunciations do not create browsing families.

Kana retain their curated families. For example, ね, ネ and 𛄧 share `U+306D`.
The kanji 子 remains a separate character and family, while `Character.jibo` records
its derivation relation to 𛄧. A shared 字母 alone does not establish a family.
Small kana and ligatures retain distinct families.

A unit's `unicode` identifies the written character; `reading` preserves its independent
reading. `variants` can identify an MJ, IVS, GlyphWiki or local form. Grouping a character
never rewrites these fields or the source transcription.

`/layers/graphemes/{code_point}` resolves either member to its family and lists
`characters`. Each character can be selected for exact occurrences; an explicit
`expand=grapheme` query returns occurrences across the family.

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
| `production` | a node of `data/vocab/production.yaml` written as its path, e.g. `handwritten`, `printed/woodblock`, `printed/type/metal/copper`; the deepest node the evidence states, with `data/vocab/production-overrides.yaml` citing more than a source says |
| `genre` | one or more ids from `data/vocab/genre.yaml` |
| `text_register` | `wabun`, `kanbun`, `kanbun-kundoku`, `sorobun`, `mixed`, `unknown` |
| `dating` | list; each with `literal` as written (文政3), `start` and `end` years, `kind` (composition, copying, publication, impression), `evidence` |
| `hands` | free-text notes on scribes where known |
| `image_rights`, `text_rights` | licence, holder, attribution string, evidence URL, date checked; `holder_terms` keeps the holder's own statement where the images of a public-domain work are recorded as PD (`docs/licensing.md`) |

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

One located unit: a character, a ligature, a mark, a gap, or a sequence awaiting segmentation.

| Field | Meaning |
| --- | --- |
| `id`, `document_id`, `page_id`, `line_id`, `seq` | `document_id` is always set; `page_id` is null for a standalone crop |
| `box` | rectangle on the page image; null for a standalone crop |
| `crop`, `crop_sha256` | URL or archive path of a standalone crop image, and its checksum |
| `granularity` | `char`, `sequence` (an unresolved run), `block` (a type block holding several characters) |
| `kind` | `char`, `sequence`, `ligature`, `iteration-mark`, `voicing-mark`, `punctuation`, `gap`, `unreadable` |
| `text_source` | the transcriber's string for this unit |
| `reading` | diplomatic reading, historical spelling kept |
| `unicode` | code point sequence, `U+1B002` or `U+304B U+3099`; null when no code point fits |
| `classification` | `unassessed`, `identified`, `ambiguous` (several candidates remain), `unencoded` (identified, no code point exists), `unidentified` |
| `script` | `hiragana`, `hentaigana`, `katakana`, `han`, `hangul`, `symbol`, `latin`, `unknown`; the character layer is the authority |
| `variants` | list of `{scheme, id, version}` with scheme `mj`, `ivs`, `glyphwiki` or `local`; several may apply |
| `candidates` | scored alternatives `{unicode, p}` when `classification` is `ambiguous` |
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
`reading` か, `unicode` U+1B019 (KA-3), and 字母 可 in the character layer, `script` hentaigana.
U+1B01A (KA-4) derives from 可 as well, so a record of this pair also carries a local shape id in
`variants`. The modern spelling is derived at export.

### characters

One encoded character, from `data/vocab/characters.tsv`: the middle layer, and the only table of the
dataset that is not about an occurrence of anything. Built by `scripts/build_character_table.py`
from one Unicode release's `UnicodeData.txt`, `Blocks.txt`, `Scripts.txt`, `DerivedAge.txt`,
`NamesList.txt`, `Jamo.txt` and `confusables.txt`, with `data/vocab/mj-hentaigana.tsv` and
`data/vocab/graphemes.yaml` for the parts Unicode states for kana forms only.

| Field | Meaning |
| --- | --- |
| `code_point`, `char` | `U+1B127`, 𛄧; the code point is the identity, so the table needs no id of its own |
| `name`, `alias` | the Unicode name; a second name such as the MJ figure name, where it differs |
| `script` | the script property, with hentaigana named as this project names it and `Han` written `han` |
| `category`, `age`, `block` | the general category, the release that assigned the code point, the block |
| `jibo` | 字母: the kanji the form derives from; empty for a kanji, and for a kana whose derivation no source states |
| `readings` | what the character reads as, historical spelling kept; empty when it is not a kana; a Hangul compatibility jamo reads as itself, a syllable or conjoining jamo has none |
| `grapheme` | the representative of its curated family; its own code point when no family is stated |
| `confusables` | the characters Unicode's confusables table pairs with this one, both directions |
| `variants` | as on a unit, when a shape registry has an id for the character |

The table covers every kana of the kana blocks and every CJK unified ideograph, because the ideographs
are the 字母 the kana point at and the characters a source text is written in. It does not cover the
kana of Enclosed CJK Letters and Months (㋕ and the like), which are enclosed forms rather than text,
nor Kana Extended-B (the tone marks of Taiwanese kana), and a code point it does not hold is `None`
rather than an error.

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
values with Japanese labels. `refs.forms(reading)` is every character written for a reading, from
the layer, and `refs.candidates(reading)` is the same list ordered for a classifier and for a
reviewer: the modern kana first, then the hentaigana in code point order, then the katakana. The
last group is where the Unicode 18.0 letters fall, so a mask over `candidates("ね")` can score the
alternate NE.
`data/vocab/characters.tsv` is the character layer above, generated
from one Unicode release by `scripts/build_character_table.py`; `data/vocab/hentaigana.tsv` lists
the kana of Kana Supplement and Kana Extended-A with their readings and 字母, generated from
Unicode's NamesList.txt by `scripts/build_hentaigana_table.py`, and `data/vocab/mj-hentaigana.tsv`
carries the MJ figure, 戸籍統一文字番号 and 学術用変体仮名番号 of the same code points.
`data/vocab/mj-kanji.tsv`, generated by `scripts/build_mj_kanji_table.py` from the MJ文字情報一覧表,
carries every MJ figure of the kanji list by the UCS code point it corresponds to (対応するUCS). A
character's `variants` lists an `mj` entry for every figure either table gives its code point,
several where the kanji table has several, each with the version in its table's first header line. `data/vocab/graphemes.yaml` states the curated kana assignments, 字母
overrides and cited orthographic families. A new value must state its source.

Refresh the cached Unicode files before rebuilding the character table; they live in `cache/ucd/`
and are not in git.

```sh
for f in UnicodeData.txt Blocks.txt Scripts.txt DerivedAge.txt NamesList.txt Jamo.txt; do
  curl -o cache/ucd/$f https://www.unicode.org/Public/18.0.0/ucd/$f
done
curl -o cache/ucd/confusables.txt https://www.unicode.org/Public/security/latest/confusables.txt
uv run python scripts/build_character_table.py cache/ucd
```

## Exports

`atlas export` writes a release directory. Options select the licence set (`--licence CC-BY-SA-4.0`
keeps records whose image and text rights compose into that licence), the review states, and whether
crops are materialised. Every release directory holds `ATTRIBUTION.md`, generated from the rights
fields of the records it contains.
