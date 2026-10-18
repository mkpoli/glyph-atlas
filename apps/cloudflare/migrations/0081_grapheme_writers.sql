-- The reduced forms that write several characters (shape_families.Family.writers), loaded by
-- scripts/export_character_variants.py from data/vocab/grapheme-families.tsv: one row per form and
-- grapheme it writes besides its own, both as text (后 writes 後, 弁 writes 辨, 辯 and 瓣). A card
-- reads the graphemes its character writes by `writer` and the forms that write its grapheme by
-- `grapheme`. Nothing here changes a family or widens a gallery.
CREATE TABLE IF NOT EXISTS grapheme_writers (
 writer TEXT NOT NULL, grapheme TEXT NOT NULL, PRIMARY KEY(writer,grapheme)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS grapheme_writer_grapheme ON grapheme_writers(grapheme);
