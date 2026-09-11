# T20 Training data for the character detector

Goal: class-agnostic character boxes from the CODH tables as a detection dataset with a frozen
split by book that T22 shares.

Read first: `work/codh` tables (T10), the image cache (T02), `docs/plan.md` section 6.

Outputs
- `data/splits/codh.tsv`, committed: bid, production (from the CODH book page: 版本 or 写本), split
  (`train`, `val`, `test`); the split is by book, stratified by production, four books in `test`
  and two in `val`, chosen by `sha1(bid)` order within each stratum so the choice is reproducible.
- `scripts/build_detector_data.py` → `work/detector/{train,val,test}.json` (COCO, one category
  `character`, images = tiles referenced by cache path plus tile origin), `work/detector/tiles/`
  generated on demand with `--materialise`, else tiles are cut at training time from the cached
  pages using the recorded origins.
- Tiling: 1024×1024 with 128 px overlap, pages padded with white at the right and bottom; a
  source box is assigned to every tile it overlaps and clipped; the clipped part is dropped when
  under 40% of the source box's area; each tile annotation keeps `source_unit_id`, so unique
  source boxes and tile annotations are counted apart.
- Units with `kind=unreadable` become `ignore` regions (COCO `iscrowd=1`); tiles without any
  annotation are kept at most as 5% of tiles per split.
- `work/detector/stats.md`: pages, tiles, unique boxes, tile annotations, box size deciles per
  split.

Edge cases: a page with no boxes; a box larger than a tile (kept in the tile holding its centre).

Tests: tiling of a synthetic 1500×1200 page with three boxes, one across a tile border, one
unreadable; the split file is deterministic.

Acceptance: 6,151 pages processed; unique boxes 1,086,326 minus the dropped-by-clipping count,
both printed; `data/splits/codh.tsv` committed.

Size: small. Depends on: T10, T02.
