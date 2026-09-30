-- What a crop's letterforms are written as, when a reviewer records that they differ from its character:
-- one character or an Ideographic Description Sequence (a 還 written 𮟃, or ⿺辶𦊷), as
-- `glyph_atlas.written_form` states it. It decides nothing: the crop keeps its character, grapheme,
-- state and revision, so a review a reader has open against it is still current after one is saved.
-- `written_forms` is the journal, a row a save; `units.written_form` is the crop's latest, which every
-- listing reads with the row. A save names the revision the reviewer saw, and one made after the crop
-- moved on is refused. A corpus glyph gets its `units` row the way its first review would give it one.
-- `target` holds no foreign key: a retired crop nobody reviewed is deleted (0014), and its forms stay in
-- the journal, which the export joins to the crops that remain.
ALTER TABLE units ADD COLUMN written_form TEXT;
CREATE TABLE IF NOT EXISTS written_forms (
 id TEXT PRIMARY KEY, submission TEXT NOT NULL UNIQUE, target TEXT NOT NULL,
 actor TEXT NOT NULL, revision INTEGER NOT NULL, pixels TEXT NOT NULL, label TEXT NOT NULL,
 form TEXT, request TEXT NOT NULL, at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS written_form_target ON written_forms(target);
CREATE TRIGGER IF NOT EXISTS written_form_revision_guard BEFORE INSERT ON written_forms
BEGIN
 SELECT RAISE(ABORT, 'written_form_revision_conflict')
 WHERE (SELECT revision FROM units WHERE id=NEW.target) IS NOT NEW.revision;
END;
CREATE TRIGGER IF NOT EXISTS written_form_apply AFTER INSERT ON written_forms
BEGIN
 UPDATE units SET written_form=NEW.form WHERE id=NEW.target;
END;
