-- How many glyphs the corpus holds of each document, for the statistics of the collection's dates
-- (0051): with `unit_counts` for the collection's own crops, a count by period reads those two count
-- tables and never the glyphs. `scripts/export_corpus_documents.py` writes it with the glyphs'
-- documents (0052), a corpus publication with each document it publishes, and a withdrawal removes it.
CREATE TABLE IF NOT EXISTS corpus_document_counts (document TEXT PRIMARY KEY, n INTEGER NOT NULL) WITHOUT ROWID;
