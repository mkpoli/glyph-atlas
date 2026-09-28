-- Glyphs whose corpus row has yet to follow their form. A decision lists the glyphs it touched here, and
-- the Worker then moves their `corpus_units.character` and `family`, and the counts Quick review deals
-- from, a few hundred at a time. Moving a glyph rewrites its entries in four `corpus_units` indexes; a
-- cluster of thousands took seconds, and the decision waited for it. Forms and record pages read
-- `form_units`, so only rounds and search wait for the corpus row, and only until the list drains.
CREATE TABLE IF NOT EXISTS corpus_follow (id TEXT PRIMARY KEY) WITHOUT ROWID;
-- One Worker drains the list at a time: the one whose lease (`until`, in epoch milliseconds) is current.
-- A drain renews it with every batch, so one that stopped without letting go is taken over once it lapses.
CREATE TABLE IF NOT EXISTS corpus_follow_drain (one INTEGER PRIMARY KEY CHECK(one = 1), until INTEGER NOT NULL);
