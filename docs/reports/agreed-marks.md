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
(206), の → 𛂜 (175), リ → り (165), ヘ → へ (135), し → 𛁅 (55), 時 → 时 (30).

## The form bar against the reviews

Collected crops a reviewer confirmed or corrected, each judged under the label the index was built
with, against today's labels of the other books.

| reviewer | crops | agreed | left to a reviewer | flagged |
| --- | ---: | ---: | ---: | ---: |
| kept the label | 4,368 | 4,143 | 219 | 6 |
| filed another form of the 字種 | 62 | 21 | 32 | 9, each naming the reviewer's form |

The form bar passes a third of the crops whose form a reviewer changed. They are forms few crops
carry as a label, so the neighbours in other books are filed under the usual one too: 両 for 兩 (6),
な for 𛂂 (5), に for 𛂌 (3), and one each of 売/賣, 也/𬻿, は/𛂣, は/𛂞, ネ/𛄧, 衡/𢖍, 撃/擊. Among
the reviewed crops that pass, 21 of 4,164 (0.5%) carry the wrong form.

Reviewed crops are no sample of the collection, and this table tests the form bar alone, without
the streak. An agreed mark says a machine found no reason to ask; it is no review.
