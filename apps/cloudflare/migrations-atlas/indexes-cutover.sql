-- Indexes the cutover adds to glyph-atlas-v3, built once by scripts/atlas3/fill.py after it has filled
-- the columns they hold, so no index is kept up row by row during the fill. Each port appends its own;
-- none is reordered. A query repeats each partial-index condition as written, or SQLite reads the table.

-- A family's crops with no character (`crops.family`, 0012), in the orders a gallery shows them: by
-- style, and by witness year. A review that gives such a crop a character takes it out of both, as
-- `crop_char` takes it in; `char` and `book` are held so a range reads no crop row.
CREATE INDEX crop_family ON crops(family, kind, style, shuffle, id, quiz, state, year, char, book) WHERE family IS NOT NULL AND char IS NULL;
CREATE INDEX crop_family_year ON crops(family, kind, year, shuffle, id, style, quiz, state, char) WHERE family IS NOT NULL AND char IS NULL AND year IS NOT NULL;
