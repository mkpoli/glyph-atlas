-- Each document the corpus publishes, with how many glyphs it holds (0054) and its title, so a page
-- that lists books names them from one indexed read rather than from a glyph's published record. A
-- corpus publication writes both with each document it publishes, keeping a title its export does not
-- know, and a withdrawal removes the row. Renaming and adding a column rewrite no rows.
ALTER TABLE corpus_document_counts RENAME TO corpus_documents;
ALTER TABLE corpus_documents ADD COLUMN title TEXT;
