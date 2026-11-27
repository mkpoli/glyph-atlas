# Agreed marks: crops no reviewer needs to be asked about

`atlas review agreed-marks` (`src/glyph_atlas/review/agreed_marks.py`) marks the pending collected
crops that pass two bars: the 20 nearest crops carry the crop's character
([neighbour agreement](neighbour-agreement.md)), and the 10 nearest crops of its 字種 in other books
carry exactly its label. A crop whose 10 all carry one other label of the 字種 is flagged with that
label. This page records the run of 2026-11-27. Nothing was loaded to the site.

## Inputs

The similar-crop index `work/similar/0a82edb62d862c2b` (2026-09-27, 503,013 crops) and the local
copy of the site's database as of 2026-11-27. The index holds 150,950 of the pending collected
crops.

## Counts

| | crops |
| --- | ---: |
| pending collected crops in the index | 150,950 |
| full streak of 20 (字種) | 84,053 |
| agreed: full streak and 10 of 10 on the form | 78,160 |
| flagged with another form | 1,813 |

14,870 of the agreed crops are kana. The commonest flags: 𛂂 filed where the neighbours carry な
(206), の → 𛂜 (175), リ → り (165), ヘ → へ (135), し → 𛁅 (55), を → 𛄚 (41), 事 → U+F0000 (39),
た → 𛁠 (38), ト → と (36).

Only crops still on their first cut are marked: the index holds that image.

## Flagged crops in Quick Review

`scripts/atlas3/quiz_marks.py --forms agreed-marks.json` loads the flagged crops as suspects of basis
`form` (`migrations-atlas/0023_suspect_basis.sql`): a round deals them among its doubted crops, and the
tile offers the neighbours' character as the reading. The classifier's mark on a crop stands, so of the
1,813 flagged crops 1,324 get a mark and 489 keep the classifier's (3 of them with the same reading).
A mark holds while the crop keeps its label, and is dropped when its box is repaired.

## The form bar against the reviews

Collected crops whose last verdict is a confirmation or a wrong-character report naming a character
(`events`; a crop only shown in a round has no verdict). A confirmed crop is judged under the
character confirmed; a crop a reviewer filed under another character of its 字種 is judged under the
label the index was built with. Neighbours carry today's labels of the other books.

| reviewer | crops | agreed | left to a reviewer | flagged |
| --- | ---: | ---: | ---: | ---: |
| confirmed the character | 371 | 312 | 58 | 1 |
| named another form of the 字種 | 39 | 9 | 24 | 6, each naming the reviewer's form |

The form bar passes 9 of the 39 crops whose form a reviewer changed, all kana filed under the plain
letter where the page has a hentaigana few crops carry as a label: な for 𛂂 (5), and one each of
に/𛂌, は/𛂣, は/𛂞, ネ/𛄧. Among the reviewed crops that pass, 9 of 321 (2.8%) carry the wrong form.

Reviewed crops are no sample of the collection, and this table tests the form bar alone, without
the streak. An agreed mark says a machine found no reason to ask; it is no review.
