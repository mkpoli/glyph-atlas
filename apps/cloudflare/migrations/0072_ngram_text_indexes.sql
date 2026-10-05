-- Runs are found by their graphemes (0071), along the indexes that lead with them, and the Worker that
-- reads them is deployed. The indexes that led with the written text (0058, 0059) serve no query now:
-- Explore's counts are read from `ngram_counts`, and a book's runs are removed along the graphemes index
-- that leads with the book.
DROP INDEX IF EXISTS unit_ngram_order;
DROP INDEX IF EXISTS unit_ngram_work;
DROP INDEX IF EXISTS unit_ngram_source;
