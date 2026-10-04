# Schema

Tables are Parquet files under `out/<release>/`; editorial logs are JSON Lines. The Pydantic models in
`src/glyph_atlas/schema.py` are the reference; this page explains the fields.

## Three layers

The hierarchy is grapheme → character → form.

| Layer | What it is | Where it lives |
| --- | --- | --- |
| Grapheme | A curated family, such as 仮 and 假 | `Character.grapheme` points to its representative |
| Character | The written identity and its code point | `data/vocab/characters.tsv`, one row per code point |
| Form | A category of written appearance a crop is written in | the assertion ledger: a crop's `has_form` claim names a form, and a form's representations name it ([docs/design/form-model.md](design/form-model.md)) |

仮 (`U+4EEE`) and 假 (`U+5047`) share a family represented by 仮. Their individual
characters and occurrence counts remain separate. The 271 supported one-to-one
Japanese old/new pairs are cited in `graphemes.yaml`; broader alignment equivalences
and shared pronunciations do not create browsing families.

Kana retain their curated families. For example, ね, ネ and 𛄧 share `U+306D`.
The kanji 子 remains a separate character and family, while `Character.jibo` records
its derivation relation to 𛄧. A shared 字母 alone does not establish a family.
Small kana and ligatures retain distinct families.

A unit's `unicode` identifies the written character. The form its ink is written in is a claim of
the assertion ledger, and an MJ id, an IVS, a GlyphWiki name or an IDS is a representation of that
form. Grouping a character never rewrites these fields or the source transcription.

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
| `origin` | where the exemplar was made, from `data/vocab/origin.yaml`: `china`, `korea`, `japan`, `vietnam`, `other` or `unknown`; the place its letterforms were written, printed or cut, named by present-day territory, which is neither the language of its text nor the place that holds it. An importer sets it where its source states it: CODH, HI Lab, 古活字 and the Ainu records are `japan`, the 국립한글박물관 and 국가유산청 collections `korea`, and HNG takes each source's category (a Chinese period, 日本 or 韓国; 大和寧 stays `unknown`) |
| `production` | a node of `data/vocab/production.yaml` written as its path, e.g. `handwritten`, `printed/woodblock`, `printed/type/metal/copper`; the deepest node the evidence states, with `data/vocab/production-overrides.yaml` citing more than a source says |
| `style` | style of the letterforms throughout, a value of `data/vocab/style.yaml`; `mixed` when pages differ, `unassessed` until someone judges it |
| `genre` | one or more ids from `data/vocab/genre.yaml` |
| `text_register` | `wabun`, `kanbun`, `kanbun-kundoku`, `sorobun`, `mixed`, `unknown` |
| `dating` | what the corpus's own source states, as its importer read it: each with `literal` as written (文政3), `start` and `end` years, `kind` (composition, copying, publication, impression), `evidence`. Every dated statement about a document, this one and those of other sources, becomes a claim in `dates` below |
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
| `style` | style of the letterforms, a value of `data/vocab/style.yaml` such as `regular`, `cursive` or `ming`; `unassessed` takes the document's |

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

Boxes a reviewer draws on a page photo go on one line per page with role `other`, `meta.scope`
`page` and the whole page as its box, created by the first box drawn on that page.
`atlas review propose-marks` puts the interlinear marks and circles it proposes on the same line, as
units with `method` `detect`, `review` `machine`, no character, and `meta.proposer` naming the
proposer's version. A reviewer names, keeps or removes each one.

### units

One located unit: a character, a ligature, a mark, a gap, or a sequence awaiting segmentation.

