-- A decided glyph's grapheme follows what it is written as: the form a person named, or the character
-- it was reported as. 0016 and 0025 kept a family only for a report, so a 倣 glyph named 仿 would have
-- stayed in 倣's grapheme while its character became 仿. The columns now hold the family of either and
-- are named for it. Decisions that named a form before this get the form's family the way the Worker
-- finds one: the character table's grapheme, else the form's own code point; their corpus glyphs then
-- move to it, as the Worker moves them (`form_bases` keeps the family to restore on undo).
ALTER TABLE form_decisions RENAME COLUMN character_family TO written_family;
ALTER TABLE form_marks RENAME COLUMN character_family TO written_family;
ALTER TABLE form_units RENAME COLUMN issue_family TO written_family;
UPDATE form_decisions SET written_family=coalesce(
  (SELECT json_extract(c.data,'$.grapheme.code_point') FROM characters c WHERE c.character=form_decisions.form),
  printf('U+%04X',unicode(form))) WHERE form IS NOT NULL AND written_family IS NULL;
UPDATE form_marks SET written_family=(SELECT d.written_family FROM form_decisions d WHERE d.id=form_marks.decision)
  WHERE form IS NOT NULL AND written_family IS NULL;
UPDATE form_units SET cluster_family=coalesce(
  (SELECT json_extract(c.data,'$.grapheme.code_point') FROM characters c WHERE c.character=form_units.cluster_form),
  printf('U+%04X',unicode(cluster_form))) WHERE cluster_form IS NOT NULL AND cluster_family IS NULL;
UPDATE form_units SET glyph_family=coalesce(
  (SELECT json_extract(c.data,'$.grapheme.code_point') FROM characters c WHERE c.character=form_units.glyph_form),
  printf('U+%04X',unicode(glyph_form))) WHERE glyph_form IS NOT NULL AND glyph_family IS NULL;
UPDATE form_units SET written_family=CASE WHEN glyph_set=1 THEN glyph_family ELSE cluster_family END;
UPDATE corpus_units SET family=coalesce(f.written_family,b.family)
  FROM form_units f JOIN form_bases b ON b.id=f.id WHERE f.id=corpus_units.id AND corpus_units.named=0;
