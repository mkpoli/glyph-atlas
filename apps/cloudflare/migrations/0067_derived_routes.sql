-- A derived form makes up to two substitutions at once, each in a part of its own
-- (refs.derived_variants): `routes` is the JSON list of the ways it was reached, each a list of
-- [was, became] as made (was in the character, became in the form). The rows written before hold
-- single substitutions in another shape, so they go; scripts/export_character_variants.py fills the
-- table again.
DELETE FROM character_derived;
ALTER TABLE character_derived RENAME COLUMN subs TO routes;
