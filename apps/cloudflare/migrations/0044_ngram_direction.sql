-- Whether a run's line is written down the page, as its line records it (`Line.vertical`); a line
-- written across is read left to right. Every run recorded so far is on a vertical line: the lines
-- published across the page hold no two neighbouring crops. The indexes carry it, so a count and a
-- page of occurrences still read no table row.
ALTER TABLE unit_ngrams ADD COLUMN vertical INTEGER NOT NULL DEFAULT 1;
DROP INDEX IF EXISTS unit_ngram_text;
DROP INDEX IF EXISTS unit_ngram_document;
CREATE INDEX IF NOT EXISTS unit_ngram_text ON unit_ngrams(size,text,first,vertical);
CREATE INDEX IF NOT EXISTS unit_ngram_document ON unit_ngrams(document,size,text,first,vertical);
