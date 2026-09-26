-- The homepage gallery's records, copied out of the R2 record packs for the corpus glyphs whose shuffle
-- falls below 2^22 (1 in 64), so a gallery page is one query. Packs are content-addressed, so a copy is
-- current exactly while `corpus_units` names the same object and offset; the Worker joins on both.
-- `scripts/fill_corpus_gallery.py` fills it after a publication.
CREATE TABLE IF NOT EXISTS corpus_gallery (
 id TEXT PRIMARY KEY, shuffle INTEGER NOT NULL, object TEXT NOT NULL, offset INTEGER NOT NULL, data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS corpus_gallery_order ON corpus_gallery(shuffle);
