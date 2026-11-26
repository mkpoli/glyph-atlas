# The hentaigana of 古活字データセット blocks

CODH's 古活字データセット (徒然草, [慶長・元和年間], doi:10.20676/00000416) states a kana and its 字母 for every block of one kana.
Where several code points share the 字母 (能: 𛂛 and 𛂜) the importer left the block ambiguous and the site showed the plain kana:
6,252 blocks, 2,022 of them from another 字母 than the modern kana's own.

`data/vocab/kokatsuji-forms.tsv` names the code point of 1,895 of the 2,022. The kana and the 字母 are the dataset's; which of the
code points a block's shape is was decided here and no reviewer has confirmed it. The imported tables keep what the dataset states.
A glyph's record gives the form as its written character with the basis `glyph_shape`, the table as its evidence and no source code
point (`corpus/identity.py`); a reviewer's answer or a form decision outranks it.

## How

The blocks of each 字母 were ordered by their likeness to the group's mean (the classifier's features of the similar-crop index),
so odd shapes stand at the end, and every block was looked at. The shapes were compared with the forms GenSeki Hentaigana
Gothic and GenZui Sans draw for the code points, and with samples of their crops in CODH's 日本古典籍くずし字データセット
(doi:10.20676/00000340). 能 was split in two by the same features from a seed of each shape; the 260 blocks around the split
were looked at again. A second reading of every named block removed two.

| Kana, 字母 | Blocks | Named | Left |
|---|---|---|---|
| の 能 | 746 | 𛂜 479, 𛂛 246 | 21 with the left part of one and the right part of the other, or near it |
| か 可 | 373 | 𛀚 373 | |
| と 登 | 291 | 𛁻 291 | |
| す 須 | 180 | 𛁑 179 | 1, which is printed す |
| は 盤 | 154 | 𛂢 111, 𛂣 42 | 1 of a third shape |
| つ 川 | 72 | | 72: the block is つ itself |
| ほ 本 | 55 | 𛃀 54 | 1, which is printed 不 (不具に, 第154段); the dataset's ほ 本 is wrong |
| は 者 | 45 | 𛂦 45 | |
| た 多 | 34 | 𛁠 32, 𛁟 2 | |
| さ 佐 | 25 | 𛀾 25 | |
| な 那 | 16 | 𛂄 16 | |
| ね 年, け 介, わ 王, す 春, ゐ 井 | 31 | | 31: too few, or no form to compare |

The other 4,230 ambiguous blocks are from the modern kana's own 字母 (て 天, を 遠, も 毛) and stay the plain kana.

## The archive

The table names blocks by the ids of the archive imported on 2026-09-11, which the published units come from. The archive CODH
serves on 2026-11-26 (`dataset.csv` of 2026-08-19) has 37,314 rows with other numbers and boxes, and is not imported; by box at least 15 of
the named blocks are part of a 連彫活字 there. A row stands only for a block of the kana it repeats.
