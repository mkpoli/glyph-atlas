# T11 Import the 古活字データセット

Goal: the 36,869 movable-type blocks of 『徒然草 2巻』 with their per-character 字母 and the code
point candidates that follow from them.

Read first: `data/sources/codh-kokatsuji.yaml`, https://codh.rois.ac.jp/omt/dataset/, T04.

Input: `https://codh.rois.ac.jp/omt/dataset/001.zip` (261 MB): `001/dataset.csv` with columns
`ID,character,jibo,block,page,x1,y1,x2,y2,line,count,old_ID,OCR`; `001/page/001_NNN_H.jpg` (341
corrected page images, `H` 1 or 2 for the half of the spread); `001/block/*.jpg` crops. `x1,y1`
and `x2,y2` are the top-left and bottom-right corners on the corrected page image, `x2` and `y2`
exclusive. `character` may hold several characters (連彫活字, e.g. つれ〱) and `jibo` then holds one
字母 per character in the same order (徒連〱).

Outputs
- `work/kokatsuji/`: one document `codh-omt:001` (『徒然草 2巻』, holder 国立国会図書館, movable type,
  `dating` literal 慶長・元和年間 with start 1596 and end 1624, kind publication), pages
  `codh-omt:001:{page stem}` registered in the image cache under a `file:` key, lines
  `codh-omt:001:{page stem}:L{line}` with `box` as the union of the blocks of that line and
  `text` as their characters in `count` order, units `codh-omt:001:{ID}` with `document_id`,
  `granularity` `char` or `block`, `text_source = reading = character`, `unicode` the sequence,
  `seq = count`, `review=transcriber`, rights CC BY 4.0 with the attribution from the source file.
- 字母: for a single-character kana block, `jibo` is set and `candidates` lists the code points
  from `refs.candidates(reading)` whose 字母 equals it; one candidate sets `unicode` and
  `classification=identified`, several set `classification=ambiguous` with equal `p`. For a
  block, `upstream["jibo_sequence"]` keeps the aligned string and `classification=unassessed`.
- CLI: `atlas import kokatsuji [--zip cache/codh/001.zip]`.

Edge cases: 〱 and ゝ inside blocks (their 字母 slot repeats the mark); `jibo` shorter than
`character` (warning, `jibo_sequence` kept as given); `OCR` ignored.

Tests: a fixture CSV with one single-character block whose 字母 gives one candidate (な from 奈), one
whose 字母 gives two (か from 可), and one three-character block.

Acceptance: 36,869 units, 341 pages; counts of `granularity=char` and `block`, and of `identified`
and `ambiguous`, printed in the pull request.

Size: small. Depends on: T01, T02, T04, T05.
