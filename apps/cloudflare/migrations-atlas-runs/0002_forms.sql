-- How each run is written, and the counts a run's page and Explore's grid read beside `run_stats`,
-- moved from the old database's `unit_ngrams`, `ngram_forms` and `ngram_counts` (0074) at the cutover by
-- scripts/atlas3/runs.py and kept by the publisher. All change at a publication, as the runs do.

-- `form` is the run's text as its crops are written (ん𛁅), where it differs from its graphemes (んし);
-- NULL where the crops are written as their graphemes, and for a run with no text (`graphemes` '').
-- `vertical` is 1 for a run whose line is written down the page; every run loaded before this
-- migration is.
ALTER TABLE runs ADD COLUMN form TEXT;
ALTER TABLE runs ADD COLUMN vertical INTEGER NOT NULL DEFAULT 1;

-- The site's count of each written form that differs from its graphemes, with how many of its runs
-- are written down the page. The form written as the graphemes themselves counts what `run_stats`
-- holds beyond these rows. A run's page reads one key range.
CREATE TABLE run_forms(size INTEGER NOT NULL, graphemes TEXT NOT NULL, form TEXT NOT NULL, n INTEGER NOT NULL,
  down INTEGER NOT NULL, PRIMARY KEY(size, graphemes, form)) WITHOUT ROWID;

-- Each book's most frequent runs of each length, by graphemes, as many as Explore's grid shows (480,
-- ties by graphemes), with how many are written down the page. A book's grid reads one key range.
CREATE TABLE run_books(book INTEGER NOT NULL, size INTEGER NOT NULL, graphemes TEXT NOT NULL, n INTEGER NOT NULL,
  down INTEGER NOT NULL, PRIMARY KEY(book, size, graphemes)) WITHOUT ROWID;
