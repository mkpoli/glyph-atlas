-- Indexes the cutover's fill (scripts/atlas3/fill.py) builds once, after it has filled the columns they
-- hold; a numbered migration creates none, so no index is kept up through a bulk write. A query repeats
-- each partial-index condition as written, or SQLite reads the table.

-- The browse page and review rounds (routes/browse.ts) deal crops in shuffle order from a point a seed
-- picks, wrapping round past the highest, so each seed shows a different but stable order that an index
-- serves as it stands.

-- The collected crops by script category: the browse page unnarrowed reads one range per category and
-- merges them, a script group reads its own. Every filter it takes is in the index.
CREATE INDEX crop_listing ON crops(category, shuffle, id, quiz, state, production, char) WHERE kind = 0;

-- One character's collected crops or corpus glyphs: a browse page of a character or grapheme reads one
-- range per character, and a review round one per character, kind and priority, the collected crops
-- first. Corpus glyphs no one has read have no character and are never dealt, so they have no entry.
CREATE INDEX crop_deal ON crops(char, kind, priority, shuffle, id, quiz, state, production) WHERE kind < 2 AND char IS NOT NULL;

-- The Flagged view: every collected crop flagged or hard to read (state 2 or 4), withheld or not.
CREATE INDEX crop_flagged ON crops(shuffle, id) WHERE kind = 0 AND state IN (2, 4);

-- A family's crops with no character (`crops.family`, 0012), in the orders a gallery shows them: by
-- style, and by witness year. A review that gives such a crop a character takes it out of both, as
-- `crop_char` takes it in; `char` and `book` are held so a range reads no crop row.
CREATE INDEX crop_family ON crops(family, kind, style, shuffle, id, quiz, state, year, char, book) WHERE family IS NOT NULL AND char IS NULL;
CREATE INDEX crop_family_year ON crops(family, kind, year, shuffle, id, style, quiz, state, char) WHERE family IS NOT NULL AND char IS NULL AND year IS NOT NULL;
