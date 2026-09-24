# 0003 Character identity and 字母 metadata

Date: 2026-09-19

## The problem

A written character was stored once per unit. `Unit.unicode` held the code point and `Unit.jibo` held
the 字母, so the 字母 of a code point was written into every record that carried the code point, and a
code point the tables did not know had nowhere to put one.

Unicode 18.0 made the cost visible. It added `U+1B127` 𛄧 KATAKANA LETTER ALTERNATE NE and
`U+1B128` 𛄨 KATAKANA LETTER ALTERNATE WI, the alternate katakana of the Meiji period, which until
then had been written as the kanji 子 and 井 for want of a code point. Three characters stand where
one did:

| | Character | Shape | 字母 |
| --- | --- | --- | --- |
| the kanji that was used instead | U+5B50 子 | — | — |
| the katakana letter | U+30CD ネ | ネ | 祢 |
| the alternate letter | U+1B127 𛄧 | 𛄧 | 子 |

A record of a Meiji printing holds one of the three, and the code point is what says which. The kanji
is not a substitute for the katakana any more, `confusables.txt` pairs 𛄧 with 子 rather than unifying
them, and a `jibo` column on a unit cannot say that 子 is what the form derives from while 子 is also
a character in its own right.

## The decision

The browsing hierarchy is **grapheme → character → form** (clarified 2026-09-21):

- **Grapheme** groups explicitly related written characters. 仮 and 假 share a family
  represented by 仮. Existing kana families remain: ね, ネ and its historical kana
  belong together. Small kana and ligatures retain their own families.
- **Character** keeps the written identity and code point. 仮 (`U+4EEE`) and 假
  (`U+5047`) remain distinct rows in `characters.tsv`, with separate counts.
- **Form** is the exact ink in a source occurrence: its unit identity, crop, placement,
  image revision and any recorded MJ/IVS/GlyphWiki/local variant identifier.

**字母** remains metadata on `Character.jibo`. Readings are independent of both
encoded identity and family membership. Shared pronunciation, derivation or visual
similarity alone does not merge characters.

The Han families currently cover 271 unambiguous one-modern/one-old pairs from the
first character cell of the [Agency for Cultural Affairs Jōyō index](https://www.bunka.go.jp/kokugo_nihongo/sisaku/joho/joho/kijun/naikaku/kanji/joyokanjisakuin/).
`graphemes.yaml` records each pair, relation and source digest. Multi-form entries
and compatibility ideographs are outside this initial set. Broader alignment
policies remain separate from browsing families.

Character and occurrence queries stay exact by default. `expand=grapheme` requests
the whole family; selecting a written character returns its own occurrences.

`Unit.jibo` and `Candidate.jibo` are removed. `Unit.unicode` keeps the code point, which is the
record's reference into the character layer.

The character table covers every kana of the kana blocks and every CJK unified ideograph: the
ideographs are the 字母 the kana point at and the characters a source text is written in, so a table
without them could not answer what 子 is. It does not cover the enclosed kana forms (㋕) or Kana
Extended-B, whose letters are the tone marks of Taiwanese kana.

Where a fact comes from is recorded per fact. The 字母 of a hentaigana is Unicode's own `derived
from` chart note first, then MJ's 表, then a hand-written row that cites its source; the 音価 map of
the kana and the grapheme of each is hand-written in `data/vocab/graphemes.yaml` with a citation per
row. The kana assignments use the encoded letter identities and cited historical descriptions.
Shared appearance or 字母 alone does not establish a family.

`Script` gains `han`, Unicode's name for the script of the kanji, and keeps `kanji`, which older
records write for it. The character layer states `han`; a unit imported before this decision keeps
what it said.

## Consequences

- The six Unicode 18.0 kana are characters the dataset can record, with their 字母, their reading and
  the kana they are forms of. A project still on Unicode 17 can read a record of U+1B127 as a code
  point it does not know, which is what a code point is for.
- A reviewer searching for ね finds ten forms, 𛄧 among them, through `atlas character --grapheme`.
- `data/vocab/characters.tsv` is 9 MB of generated table in git. Rebuilding it needs the cached UCD
  files of one release, which are not in git; `docs/schema.md` carries the commands.
- The 字母 of a unit is now derived, so it cannot be corrected on a unit. Correcting it means
  correcting the character layer, which changes it for every record of that character. That is the
  intent: a 字母 that is wrong is wrong everywhere.
- A code point the table does not hold answers `None` rather than raising, so an importer that meets a
  character outside the covered blocks records it and gets no 字母, which is what it already did for
  a kana whose derivation no source stated.
- `tables.SCHEMA_VERSION` is 2. A dataset written before this decision holds a `units.jibo` column
  that the model no longer reads, so it is read as if the column were absent and loses the letter on
  the way out. Re-importing a source rebuilds the units with the code point, which is what the letter
  is derived from.
