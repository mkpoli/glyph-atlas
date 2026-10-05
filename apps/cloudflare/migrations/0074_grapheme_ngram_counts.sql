-- Explore counts runs by their graphemes (0071) rather than by the forms they are written in, so ん𛁅 and
-- ん𛁈 are one entry, んし, and a run's page lists the written forms it gathers. `ngram_forms` counts each
-- written text under its graphemes, on the whole site (`scope` '') and in each book (`scope` its
-- document), with how many of its occurrences are written down the page (`down`); a run's page reads its
-- forms most frequent first from `ngram_form_rank`. `ngram_counts` sums them per graphemes, with how many
-- forms each gathers (`forms`), and Explore reads its first page from `ngram_count_rank`. The counts by
-- written text (0060) go.
DROP TRIGGER IF EXISTS ngram_counted;
DROP TRIGGER IF EXISTS ngram_uncounted;
DROP TRIGGER IF EXISTS ngram_recounted;
DROP TABLE IF EXISTS ngram_counts;
CREATE TABLE IF NOT EXISTS ngram_forms (
 scope TEXT NOT NULL, size INTEGER NOT NULL, graphemes TEXT NOT NULL, text TEXT NOT NULL, n INTEGER NOT NULL, down INTEGER NOT NULL,
 PRIMARY KEY (scope,size,graphemes,text)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS ngram_form_rank ON ngram_forms(scope,size,graphemes,n DESC,text);
CREATE TABLE IF NOT EXISTS ngram_counts (
 scope TEXT NOT NULL, size INTEGER NOT NULL, graphemes TEXT NOT NULL, n INTEGER NOT NULL, down INTEGER NOT NULL, forms INTEGER NOT NULL,
 PRIMARY KEY (scope,size,graphemes)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS ngram_count_rank ON ngram_counts(scope,size,n DESC,graphemes,down,forms);
-- A form's count moves its graphemes' count; a form first seen adds one to the forms it gathers, and
-- one counted down to nothing takes one away and goes, its graphemes' row with it once it holds none.
CREATE TRIGGER IF NOT EXISTS ngram_form_added AFTER INSERT ON ngram_forms
BEGIN
  INSERT INTO ngram_counts(scope,size,graphemes,n,down,forms) VALUES(NEW.scope,NEW.size,NEW.graphemes,NEW.n,NEW.down,1)
    ON CONFLICT(scope,size,graphemes) DO UPDATE SET n=n+excluded.n,down=down+excluded.down,forms=forms+1;
END;
CREATE TRIGGER IF NOT EXISTS ngram_form_moved AFTER UPDATE OF n,down ON ngram_forms
BEGIN
  UPDATE ngram_counts SET n=n+NEW.n-OLD.n,down=down+NEW.down-OLD.down WHERE scope=NEW.scope AND size=NEW.size AND graphemes=NEW.graphemes;
END;
CREATE TRIGGER IF NOT EXISTS ngram_form_gone AFTER DELETE ON ngram_forms
BEGIN
  UPDATE ngram_counts SET n=n-OLD.n,down=down-OLD.down,forms=forms-1 WHERE scope=OLD.scope AND size=OLD.size AND graphemes=OLD.graphemes;
  DELETE FROM ngram_counts WHERE scope=OLD.scope AND size=OLD.size AND graphemes=OLD.graphemes AND forms<=0;
END;
-- A run is counted once it has a text and its graphemes, which its fold (0071) gives every run with a
-- text, a member the characters table does not know standing for itself. A run is written with no
-- graphemes and folded at once, so the fold's update counts it. A writer replaces a run by deleting it
-- first: `INSERT OR REPLACE` removes the old row without firing the delete trigger.
-- The runs written before this migration are counted by `scripts/backfill_ngram_counts.sh`, a slice at a
-- time in key order: millions of runs are more than one statement of a migration may read. Until it
-- reaches a run, the triggers leave that run to it: they count a run only up to the cursor
-- (`ngram_counts_backfill.after`), which the script moves past every run when it is done. A database with
-- no runs has nothing to count, and its cursor starts past every run.
CREATE TABLE IF NOT EXISTS ngram_counts_backfill (one INTEGER PRIMARY KEY CHECK(one = 1), after TEXT NOT NULL);
INSERT INTO ngram_counts_backfill(one,after) SELECT 1,iif(EXISTS(SELECT 1 FROM unit_ngrams),'',char(1114111))
  WHERE NOT EXISTS(SELECT 1 FROM ngram_counts_backfill);
CREATE TRIGGER IF NOT EXISTS ngram_counted AFTER INSERT ON unit_ngrams
  WHEN NEW.text IS NOT NULL AND NEW.graphemes IS NOT NULL AND NEW.first<=(SELECT after FROM ngram_counts_backfill)
BEGIN
  INSERT INTO ngram_forms(scope,size,graphemes,text,n,down) SELECT value,NEW.size,NEW.graphemes,NEW.text,1,NEW.vertical
    FROM json_each(json_array('',nullif(NEW.document,''))) WHERE value IS NOT NULL
    ON CONFLICT(scope,size,graphemes,text) DO UPDATE SET n=n+1,down=down+excluded.down;
END;
CREATE TRIGGER IF NOT EXISTS ngram_uncounted AFTER DELETE ON unit_ngrams
  WHEN OLD.text IS NOT NULL AND OLD.graphemes IS NOT NULL AND OLD.first<=(SELECT after FROM ngram_counts_backfill)
BEGIN
  UPDATE ngram_forms SET n=n-1,down=down-OLD.vertical
    WHERE scope IN ('',OLD.document) AND size=OLD.size AND graphemes=OLD.graphemes AND text=OLD.text;
  DELETE FROM ngram_forms WHERE scope IN ('',OLD.document) AND size=OLD.size AND graphemes=OLD.graphemes AND text=OLD.text AND n<=0;
END;
CREATE TRIGGER IF NOT EXISTS ngram_recounted AFTER UPDATE OF first,size,text,graphemes,vertical,document ON unit_ngrams
  WHEN OLD.first IS NOT NEW.first OR OLD.size IS NOT NEW.size OR OLD.text IS NOT NEW.text OR OLD.graphemes IS NOT NEW.graphemes
    OR OLD.vertical IS NOT NEW.vertical OR OLD.document IS NOT NEW.document
BEGIN
  UPDATE ngram_forms SET n=n-1,down=down-OLD.vertical
    WHERE OLD.text IS NOT NULL AND OLD.first<=(SELECT after FROM ngram_counts_backfill)
      AND scope IN ('',OLD.document) AND size=OLD.size AND graphemes=OLD.graphemes AND text=OLD.text;
  DELETE FROM ngram_forms WHERE OLD.text IS NOT NULL AND OLD.first<=(SELECT after FROM ngram_counts_backfill)
    AND scope IN ('',OLD.document) AND size=OLD.size AND graphemes=OLD.graphemes AND text=OLD.text AND n<=0;
  INSERT INTO ngram_forms(scope,size,graphemes,text,n,down) SELECT value,NEW.size,NEW.graphemes,NEW.text,1,NEW.vertical
    FROM json_each(json_array('',nullif(NEW.document,'')))
    WHERE value IS NOT NULL AND NEW.text IS NOT NULL AND NEW.graphemes IS NOT NULL AND NEW.first<=(SELECT after FROM ngram_counts_backfill)
    ON CONFLICT(scope,size,graphemes,text) DO UPDATE SET n=n+1,down=down+excluded.down;
END;
