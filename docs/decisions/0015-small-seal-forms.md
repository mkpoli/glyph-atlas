# 0015 Small seal characters are forms of their Han character's grapheme

Date: 2026-11-24
Status: accepted

## The problem

Unicode 18.0 encodes small seal (小篆) apart from Han, as it encodes katakana apart from hiragana, and
the character layer took each of the 11,328 seal characters for a grapheme of its own. A seal
character writes the same 字種 as the Han character it became: 𽌇 and 萅 are 春, as 𛂞 and ハ are は. On
the site 春's gallery, rounds and 字体 row left out the crops of 説文解字, and 𽌇 stood among the related
characters beside loans and homographs.

## The decision

A small seal character is a member (字体) of the grapheme of its Han character, the `small-seal`
equivalent in `data/vocab/kanji-variants.tsv` (Unicode's `kSEAL_MCJK`). Its `script` stays `seal`.

- **Through the head.** A seal character whose equivalent is a member of a family joins that family:
  𽌇's equivalent is 萅, so it joins 春. Several seal characters of one Han character (折, 民) are all
  its members.
- **By the stated equivalent alone.** 叓 is a grapheme of its own, so 𽀅 joins 叓. That 𽀅 is the seal
  form of 吏 is inferred by joining two rows (`seal-chain`, `refs.seal_chain_edges`); the pair stays a
  related one and joins nothing.
- **After the Han families.** `scripts/build_character_table.py` places the seal characters once
  `glyph_atlas.shape_families` has made the families. A seal character is no tie there, so no Han
  character joins or leaves a family by it. Each membership is a row of role `seal` in
  `data/vocab/grapheme-families.tsv`.
- **Last.** Seal members follow every Han form and every drawn form of the family wherever members are
  listed. On a form bar a seal character stands last in its 構形, in a block of its own there where it
  is built as a layout of it (𽓻 `⿰辵禺` under 遇's `⿺辶禺`), and the number keys reach it after every
  other form.

## What a seal member leaves as it was

A seal member makes a family larger, and several rules read a family's size or its members. Each reads
the members a transcription may write under one code point (`refs.transcribed`), which leaves the seal
characters out:

| Rule | Where |
| --- | --- |
| A normalised corpus's glyph names a family and no member of it when the family has several; a glyph's `family_members` | `corpus/identity.py`, `importers/codh.py` |
| The family scope a card's corpus counts ask for, and the gallery's default scope | `corpus/api.py`, `review/characters.py`, `scripts/export_graphemes.py` |
| The forms a cluster may be named by; a seal character has no clustering of its own | `form_clusters.py` |
| The members of a merged class the classifier votes for | `review/suggestions.py` |
| The members of a visual grouping, and the stored grouping a card keeps | `visual_samples.py`, `scripts/export_graphemes.py` |
| The kanji a kana is the cursive form of | `refs.kana_origins` |

The relation of a family that is more than its seal characters is unchanged (`shinjitai-kyujitai`,
`shape-variant`); a family that no other row makes has the relation `small-seal`.
A seal character's own card opens on its own crops.

## Rounds

A round of a grapheme deals the crops of its seal characters with the rest. A round narrowed to a
script deals the corpus glyphs of the members of that script. A round is keyed by its grapheme: one
asked for by a member's key is refused with the grapheme's name by the service, and an address that
names a member (`/review/U+3D307`, `/review/U+30CF`) deals its grapheme's round.

## Measures

From the committed tables, on Unicode 18.0:

| What | Count |
| --- | --- |
| Rows of `characters.tsv` that change, in `grapheme` alone | 11,328 |
| Rows added to `grapheme-families.tsv` (none removed) | 11,328 |
| Families with a seal member | 10,134 |
| of them, one Han character before | 2,646 |
| Cards whose family changes | 45,303 of 128,779 |
| `graphemes` rows a reference load updates (`head`) | 11,328, counted as 22,656 written with the index |
| R2 objects it changes: `char/` | 59,113 of 128,779 (45,303 with a changed family, the rest naming one among their variants) |
| `summary/` shards | 4,077 of 4,096 |
| `readings/` shards | 2,479 of 2,839 |
| `list/graphemes/` pages | 107 before, 135 after, all changed |
| `names`, `ids/`, `components/`, `structure.json.gz` | none |

The load writes no row of `run_written` or `runs`. The next publication of the 篆書字体データセット books
writes a `run_written` row for each crop placed on a seal character, 16,616 on the five books'
committed tables, and refiles the runs that hold one, 5 on the boxes of those tables. Until it does,
those runs are found by the seal character and not by its grapheme, and `check.py` reports them.

`graphemes.head` takes effect with the load, and the publication's answers name each character's
grapheme as they were written: run `answers.py` directly after the load, or the rounds the answers list
under a seal character's own key are refused.

## Limits

- `kSEAL_MCJK` is "the modern CJK equivalent" in the file's words, and one value for each seal
  character. The character may be written otherwise today (叓 and 吏); such a seal character stays with
  the character Unicode names. The 構形 a seal character is proposed from the same value is a matter of
  its own (decision 0012): 書's seal character has none and is a form of 書.
- The records of published crops carry the `grapheme` they were written with until a publication writes
  them again.

## Sources

- Unicode, `SealSources.txt`, version 18.0.0, https://www.unicode.org/Public/18.0.0/ucd/SealSources.txt:
  one `kSEAL_MCJK` for each of U+3D000–3FC3F. `data/sources/unicode-ucd.yaml`.
- `docs/decisions/0003-three-layers.md`, `docs/decisions/0012-structure-layer.md`,
  `docs/decisions/0014-drawn-forms.md`.
