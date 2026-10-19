CREATE TABLE crops(id INTEGER PRIMARY KEY,
  kind INTEGER NOT NULL,          -- collected, corpus, candidate
  book INTEGER NOT NULL, char INTEGER, form INTEGER, cluster INTEGER, visual_group INTEGER,
  production INTEGER, category INTEGER, style INTEGER, state INTEGER NOT NULL, quiz INTEGER NOT NULL,
  year INTEGER,                   -- witness year of the book, for year order and ranges
  shuffle INTEGER NOT NULL, cut INTEGER NOT NULL DEFAULT 0,
  revision INTEGER NOT NULL DEFAULT 0,
  flags INTEGER NOT NULL DEFAULT 0); -- suspect, irregular, reported
CREATE INDEX crop_char ON crops(char, kind, style, shuffle, id, quiz, state) WHERE kind < 2;
CREATE INDEX crop_year ON crops(char, kind, year, shuffle, id, style, quiz, state) WHERE kind < 2 AND year IS NOT NULL;
CREATE INDEX crop_book ON crops(book, shuffle, id) WHERE kind < 2;
CREATE INDEX crop_pending ON crops(production, shuffle, id) WHERE kind = 0 AND state = 0 AND quiz = 1;
CREATE INDEX crop_backlog ON crops(shuffle, id, production) WHERE kind = 0 AND state = 0 AND quiz = 1;
CREATE INDEX crop_candidate ON crops(char, shuffle, id) WHERE kind = 2;
CREATE INDEX crop_attention ON crops(char, book) WHERE flags <> 0;
CREATE TABLE pages(id INTEGER PRIMARY KEY, key TEXT NOT NULL UNIQUE, book INTEGER NOT NULL,
  rev INTEGER NOT NULL, next_slot INTEGER NOT NULL DEFAULT 0, next_site_slot INTEGER NOT NULL DEFAULT 8388608);
CREATE TABLE books(id INTEGER PRIMARY KEY, key TEXT NOT NULL UNIQUE);
CREATE TABLE graphemes(id INTEGER PRIMARY KEY, text TEXT NOT NULL UNIQUE, head INTEGER);
CREATE INDEX grapheme_head ON graphemes(head);
CREATE TABLE names(text TEXT, code_point INTEGER, rank INTEGER, PRIMARY KEY(text, code_point)) WITHOUT ROWID;
CREATE TABLE events(id INTEGER PRIMARY KEY, crop INTEGER NOT NULL, revision INTEGER NOT NULL,
  cut INTEGER NOT NULL, gen INTEGER NOT NULL, actor INTEGER NOT NULL, submission INTEGER NOT NULL,
  at INTEGER NOT NULL, kind INTEGER NOT NULL,
  label INTEGER, state INTEGER, style INTEGER, quiz INTEGER, x INTEGER, y INTEGER, w INTEGER, h INTEGER,
  note TEXT CHECK(length(note) <= 2000), UNIQUE(crop, revision));
CREATE INDEX event_actor ON events(actor, id);
CREATE TABLE reviews(crop INTEGER PRIMARY KEY, gen INTEGER NOT NULL, at INTEGER NOT NULL,
  char INTEGER, state INTEGER NOT NULL, style INTEGER, quiz INTEGER, mark INTEGER,
  x INTEGER, y INTEGER, w INTEGER, h INTEGER,
  base_char INTEGER, base_state INTEGER, base_style INTEGER, base_quiz INTEGER);
CREATE TABLE skips(crop INTEGER, actor INTEGER, cut INTEGER, at INTEGER,
  PRIMARY KEY(crop, actor)) WITHOUT ROWID;
CREATE INDEX skip_actor ON skips(actor, at);
CREATE TABLE generation(id INTEGER PRIMARY KEY CHECK(id = 0), gen INTEGER NOT NULL);
-- tally key columns write -1 for an unknown value: a WITHOUT ROWID key cannot hold NULL, and 0 is a real code.
CREATE TABLE tally(gen INTEGER, kind INTEGER, char INTEGER, book INTEGER, production INTEGER,
  style INTEGER, state INTEGER, quiz INTEGER, n INTEGER NOT NULL,
  PRIMARY KEY(gen, char, kind, book, production, style, state, quiz)) WITHOUT ROWID;
CREATE TABLE actor_stats(actor INTEGER PRIMARY KEY, reviews INTEGER, rounds INTEGER, at INTEGER);
CREATE TABLE productions(id INTEGER PRIMARY KEY, key TEXT NOT NULL UNIQUE, parent INTEGER);
CREATE TABLE codes(field TEXT NOT NULL, code INTEGER NOT NULL, key TEXT NOT NULL, PRIMARY KEY(field, code), UNIQUE(field, key)) WITHOUT ROWID;
CREATE TABLE imports(stamp TEXT PRIMARY KEY, at INTEGER NOT NULL);
