-- The document each corpus glyph comes from, beside the pointer to its record. Its record names it too
-- (`source.document_id`), but a listing that reads no record, such as a form cluster's glyphs, needs it
-- in the row to show the book's dates (0052). A corpus publication writes it; the glyphs published
-- before it are filled by `scripts/export_corpus_documents.py`, in id ranges of one document each.
ALTER TABLE corpus_units ADD COLUMN document TEXT;
