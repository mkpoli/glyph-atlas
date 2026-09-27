-- Two crops that follow each other on a line, the occurrences Explore's two-character frequencies
-- count. A publication writes the ids (`glyph_atlas.unit_pairs`); `text` is the two crops' labels as
-- the site holds them and `document` the first crop's book. The triggers keep both in step when a
-- review relabels a crop or a publication rewrites its book, so counting a pair reads no `units` row.
CREATE TABLE IF NOT EXISTS unit_pairs (
 first TEXT PRIMARY KEY, second TEXT NOT NULL, text TEXT, document TEXT
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS unit_pair_second ON unit_pairs(second);
CREATE INDEX IF NOT EXISTS unit_pair_text ON unit_pairs(text);
CREATE INDEX IF NOT EXISTS unit_pair_document ON unit_pairs(document,text);
CREATE TRIGGER IF NOT EXISTS unit_pair_follow AFTER UPDATE OF character,document ON units
BEGIN
  UPDATE unit_pairs SET text=NEW.character||(SELECT character FROM units WHERE id=unit_pairs.second),
    document=NEW.document WHERE first=NEW.id;
  UPDATE unit_pairs SET text=(SELECT character FROM units WHERE id=unit_pairs.first)||NEW.character
    WHERE second=NEW.id;
END;
CREATE TRIGGER IF NOT EXISTS unit_pair_drop AFTER DELETE ON units
BEGIN
  DELETE FROM unit_pairs WHERE first=OLD.id;
  DELETE FROM unit_pairs WHERE second=OLD.id;
END;
