-- What the site knows of each book beside its crops, moved from the old database at the cutover
-- (scripts/atlas3/books.py). Books are named by their integer id in `books`.

-- The name a listing of books shows: the title of the book's pages, else the source its crops name,
-- else the one its corpus publication gave it.
ALTER TABLE books ADD COLUMN title TEXT;

-- When each book was written, copied or printed, on two axes: `witness` the copy itself, `composed`
-- the text it carries, as `glyph_atlas.dates` resolved them from the ledger's `date_*` claims, which
-- stay in `assertions`. `conversion` is the calendar conversion's JSON, `source` the record that dates it.
CREATE TABLE book_dates(book INTEGER NOT NULL, axis TEXT NOT NULL CHECK (axis IN ('witness','composed')),
  kind TEXT NOT NULL CHECK (kind IN ('composed','copied','colophon','annotated','printed','edition','exemplar','produced','other')),
  start INTEGER, end INTEGER, precision TEXT NOT NULL, qualifier TEXT, uncertain INTEGER NOT NULL DEFAULT 0,
  text TEXT NOT NULL, label TEXT NOT NULL, status TEXT NOT NULL CHECK (status IN ('single','agreed','disputed')),
  calendar TEXT NOT NULL, conversion TEXT, source TEXT NOT NULL,
  PRIMARY KEY(book, axis), CHECK (start IS NULL OR end IS NULL OR start <= end)) WITHOUT ROWID;

-- A book's characters in source order, for another site that shows each occurrence where it stands on
-- its page. An occurrence the site publishes names its crop; one it does not keeps the source's own id
-- in `unit`. `data` is what the source knows of it: page, line, block, position, context, box, label and
-- where the label came from.
CREATE TABLE book_characters(book INTEGER NOT NULL, ord INTEGER NOT NULL, crop INTEGER, unit TEXT, data TEXT NOT NULL,
  PRIMARY KEY(book, ord), CHECK ((crop IS NULL) <> (unit IS NULL))) WITHOUT ROWID;

-- Values a publication states about the whole site: `collection` (the collection's status),
-- `corpus_index` (the corpus summary), `review_epoch` (moves when review history was reset, so
-- browsers drop their undo state), and the citations of the sources behind character variants and
-- words (`variant_sources`, `word_sources`). Each value is JSON.
CREATE TABLE site(key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID;
