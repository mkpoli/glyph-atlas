-- A glyph reported as another character joins that character's grapheme family
-- (`character_family`), and `form_bases` keeps the family it had, so taking the report back
-- restores both its character and its family.
ALTER TABLE form_decisions ADD COLUMN character_family TEXT;
ALTER TABLE form_units ADD COLUMN glyph_family TEXT;
ALTER TABLE form_marks ADD COLUMN character_family TEXT;
ALTER TABLE form_bases ADD COLUMN family TEXT;
UPDATE form_bases SET family=(SELECT family FROM corpus_units c WHERE c.id=form_bases.id);
