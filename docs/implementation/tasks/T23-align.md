# T23 Alignment of transcription lines to detected characters

Goal: unit records with boxes for every character of a transcribed line, with confidences and an
honest failure mode.

Read first: `docs/plan.md` sections 5 and 6, `docs/schema.md` (units, groups), T03, T04, T21, T22,
`~/projects/Philology/honkoku-collate/collate/lines.py` (line matching by sequence similarity).

Interface: `atlas align <dir> [--document ID] [--pages N] [--detector path] [--classifier path]`
reads lines with boxes, fetches or reads cached page images, writes `units.parquet` and
`groups.parquet` into the same directory.

Steps
1. For each line, run the detector on the line box padded by 25% of its width, keep detections
   whose centre lies inside the padded box.
2. Order detections along the line (vertical: by y; for a line whose detections form two columns,
   treat as 割書 and order each column separately).
3. Take the transcription characters with role `main` from T03; 割書 characters align to their
   column; ruby, notes, okurigana and kaeriten are set aside as units without a box.
4. Dynamic programming over characters × detections: match cost `-log score_class(crop,
   equivalents(char))`, skip-detection cost and skip-character cost tuned on the pilot (T24); a
   character may consume two detections (a split character) and a detection two characters (a
   ligature or a merged pair) with their own costs.
5. Emit units: `box`, `text_source`, `reading` (the character), `unicode` for kanji and marks, and
   for kana the modern kana code point with `classification=unassessed`; `confidence.detection`,
   `confidence.segmentation` (1 for one-to-one, lower for split or merged), `confidence.text` (the
   match probability), `method="detect-align"`, `review=machine`; `groups` for runs where the DP
   chose merges (granularity `sequence`).
6. Below a page-level threshold on mean match probability, mark every unit of the line
   `review=rejected` and keep them.

Edge cases: lines with far more detections than characters (illustration inside the box); empty
`text`; □ gaps (consume a detection with no label, `kind=gap`); 見せ消ち (both segments aligned,
`flags` kept in `upstream`).

Tests: a synthetic detector and classifier (fixtures returning fixed boxes and scores) on lines
with a split, a merge, and a gap; determinism of ids.

Acceptance: on the calibration pages of T24, joint precision (IoU ≥ 0.5 and label match) ≥ 0.95
for accepted units on printed pages, with coverage reported; the thresholds and costs written into
`models/align/README.md`.

Size: large. Depends on: T02, T03, T04, T21, T22.
