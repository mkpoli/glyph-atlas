-- A collected crop's review priority, which a round deals by on four seeds in five: 0 when the aligner's
-- confidence in its text or segmentation is below 0.9, 2 when both are at least 0.99, else 1
-- (`glyph_atlas.review.atlas.review_priority`). The publisher owns it: a catalogue's `units.priority`.
-- A corpus glyph keeps 1, and a review never changes it. Adding the column writes no row: the cutover's
-- fill (scripts/atlas3/fill.py) applies it, gives the crops published before it their priority, and
-- builds the browse page's indexes (indexes-cutover.sql) once over the filled rows; publish.py refuses
-- a layout without it.
ALTER TABLE crops ADD COLUMN priority INTEGER NOT NULL DEFAULT 1;