| Field | Meaning |
| --- | --- |
| `id`, `document_id`, `page_id`, `line_id`, `seq` | `document_id` is always set; `page_id` is null for a standalone crop |
| `box` | rectangle on the page image; null for a standalone crop |
| `crop`, `crop_sha256` | URL or archive path of a standalone crop image, and its checksum |
| `granularity` | `char`, `sequence` (an unresolved run), `block` (a type block holding several characters) |
| `kind` | `char`, `sequence`, `ligature`, `iteration-mark`, `voicing-mark`, `punctuation`, `gap`, `unreadable` |
| `text_source` | the transcriber's string for this unit. When it writes a kana in a different script from the unit's character (`unicode`), such as ツ for つ, no other field of the unit records that choice; the form model ([#501](https://github.com/mkpoli/glyph-atlas/pull/501)) is to take it as a form proposal from the transcription |
| `unicode` | code point sequence, `U+1B002` or `U+304B U+3099`; null when no code point fits |
| `classification` | `unassessed`, `identified`, `ambiguous` (several candidates remain), `unencoded` (identified, no code point exists), `unidentified` |
| `script` | `hiragana`, `hentaigana`, `katakana`, `han`, `hangul`, `gugyeol`, `symbol`, `latin`, `unknown`; the character layer is the authority |
| `style` | style of this unit's letterforms, a value of `data/vocab/style.yaml`. `unassessed` takes the page's, then the document's; a `mixed` page or document passes nothing down, and the unit stays unassessed |
| `candidates` | scored alternatives `{unicode, p}` when `classification` is `ambiguous` |
| `antecedent_ids` | for an iteration mark, the units it repeats, across a line break if needed |
| `group_id` | 連綿 group |
| `voicing` | mark present on the page: `none`, `dakuten`, `handakuten` |
| `method` | `import`; `detect-align`, a detected box aligned to the transcription; `detect`, a box a detector proposed with no text aligned to it; `manual` |
| `confidence` | `detection`, `segmentation`, `text`, `jibo`, and the model that produced them |
| `review` | `machine`, `transcriber`, `reviewed`, `double-reviewed`, `adjudicated`, `disputed`, `rejected` |
| `upstream` | source id and upstream identifier |
| `active` | false once a split or merge retired the unit |
| `split_into`, `merged_into` | segmentation history |
| `meta` | fields with no column of their own, such as the audit sample the unit belongs to (`sample`, `p`, `stratum`, `predicted`, `hidden`) |

A hentaigana form of か derived from 可 has `unicode` U+1B019 (KA-3), `script` hentaigana, and, in
the character layer, 字母 可 and the kana か in `readings`.
U+1B01A (KA-4) derives from 可 as well; which shape a crop of the pair shows is its form claim. The
modern spelling is derived at export.

Schema version 5 removed `written_form` and `variants` from the unit: a table or a review store
that still holds them reads as without them while they are empty, and refuses a value, which
`scripts/migrate_written_forms.py` moves into the ledger.

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
| `jibo` | 字母: the kanji the form derives from; empty for a kanji, and for a kana or 구결자 whose derivation no source states |
| `readings` | what the character reads as, historical spelling kept; empty when it is not a kana or a 구결자; a Hangul compatibility jamo reads as itself, a syllable or conjoining jamo has none |
| `grapheme` | the representative of its curated family; its own code point when no family is stated |
| `confusables` | the characters Unicode's confusables table pairs with this one, both directions |
| `variants` | list of `{scheme, id, version}` with scheme `mj`, `ivs`, `glyphwiki` or `local`, when a shape registry has an id for the character |

The table covers every kana of the kana blocks and every CJK unified ideograph, because the ideographs
are the 字母 the kana point at and the characters a source text is written in. It does not cover the
kana of Enclosed CJK Letters and Months (㋕ and the like), which are enclosed forms rather than text,
nor Kana Extended-B (the tone marks of Taiwanese kana), and a code point it does not hold is `None`
rather than an error.

It also holds the 255 구결자 that 한/글 places in the Private Use Area, U+F67E to U+F77C, from
`data/vocab/gugyeol.tsv`. Unicode encodes none of them, so the Hanyang private-use code point is their
identity here, as it is in the documents and fonts that use the convention. Their script is `gugyeol`;
their readings and 字母 stay empty until an openly licensed or cited source states them.

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

`data/vocab/genre.yaml` and `data/vocab/holders.yaml` hold the controlled values with Japanese
labels. `data/vocab/production.yaml` is a tree: an id is its path from the root
(`printed/type/wood`). It and `data/vocab/style.yaml` give each value an English label and a
definition. The site files each crop under one of three style groups, in the order its galleries
list them: running and cursive script; not assessed (with `mixed`); and the formal scripts (seal,
clerical, regular) with the print faces. A publication writes each crop's resolved style;
`scripts/publish_styles.py` writes the confirmed document styles onto crops already published.

`refs.forms(reading)` is every character written for a reading, from the layer, and
`refs.candidates(reading)` is the same list ordered for a classifier and for a reviewer: the modern kana first, then the hentaigana in code point order, then the katakana. The
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
several where the kanji table has several, each with the version in its table's first header line.
`data/vocab/han-ids.tsv` gives each ideograph's Ideographic Description Sequences from BabelStone's
IDS.TXT, and `data/vocab/han-component-forms.tsv` the component forms that stand for another character
(⺡ for 氵, 氵 for 水); `scripts/build_han_components.py` writes both, and search reads them to find a
character by its components. `data/vocab/graphemes.yaml` states the curated kana assignments, 字母
overrides and cited orthographic families. A new value must state its source.
`data/vocab/words.tsv` and `data/vocab/word-spellings.tsv` record the characters cited as writing a
word (計, 許 and 斗 for the particle ばかり), one row per source statement; the spellings of a word are
read through the word, never as a pair of characters (decision 0004).

Refresh the cached Unicode files before rebuilding the character table; they live in `cache/ucd/`
and are not in git.

```sh
for f in UnicodeData.txt Blocks.txt Scripts.txt DerivedAge.txt NamesList.txt Jamo.txt; do
  curl -o cache/ucd/$f https://www.unicode.org/Public/18.0.0/ucd/$f
done
curl -o cache/ucd/confusables.txt https://www.unicode.org/Public/security/latest/confusables.txt
uv run python scripts/build_character_table.py cache/ucd
```

### dates

`atlas dates` collects every dated statement the sources make about each document as an attributed
claim (`DateClaim`, written to `work/dates/claims.jsonl`), and resolves what each document shows
(`resolved.jsonl`). On the site each claim is an assertion of the ledger (`docs/design/form-model.md`):
subject the document, predicate `date_<kind>`, the fields below but its id, kind, tier and source as its
value, asserted by `source:<id>` with an evidence row naming the source and the locator. What each
document shows is `document_dating` (migration 0052).

| Field | Meaning |
| --- | --- |
| `id`, `document` | the claim's id, from its document, source, locator, text and kind |
| `kind` | `composed` (成立, the text itself), `copied` (書写, 補写 noted), `colophon` (奥書, 識語, 序, 跋, 刊記), `annotated` (加点), `printed` (刊行), `edition` (a later impression or edition), `exemplar` (the date of the copy it was copied from, 元奥書), `produced` (the holder dates the item and says no more), `other` (a date of the content, 内容年代) |
| `scope` | `witness` for this copy, `work` for the work it carries: 国書's work dates and every `composed` |
| `text` | the date as the source writes it, verbatim |
| `start`, `end` | first and last year in the Western calendar (Julian before 1582-10-15, Gregorian after, as HuTime's 101.1); one is null for `before` or `after` |
| `precision` | `day`, `month`, `year`, `years` (a range or an era span), `decade`, `century`, or `period`: words that name a time with no years read from them, a named period such as 江戸後期 or an era date HuTime could not convert, kept as written |
| `qualifier`, `uncertain` | `circa`, `before` or `after`; a question mark or 推定 in the source |
| `day`, `calendar`, `conversion` | the Western-calendar day of a dated day; whether the text is in the Japanese calendar; the HuTime request that converted it |
| `tier` | `attested` (a catalogue field states it), `derived` (read from a free-text note) |
| `source`, `locator`, `note` | the data/sources id, the record URL and its field (`#bpublish.0`, `#metadata=Publication Date`), and the note or work the date belongs to |

An era year is placed in the Western year its first day falls in (寛政3年, 1791-02-03 to
1792-01-23, is 1791); where the source writes the Western year beside the era, the source's stands.
A document with no claim that has years stays undated.

What a document shows (`dates.resolve`): on the witness axis, the first kind with a dated claim about
this copy, in the order copied, colophon, produced, annotated, printed, edition for handwriting and
edition, printed, produced, colophon, copied, annotated for print (a later impression dates the copy in hand more closely than the first printing); on the composed axis, its
composition. Within a kind, attested wins over derived, a holder's manifest over 国書 and
the aggregators, then the more precise claim. A claim that does not overlap the chosen one makes the
date `disputed`, and every claim stays listed.

## Exports

`atlas export` writes a release directory. Options select the licence set (`--licence CC-BY-SA-4.0`
keeps records whose image and text rights compose into that licence), the review states, and whether
crops are materialised. Every release directory holds `ATTRIBUTION.md`, generated from the rights
fields of the records it contains.
