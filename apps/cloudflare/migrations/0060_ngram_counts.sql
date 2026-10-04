-- How often each text occurs as a run of each length, on the whole site (`scope` '') and in each book
-- (`scope` its document), which Explore's counts list most frequent first, and how many of those
-- occurrences are written down the page (`down`). Counting the runs for each request read every run of
-- the length asked for, or every run of the book; this reads the first page of `ngram_count_rank`.
-- The triggers keep it in step with every run written, removed, relabelled or filed under another
-- book. A writer replaces a run by deleting it first: `INSERT OR REPLACE` removes the old row without
-- firing the delete trigger.
CREATE TABLE IF NOT EXISTS ngram_counts (
 scope TEXT NOT NULL, size INTEGER NOT NULL, text TEXT NOT NULL, n INTEGER NOT NULL, down INTEGER NOT NULL,
 PRIMARY KEY (scope,size,text)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS ngram_count_rank ON ngram_counts(scope,size,n DESC,text,down);
INSERT INTO ngram_counts(scope,size,text,n,down) SELECT '',2,text,count(*),sum(vertical) FROM unit_ngrams WHERE size=2 AND text IS NOT NULL GROUP BY text;
INSERT INTO ngram_counts(scope,size,text,n,down) SELECT '',3,text,count(*),sum(vertical) FROM unit_ngrams WHERE size=3 AND text IS NOT NULL GROUP BY text;
INSERT INTO ngram_counts(scope,size,text,n,down) SELECT document,size,text,count(*),sum(vertical) FROM unit_ngrams
  WHERE document<>'' AND text IS NOT NULL GROUP BY document,size,text;
CREATE TRIGGER IF NOT EXISTS ngram_counted AFTER INSERT ON unit_ngrams WHEN NEW.text IS NOT NULL
BEGIN
  INSERT INTO ngram_counts(scope,size,text,n,down) SELECT value,NEW.size,NEW.text,1,NEW.vertical
    FROM json_each(json_array('',nullif(NEW.document,''))) WHERE value IS NOT NULL
    ON CONFLICT(scope,size,text) DO UPDATE SET n=n+1,down=down+excluded.down;
END;
CREATE TRIGGER IF NOT EXISTS ngram_uncounted AFTER DELETE ON unit_ngrams WHEN OLD.text IS NOT NULL
BEGIN
  UPDATE ngram_counts SET n=n-1,down=down-OLD.vertical WHERE scope IN ('',OLD.document) AND size=OLD.size AND text=OLD.text;
  DELETE FROM ngram_counts WHERE scope IN ('',OLD.document) AND size=OLD.size AND text=OLD.text AND n<=0;
END;
CREATE TRIGGER IF NOT EXISTS ngram_recounted AFTER UPDATE OF size,text,vertical,document ON unit_ngrams
  WHEN OLD.size IS NOT NEW.size OR OLD.text IS NOT NEW.text OR OLD.vertical IS NOT NEW.vertical OR OLD.document IS NOT NEW.document
BEGIN
  UPDATE ngram_counts SET n=n-1,down=down-OLD.vertical
    WHERE OLD.text IS NOT NULL AND scope IN ('',OLD.document) AND size=OLD.size AND text=OLD.text;
  DELETE FROM ngram_counts WHERE OLD.text IS NOT NULL AND scope IN ('',OLD.document) AND size=OLD.size AND text=OLD.text AND n<=0;
  INSERT INTO ngram_counts(scope,size,text,n,down) SELECT value,NEW.size,NEW.text,1,NEW.vertical
    FROM json_each(json_array('',nullif(NEW.document,''))) WHERE value IS NOT NULL AND NEW.text IS NOT NULL
    ON CONFLICT(scope,size,text) DO UPDATE SET n=n+1,down=down+excluded.down;
END;
