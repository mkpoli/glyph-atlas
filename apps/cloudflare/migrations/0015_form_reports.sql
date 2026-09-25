-- A glyph decision can report that a glyph is not its family's character (`issue` 'character', with
-- `character` saying what it is when known) or that its crop is bad (`issue` 'crop'). The glyph keeps
-- no form; `form_units` carries the report so lists and counts need no join, and a republished
-- clustering replays it through `form_marks` like any other decision. A glyph reported as another
-- character moves to that character's family (`character_family`), and `form_bases` keeps the family
-- it had, so taking the report back restores both.
ALTER TABLE form_decisions ADD COLUMN issue TEXT;
ALTER TABLE form_decisions ADD COLUMN character TEXT;
ALTER TABLE form_decisions ADD COLUMN character_family TEXT;
ALTER TABLE form_units ADD COLUMN glyph_issue TEXT;
ALTER TABLE form_units ADD COLUMN glyph_character TEXT;
ALTER TABLE form_units ADD COLUMN glyph_family TEXT;
ALTER TABLE form_marks ADD COLUMN issue TEXT;
ALTER TABLE form_marks ADD COLUMN character TEXT;
ALTER TABLE form_marks ADD COLUMN character_family TEXT;
ALTER TABLE form_bases ADD COLUMN family TEXT;
UPDATE form_bases SET family=(SELECT family FROM corpus_units c WHERE c.id=form_bases.id);
ALTER TABLE form_families ADD COLUMN rejected INTEGER NOT NULL DEFAULT 0;
