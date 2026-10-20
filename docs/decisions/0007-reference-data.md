# 0007 Character reference data in glyph-atlas-v3

Date: 2026-10-20

## The problem

Character pages and search read more than crops: each character's card, the 異体字 graph and its
derived tier, the forms that write another grapheme, words and their spellings, the anchors of forms,
and the component index. These sit in the old `glyph-atlas` database, which is deleted after the
cutover (0006).

## The decision

They move to `glyph-atlas-v3` as tables (`migrations-atlas/0004_reference.sql`): `characters`,
`character_variants`, `character_derived`, `grapheme_writers`, `component_variants`, `words`,
`word_spellings`, `representations`, `han_ids`, `han_components`, `han_component_names`, and
`visual_samples`.

They stay tables because the Worker filters and joins on them in SQL: a gallery widens to a
character's variants and writers, the component search intersects code points across components,
spellings look up words, and a form names its anchor. An R2 answer per publication could serve a
card, but not these joins without fetching and merging whole files per request.

- **Characters are `graphemes` ids** where a gallery or a widening join meets them: the card's key, both
  ends of a variant edge, a derived list's character, a writer and the grapheme it writes. A character
  the layout lacks gets the next id with its card's family head, as the publisher numbers one. The URL
  key stays `characters.code_point`; the character itself is `graphemes.text`.
- **Text stays where the Worker only looks up what was typed** or a Unicode code point: components,
  `han_ids`, the component index, words, spellings (a spelling can be several characters) and
  representation ids, which `forms.anchor` names.
- **Crops are integer ids.** A card's visual groups name the crops they show; those move out of the card
  into `visual_samples(grapheme, grp, role, rank, crop)`, resolved through the publisher's mirror. A crop
  the mirror lacks leaves the list, as other script facts about deleted crops do (0006).
- Indexes are those a Worker query uses: the reverse ends of variant edges, writers and component
  substitutions, spellings by spelling, representations by value. The old index on the card's
  character is `graphemes.text`.
- `representations` is written by form claims and never changes, so a reload inserts and never clears it.

## Consequences

- Every table that names a character for a crop query uses the same number as `crops.char`.
- The load is about 1.95M rows and 1.05 GB with indexes (local copies of 19–20 October 2026): cards
  811 MB, derived forms 170 MB, the component index 1.46M rows. It writes about 2.24M D1 rows,
  288K of them index entries; `reference.py` reports the figures for each load.
- The tools that export these tables (`export_character_variants.py`, `export_components_cloudflare.py`)
  still write the old database and move to `glyph-atlas-v3` with the Worker.
