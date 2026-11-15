# 同音, head characters

同音 lists the Tangut characters by initial, homophones together. Each entry is a head character in
large writing with a note in small writing beside it. These tables place the head characters of two
of the exemplars reproduced in 俄藏黑水城文獻 vol. 7 (IOM RAS) on its plates, each in its own folder.
The 丙種本 (Инв. No. 2619), which follows on printed page 55, is not placed.

| Folder | Exemplar | Printed pages | `pages.tsv` | `columns.tsv` | `characters.tsv` | `ok` |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| `inv-207/` | 甲種本, Инв. No. 207 | 1–28 | 28 | 721 | 5,673 | 4,189 |
| `inv-208/` | 乙種本, Инв. No. 208, 209, 4775 | 30–55 | 26 | 666 | 5,287 | 4,285 |

`pages.tsv` has a row per printed page of the volume, with its scan's checksum and its two plates;
`columns.tsv` a row per column of a half-folio that holds head characters; `characters.tsv` a row
per head character.

A place is cited as the dictionaries cite it: `02A51` is folio 2, side A, column 5, position 1. Side A
is the right half of a plate and column 1 its rightmost column. Boxes are pixels of the page scan,
origin top left.

The volume prints two plates to a page. In the 甲種本 plate `n` shows folio `n`. In the 乙種本 plates
1 and 2 (page 29) are the preface, plates 3 and 4 show folios 3 and 4, plates 5 and 6 the two halves
of folio 7 (folios 5 and 6 are lost), plates 7 to 49 folios 8 to 50, plates 50 and 51 the two halves
of folio 51, and plates 52 and 53 folios 52 and 53. The folio of each plate was found by matching
every plate against the folios near it; plates 5, 6, 50 and 51 were looked at to see which half
they show.

## characters.tsv

`folio`, `side`, `column`, `position`, `char`, `codepoint`, `page`, `x`, `y`, `w`, `h`, `score`,
`ink`, `quality`.

The character and its place come from nkay0's rhyme-dictionary table, column `旧版同音` for the
甲種本 and `新版同音` for the 乙種本 (`data/sources/nkay0-tangut-rhyme.yaml`). The box is a machine
placement by [`scripts/build_tangut_tongyin.py`](../../../scripts/build_tangut_tongyin.py): `score`
is how well the character's glyph in Noto Serif Tangut matches the ink at the box, and `ink` is the
stroke darkness in the box over the share of its box the glyph inks.

| `quality` | 甲種本 | 乙種本 | Meaning |
| --- | ---: | ---: | --- |
| `ok` | 4,189 | 4,285 | becomes a unit |
| `low-score` | 1,253 | 807 | matches under 0.28 |
| `size` | 95 | 67 | a box far from its folio's usual size |
| `faint` | 62 | 80 | far less ink than the folio's other boxes: bare paper, or a stain that swallows the strokes |
| `sparse` | 74 | 48 | a match in a half-folio where fewer than half the places match |

The `sparse` places are in half-folios whose columns the cutter did not find: 30B, 38A, 38B, 51A,
53A and 55B of the 甲種本, and 3A, 7A and 42B of the 乙種本. By eye, 22 of the 24 matches in 51A and
53A of the 甲種本 sit on another character and two are doubtful, as do about half of the 23 in 7A of
the 乙種本; in 30B and 38B some columns are right and others are not.

In a random draw of 150 from the `ok` and `sparse` places of the 甲種本 together, 148 sat on the
character the table names; six of those cut off part of it or fell on ink too dark to read. In a
random draw of 150 `ok` places of the 乙種本, none was seen on another character and six fell on ink
too dark to read. `atlas import tangut-tongyin <scans>` makes a unit of each `ok` place and
leaves the others in these tables; a half-folio with no `ok` place gives no lines either, since its
column boxes are not on its columns.

## Limits

- The notes in small writing are not transcribed, so a column's `text` is its head characters only,
  and the units carry no position in the line: neighbours in a column are no sequence of the text.
- 甲種本: folios 2, 7, 10, 18, 27, 30, 37, 38, 44, 51 and 53–56 have fewer than three `ok` places
  in four: a section heading changes the grid, or the plate is too dark to show the rule lines.
  Folio 1 is the preface. The table gives column 19B6 nine head characters.
- 乙種本: folios 3, 4, 7, 9, 14, 25, 26, 27, 35, 38, 42, 46 and 53 have fewer than three `ok` places
  in four. The plates of folios 4 and 53 show side A only, and folio 7 side A is a torn fragment.
  The table's column holds 5,627 places; the 340 in folios 5, 6, 54 and 56 have no plate. The table
  gives 𗸈 two places, 17B55 and 20B64.

## Sources and rights

The plates are photographs of Western Xia woodblock prints and are recorded as public domain
(`docs/licensing.md`, Copyright in crops), with the publisher's terms kept beside them. The scans of
the printed volume were supplied by Maxim Persikov. The scans themselves are not in the repository;
each `pages.tsv` pins its scans by SHA-256.
