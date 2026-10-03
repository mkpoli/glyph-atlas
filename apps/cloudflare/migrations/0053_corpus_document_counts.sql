-- How many glyphs the corpus holds of each document, for the statistics of the collection's dates
-- (0051): with `unit_counts` for the collection's own crops, a count by period is a join of a few
-- thousand rows and never reads the glyphs. `scripts/export_corpus_documents.py` writes it with the
-- glyphs' documents (0052), from the same corpora.
CREATE TABLE IF NOT EXISTS corpus_document_counts (document TEXT PRIMARY KEY, n INTEGER NOT NULL) WITHOUT ROWID;
