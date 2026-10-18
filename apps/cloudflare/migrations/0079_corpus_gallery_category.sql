-- Each gallery copy's category, the script of its published character as `categoryOf` computes it,
-- so a script group deals its own corpus glyphs. Scripts the corpus holds few glyphs of (hangul,
-- gugyeol) are copied whole, whatever their shuffle; the rest keep the 1-in-64 sample.
-- `scripts/fill_corpus_gallery.py` writes the category of every copy it adds and of every copy made
-- before this column existed.
ALTER TABLE corpus_gallery ADD COLUMN category TEXT;
CREATE INDEX IF NOT EXISTS corpus_gallery_category ON corpus_gallery(category,shuffle);
