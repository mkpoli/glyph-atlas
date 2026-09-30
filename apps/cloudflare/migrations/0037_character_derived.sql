-- The derived tier of the variant graph, loaded by scripts/export_character_variants.py from
-- data/vocab/han-component-variants.tsv: substitutions two written variant pairs attest, applied
-- inside a character's own decomposition. `component_variants` is one substitution per row with its
-- count and every pair that attests it (the sources each pair states, as JSON); `character_derived`
-- is one prediction per row: a pair no source of the 異体字 graph states under any relation, its
-- sides in code point order, or a character and the sequence of a form no character has (the twelve
-- closest per character). `subs` is the JSON list of [was, became] the row came by, strongest first;
-- each substitution's count and pairs are the `component_variants` row. Citations, `derived-ids`
-- among them, are metadata `variant_sources`. None of this widens a gallery or merges a grapheme:
-- a page shows it as derived, beside the attested tiers.
CREATE TABLE IF NOT EXISTS component_variants (
 a TEXT NOT NULL, b TEXT NOT NULL, count INTEGER NOT NULL, pairs TEXT NOT NULL,
 PRIMARY KEY(a,b)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS character_derived (
 a TEXT NOT NULL, b TEXT NOT NULL, subs TEXT NOT NULL,
 PRIMARY KEY(a,b)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS character_derived_b ON character_derived(b);
