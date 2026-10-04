-- The derived tier by standing (refs.derived_variants). A substitution is attested
-- (data/vocab/han-component-variants.tsv, with its count and pairs) or editorial
-- (data/vocab/han-component-variants-editorial.tsv: no pairs, and `basis` the JSON statement of who
-- stated it, when, on what basis, and what the 異体字 graph says of it). A derived form makes up to
-- two substitutions at once, each in a part of its own: `routes` is the JSON list of the ways it was
-- reached, each a list of [was, became] as made (was in the character, became in the form), and
-- `tier` is editorial when its routes use an editorial substitution, attested otherwise. The rows
-- written before hold single substitutions in another shape, so they go;
-- scripts/export_character_variants.py fills the table again.
ALTER TABLE component_variants ADD COLUMN tier TEXT NOT NULL DEFAULT 'attested';
ALTER TABLE component_variants ADD COLUMN basis TEXT;
DELETE FROM character_derived;
ALTER TABLE character_derived RENAME COLUMN subs TO routes;
ALTER TABLE character_derived ADD COLUMN tier TEXT NOT NULL DEFAULT 'attested';
