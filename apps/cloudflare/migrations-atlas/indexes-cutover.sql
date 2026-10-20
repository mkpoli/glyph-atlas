-- Indexes the cutover adds to glyph-atlas-v3, built once by scripts/atlas3/fill.py after it has filled
-- the columns they hold, so no index is kept up row by row during the fill. Each port appends its own;
-- none is reordered. A query repeats each partial-index condition as written, or SQLite reads the table.

-- A family's crops with no character (`crops.family`, 0012), in the orders a gallery shows them: by
-- style, and by witness year. A review that gives such a crop a character takes it out of both, as
-- `crop_char` takes it in; `char` and `book` are held so a range reads no crop row.
CREATE INDEX crop_family ON crops(family, kind, style, shuffle, id, quiz, state, year, char, book) WHERE family IS NOT NULL AND char IS NULL;
CREATE INDEX crop_family_year ON crops(family, kind, year, shuffle, id, style, quiz, state, char) WHERE family IS NOT NULL AND char IS NULL AND year IS NOT NULL;
-- A character's crops by book, for the composed axis: a gallery, its decades and its time axis place a
-- crop by the date of the text its book carries, which few books have (59 of 1,609 on 19 October 2026),
-- so each reads one range per such book. `production` is here for the time axis's filter.
CREATE INDEX crop_char_book ON crops(char, kind, book, shuffle, id, style, quiz, state, production) WHERE kind < 2 AND char IS NOT NULL;
CREATE INDEX crop_family_book ON crops(family, kind, book, shuffle, id, style, quiz, state, production, char) WHERE family IS NOT NULL AND char IS NULL;
-- A visual group's crops, which a corpus gallery filters by: a few hundred at most, read whole.
CREATE INDEX crop_visual ON crops(visual_group, kind, style, shuffle, id, char, family, year, book, quiz, state) WHERE visual_group IS NOT NULL;
-- The books dated on one axis, and their years.
CREATE INDEX book_date_axis ON book_dates(axis, book, start, end);
-- A character's corpus glyphs with no witness year, which a gallery in year order lists after the
-- dated ones and the time axis counts as undated, as `crop_undated` (0002) holds the collected crops.
CREATE INDEX crop_undated_corpus ON crops(char, kind, style, shuffle, id, quiz, state, year, production) WHERE kind = 1 AND year IS NULL;
