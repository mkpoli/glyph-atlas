# 同音 (甲種本), head characters

同音 lists the Tangut characters by initial, homophones together. Each entry is a head character in
large writing with a note in small writing beside it. These tables place the head characters of the
甲種本 (IOM RAS, Инв. No. 207) on the plates of 俄藏黑水城文獻 vol. 7, printed pages 1–28.

| File | Rows | One row per |
| --- | ---: | --- |
| `pages.tsv` | 28 | printed page of the volume, with its scan's checksum and its two plates |
| `columns.tsv` | 721 | column of a half-folio that holds head characters |
| `characters.tsv` | 5,673 | head character |

A place is cited as the dictionaries cite it: `02A51` is folio 2, side A, column 5, position 1. Side A
is the right half of a plate and column 1 its rightmost column. Folio `n` is plate `(n - 1) % 2` of
printed page `(n + 1) // 2`. Boxes are pixels of the page scan, origin top left.

## characters.tsv

`folio`, `side`, `column`, `position`, `char`, `codepoint`, `page`, `x`, `y`, `w`, `h`, `score`,
`ink`, `quality`.

The character and its place come from nkay0's rhyme-dictionary table, column `旧版同音`
(`data/sources/nkay0-tangut-rhyme.yaml`). The box is a machine placement by
[`scripts/build_tangut_tongyin.py`](../../../scripts/build_tangut_tongyin.py): `score` is how well the
character's glyph in Noto Serif Tangut matches the ink at the box, and `ink` is the stroke darkness
in the box over the share of its box the glyph inks. `quality` is `ok` (4,189), `low-score` (1,253,
under 0.28), `size` (95, a box far from its folio's usual size), `faint` (62, far less ink than the
folio's other boxes: bare paper, or a stain that swallows the strokes) or `sparse` (74, a match in
a half-folio where fewer than half the places match).

The `sparse` places are in the half-folios 30B, 38A, 38B, 51A, 53A and 55B, whose columns the
cutter did not find. By eye, 22 of the 24 matches in 51A and 53A sit on another character and two
are doubtful; in 30B and 38B some columns are right and others are not.

In a random draw of 150 from the `ok` and `sparse` places together, 148 sat on the character the
table names; six of those cut off part of it or fell on ink too dark to read. `atlas import tangut-tongyin <scans>` makes a unit of
each `ok` place and leaves the others in this table; a half-folio with no `ok` place gives no
lines either, since its column boxes are not on its columns.

## Limits

- The notes in small writing are not transcribed, so a column's `text` is its head characters only,
  and the units carry no position in the line: neighbours in a column are no sequence of the text.
- Folios 2, 18, 27, 30, 37, 38, 44, 51 and 53–55 place fewer than three characters in four: a
  section heading changes the grid, or the plate is too dark to show the rule lines.
- Folio 1 is the preface and has no head characters. The table gives column 19B6 nine.

## Sources and rights

The plates are photographs of a Western Xia woodblock print and are recorded as public domain
(`docs/licensing.md`, Copyright in crops), with the publisher's terms kept beside them. The scans of
the printed volume were supplied by Maxim Persikov. The scans themselves are not in the repository;
`pages.tsv` pins each by SHA-256.
