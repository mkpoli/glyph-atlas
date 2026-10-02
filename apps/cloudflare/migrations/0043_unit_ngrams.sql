-- Runs of two or three crops that follow each other on a line, the occurrences Explore's pair and
-- trigram frequencies count. A publication writes the ids (`glyph_atlas.ngrams`), one run of each
-- `size` starting at a crop; `third` is null in a pair. `text` is the crops' labels as the site holds
-- them and `document` the first crop's book. The triggers keep both in step when a review relabels a
-- crop or a publication rewrites its book, so counting a run reads no `units` row.
CREATE TABLE IF NOT EXISTS unit_ngrams (
 first TEXT NOT NULL, size INTEGER NOT NULL, second TEXT NOT NULL, third TEXT, text TEXT, document TEXT,
 PRIMARY KEY (first, size)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS unit_ngram_second ON unit_ngrams(second);
CREATE INDEX IF NOT EXISTS unit_ngram_third ON unit_ngrams(third);
CREATE INDEX IF NOT EXISTS unit_ngram_text ON unit_ngrams(size,text);
CREATE INDEX IF NOT EXISTS unit_ngram_document ON unit_ngrams(document,size,text);
CREATE TRIGGER IF NOT EXISTS unit_ngram_follow AFTER UPDATE OF character,document ON units
BEGIN
  UPDATE unit_ngrams SET text=(SELECT character FROM units WHERE id=unit_ngrams.first)
    ||(SELECT character FROM units WHERE id=unit_ngrams.second)
    ||iif(third IS NULL,'',(SELECT character FROM units WHERE id=unit_ngrams.third))
    WHERE first=NEW.id OR second=NEW.id OR third=NEW.id;
  UPDATE unit_ngrams SET document=NEW.document WHERE first=NEW.id;
END;
CREATE TRIGGER IF NOT EXISTS unit_ngram_drop AFTER DELETE ON units
BEGIN
  DELETE FROM unit_ngrams WHERE first=OLD.id OR second=OLD.id OR third=OLD.id;
END;
-- The pairs recorded so far carry over; trigrams come with the next publication or backfill.
INSERT OR IGNORE INTO unit_ngrams(first,size,second,text,document) SELECT first,2,second,text,document FROM unit_pairs;
DROP TRIGGER IF EXISTS unit_pair_follow;
DROP TRIGGER IF EXISTS unit_pair_drop;
DROP TABLE IF EXISTS unit_pairs;
