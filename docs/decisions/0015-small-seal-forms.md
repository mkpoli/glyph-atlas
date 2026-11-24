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
- **Last.** Where a card, a search, a round or a form bar lists a family, its seal members follow every
  other form: the Han and drawn members, and on a card and among a selection's targets the 異体字 and
  described forms outside the grapheme too. A list ordered by counts (Explore's tiles and their cards)
  keeps that order. On a form bar a seal character stands last in its 構形, in a block of its own there where it
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
| The class the benchmark credits a Han character for | `models/benchmark/bench.py` |

The relation of a family that is more than its seal characters is unchanged (`shinjitai-kyujitai`,
`shape-variant`); a family that no other row makes has the relation `small-seal`.
A seal character's own card opens on its own crops.

## What a Han reader sees change

- A gallery of a whole grapheme lists the crops of its seal characters, as it lists every member's. 仮
  and the other families a card opens on show them without being asked.
- The glyphs counted for a family (`family_glyphs`, `family_total`) count them, on the card of one
  member too.
- A selection on a Han card can be marked as a seal character, and a search for a kanji lists its seal
  characters, after its other forms and within the same 48.
- Explore under a script lists that script's characters under the grapheme's head. Where the head is of
  another script, as for every seal character, each character is a tile of its own and opens its own
  gallery.

## Keys that name a member

A seal character's key was a grapheme's key, and is a member's. Each reader of a key takes it to the
head the character table gives now:

- A round dealt, open or kept on a device under a member's key is its grapheme's, when it is asked for
  (`/review/U+3D307`, `/review/U+30CF`) and when it is saved. A round narrowed to a script deals the
  corpus glyphs of that script's members.
- A `has_grapheme` or `written_for` claim on it is read as the head's, and taken back by either key. A
  new claim names a head.
- A run is found by either spelling, as the next section says.

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

The figures are of the committed tables, before any other change the input holds. A dry run that
counts more `graphemes` than 11,328, or moves a Han head, is stopped and read.

## Rollout

Each step is run again without harm: a generator rewrites what differs, a load sends what differs from
the local copy and the manifest, and answers equal to the published ones publish nothing.

| Step | D1 rows written | What a reader sees |
| --- | --- | --- |
| 1. Merge and deploy | none | A seal character is a related character, as before, last in its 構形 on cards and form bars; its crops are its own grapheme's. The local review service has the new families at once |
| 2. `export_graphemes.py` | none: the reference input alone | nothing |
| 3. `reference.py`, dry run | none | nothing; its report is compared with the figures above |
| 4. `reference.py --send` | `graphemes.head` of 11,328 rows (22,656 with the index), 2 a part for its stamp | see below |
| 5. `answers.py`, as soon as 4 ends | one `generation` row and what the publication absorbs | Explore's tiles, browsing and the counts of the rounds file seal characters under their graphemes, once the pointer (a minute) and the copies kept at the edge (an hour at most) are read again |
| 6. Publish the 篆書字体データセット books from a new export | `run_written`, 16,616 rows; 5 `runs` rows moved, with their indexes and counts | no change: the runs are filed by the head, and read by either spelling throughout |

In step 4 the D1 rows land first and about 65,800 R2 objects follow. `mirror.lock` is held until the
upload ends, and no publication or `answers.py` starts before. Until a card's object is uploaded, and
for up to five minutes after in an isolate that holds the old one, the card lists its old members while
its grapheme's gallery and rounds hold the new ones; a seal character is a member on a card or related
on it, never both. A search reads a card its family's shard lacks from the card's own object. Explore's
tiles and the rounds' counts stay under the seal characters' own keys until step 5, and a round asked
for by one of them is dealt as its grapheme's.

Between 1 and 4 the 篆書 books are not published: a catalogue exported by then spells their runs by the
Han heads, while the site files the characters under themselves. Step 6 is held until a new export of
those books, since a catalogue made earlier keeps its old spellings. Five runs hold a small seal
character, on the boxes of the committed tables (已以, 托拓, 北柩, 反取 and 反取取). They are spelled by
the seal character until step 6, and `check.py` reports them. A run of two or three is read by its folded
spelling and by each with a seal character of those families that `run_stats` counts, at most 64 keys
probed; a run no seal spelling counts reads one spelling. That reading can go once `check.py` reports
none.

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
