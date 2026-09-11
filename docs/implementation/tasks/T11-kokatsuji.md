# T11 Import the 古活字データセット

Goal: the 36,869 movable-type blocks of 『徒然草 2巻』 with their per-character 字母.

Read first: `data/sources/codh-kokatsuji.yaml`, https://codh.rois.ac.jp/omt/dataset/.

Input: `https://codh.rois.ac.jp/omt/dataset/001.zip` (261 MB): `001/dataset.csv` with columns
`ID,character,jibo,block,page,x1,y1,x2,y2,line,count,old_ID,OCR`; `001/page/001_NNN_H.jpg` (341
page images); `001/block/*.jpg` crops. `character` may hold several characters (連彫活字, e.g. つれ〱)
and `jibo` then holds one 字母 per character in the same order (徒連〱).

Outputs
- `work/kokatsuji/` tables. One document `codh-omt:001` (『徒然草 2巻』, NDL holding, movable type,
  慶長・元和年間 as `dating.literal`), pages from the `page` column, one line per (`page`, `line`),
  one unit per block: `granularity=char` when `character` is one code point, else `block`;
  `text_source=reading=character`; `unicode` the code point sequence; `jibo` the single 字母 for
  single-character blocks, and for blocks the aligned string in `upstream["jibo_sequence"]`; `seq`
  from `count`; `classification=identified`; `review=transcriber`; rights CC BY 4.0 with the
  attribution string from the source file.
- CLI: `atlas import kokatsuji [--zip cache/codh/001.zip]`.

Edge cases: 〱 and ゝ inside blocks (their 字母 slot repeats the mark); `jibo` shorter than
`character` (log and leave `jibo_sequence` as given); `OCR` column ignored.

Tests: a fixture CSV with one single-character block and one three-character block.

Acceptance: 36,869 units, 341 pages; the number of `granularity=char` units and of `block` units
printed in the pull request.

Size: small. Depends on: T01, T05.
