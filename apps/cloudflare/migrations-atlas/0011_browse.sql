-- The browse page and review rounds deal crops in shuffle order from a point a seed picks, wrapping round
-- past the highest, so each seed shows a different but stable order that an index serves as it stands.
-- A query repeats each partial-index condition as written, or SQLite reads the table.

-- The collected crops by script category: the browse page unnarrowed reads one range per category and
-- merges them, a script group reads its own. Every filter it takes is in the index.
CREATE INDEX crop_listing ON crops(category, shuffle, id, quiz, state, production, char) WHERE kind = 0;

-- One character's collected crops or corpus glyphs: a browse page of a character or grapheme reads one
-- range per character, and a review round one per character and kind, the collected crops first.
-- Corpus glyphs no one has read have no character and are never dealt, so they have no entry.
CREATE INDEX crop_deal ON crops(char, kind, shuffle, id, quiz, state, production) WHERE kind < 2 AND char IS NOT NULL;

-- The Flagged view: every collected crop flagged or hard to read (state 2 or 4), withheld or not.
CREATE INDEX crop_flagged ON crops(shuffle, id) WHERE kind = 0 AND state IN (2, 4);
