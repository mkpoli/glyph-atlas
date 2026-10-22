-- How the crops of a run are written, moved from the old database's `unit_ngrams.text` at the cutover by
-- scripts/atlas3/runs.py. A run is filed by its graphemes, each crop folded to its grapheme family's head
-- (んし); this names the written character of each crop written apart from its head (𛁅 for し), so a
-- run's written form is its crops' characters, each this row's or else its head. A review that changes a
-- crop's written character updates its row here; the publisher keeps the rest.
CREATE TABLE run_written(crop INTEGER PRIMARY KEY, char TEXT NOT NULL);
