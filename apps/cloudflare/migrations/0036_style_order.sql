-- A character's or grapheme's gallery reads each of its ranges in style order, then id, so a page of it
-- needs no sort. These replace the indexes the galleries read in id order alone.
DROP INDEX IF EXISTS corpus_character;
DROP INDEX IF EXISTS unit_corpus_character;
CREATE INDEX IF NOT EXISTS corpus_character_style ON corpus_units(character,style_order,id);
CREATE INDEX IF NOT EXISTS corpus_family_style ON corpus_units(family,style_order,id);
CREATE INDEX IF NOT EXISTS unit_corpus_character_style ON units(character,style_order,id) WHERE origin='corpus';
CREATE INDEX IF NOT EXISTS unit_character_style ON units(origin,character,style_order,id);
CREATE INDEX IF NOT EXISTS unit_family_style ON units(origin,family,style_order,id);
