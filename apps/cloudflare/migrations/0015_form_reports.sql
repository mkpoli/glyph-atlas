-- A glyph decision can report that a glyph is not its family's character (`issue` 'character', with
-- `character` saying what it is when known) or that its crop is bad (`issue` 'crop'). The glyph keeps
-- no form; `form_units` carries the report so lists and counts need no join, and a republished
-- clustering replays it through `form_marks` like any other decision.
ALTER TABLE form_decisions ADD COLUMN issue TEXT;
ALTER TABLE form_decisions ADD COLUMN character TEXT;
ALTER TABLE form_units ADD COLUMN glyph_issue TEXT;
ALTER TABLE form_units ADD COLUMN glyph_character TEXT;
ALTER TABLE form_marks ADD COLUMN issue TEXT;
ALTER TABLE form_marks ADD COLUMN character TEXT;
ALTER TABLE form_families ADD COLUMN rejected INTEGER NOT NULL DEFAULT 0;
