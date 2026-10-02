-- The derived tier of the variant graph, loaded by scripts/export_character_variants.py from
-- data/vocab/han-component-variants.tsv: substitutions two written variant pairs attest, applied
-- inside a character's own decomposition. `component_variants` is one substitution per row with its
-- count and every pair that attests it (the sources each pair states, as JSON); `character_derived`
-- is one row per entry of a character's derived list (refs.derived_variants), `rank` its place in
-- that list: another character no source of the 異体字 graph relates to it, or the sequence of a
-- form no character has. `subs` is the JSON list of [was, became] the row came by, strongest first;
-- each substitution's count and pairs are the `component_variants` row. Citations, `derived-ids`
-- among them, are metadata `variant_sources`. None of this widens a gallery or merges a grapheme:
-- a page shows it as derived, beside the attested tiers.
CREATE TABLE IF NOT EXISTS component_variants (
 a TEXT NOT NULL, b TEXT NOT NULL, count INTEGER NOT NULL, pairs TEXT NOT NULL,
 PRIMARY KEY(a,b)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS character_derived (
 a TEXT NOT NULL, rank INTEGER NOT NULL, b TEXT NOT NULL, subs TEXT NOT NULL,
 PRIMARY KEY(a,rank)
) WITHOUT ROWID;
