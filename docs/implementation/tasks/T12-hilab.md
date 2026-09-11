# T12 Import the 東京大学史料編纂所 くずし字データセット

Goal: the 325,261 crops as units without page placement, reaching their rights through a document.

Read first: `data/sources/hi-lab-kuzushiji.yaml`, `src/kuzushiji_atlas/remotezip.py`.

Input: `https://data.lab.hi.u-tokyo.ac.jp/kuzushiji/2023-03-27/all.zip` (4,025,813,993 bytes,
zip64), members `all/characters/U+XXXX/<id>.jpg`; the `characters/` tree at the root is a subset
and is ignored.

Outputs
- `work/hilab/`: one document `hi:kuzushiji-2023` standing for the dataset as published (title
  くずし字データセット（東京大学史料編纂所）, holder 東京大学史料編纂所, `meta.kind = "dataset"`, rights
  CC BY 4.0 with the attribution from the source file); no pages; one unit per member:
  `id = hi:<numeric id>`, `document_id`, `crop = all.zip!all/characters/U+XXXX/<id>.jpg`,
  `unicode` from the folder, `script` from the code point range, `text_source = reading = chr`,
  `classification=identified` for kanji and `unassessed` for kana, `granularity=char`,
  `review=transcriber`, `upstream` with `ref` and the record URL
  `https://wwwap.hi.u-tokyo.ac.jp/ships/w34/detail/<id>`.
- The importer asserts that numeric ids are unique across folders and stops if they are not.
- `atlas import hilab [--download]`: without the flag only the listing is read; with it the crops
  are extracted to `cache/hilab/` in batches and `crop_sha256`, width and height are filled.

Edge cases: `.DS_Store`; a folder not named `U+XXXX` (skipped, counted).

Tests: a zip64 fixture with three members in two folders; the uniqueness assertion.

Acceptance: 325,261 units; 5,887 distinct `unicode`; 47,477 units with hiragana or katakana script.

Size: small. Depends on: T01, T05.
