# T20 Training data for the character detector

Goal: class-agnostic character boxes from the CODH tables as a detection dataset with a split by
book.

Read first: `work/codh` tables (T10), `docs/plan.md` section 6.

Outputs
- `scripts/build_detector_data.py` → `work/detector/{train,val,test}.json` in COCO format with one
  category `character`, images referenced by cache path, and `work/detector/split.tsv` (bid, split).
  Held-out books: four chosen by a fixed hash of the bid, listed in the TSV; `val` takes two more.
- Tiling: pages cut into 1024×1024 tiles with 128 px overlap; boxes clipped, boxes with less than
  40% of their area inside a tile dropped; tile coordinates recorded so predictions map back.
- Units with `kind=unreadable` are excluded from targets and their regions marked as ignore.
- Statistics printed: images, tiles, boxes, box size percentiles.

Edge cases: pages with zero boxes (kept as negatives, at most 5% of tiles); very small boxes
(voicing marks) kept.

Tests: tiling of a synthetic page with three boxes, including one on a tile boundary.

Acceptance: 6,151 pages processed, 1,086,326 boxes assigned, no box lost except by the clipping rule
(count reported).

Size: small. Depends on: T10, T02.
