# T12 Import the 東京大学史料編纂所 くずし字データセット

Goal: the 325,261 crops as units without page placement.

Read first: `data/sources/hi-lab-kuzushiji.yaml`, `src/kuzushiji_atlas/remotezip.py`.

Input: `https://data.lab.hi.u-tokyo.ac.jp/kuzushiji/2023-03-27/all.zip` (4.03 GB, zip64), members
`all/characters/U+XXXX/<id>.jpg`; the `characters/` tree at the root is a subset and is ignored.

Outputs
- `work/hilab/` tables: one document `hi:kuzushiji-2023` (holder 東京大学史料編纂所, rights CC BY 4.0,
  attribution from the source file); no pages; one unit per member with `id=hi:<numeric id>`,
  `crop` = member path, `unicode` from the folder, `script` from the code point range,
  `text_source=reading=chr`, `classification=identified`, `granularity=char`, `review=transcriber`,
  `upstream={"source": "hi-lab-kuzushiji", "ref": "<id>", "record": "https://wwwap.hi.u-tokyo.ac.jp/ships/w34/detail/<id>"}`.
- `atlas import hilab [--download]`: without the flag only the listing is read (one request); with
  it the crops are extracted into `cache/hilab/` and `crop_sha256` filled.

Edge cases: `.DS_Store` members; folders whose name is not `U+XXXX`.

Tests: a zip64 fixture with three members in two code point folders.

Acceptance: 325,261 units; 5,887 distinct `unicode`; 47,477 units with hiragana or katakana script.

Size: small. Depends on: T01, T05.
