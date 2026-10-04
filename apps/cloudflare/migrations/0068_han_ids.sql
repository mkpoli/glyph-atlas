-- What the form picker's IDS editor starts from (/layers/structure): `han_ids` holds each character's
-- descriptions (refs.ids_sequences, a JSON list of BabelStone sequences without region tags), read by
-- key, and the substitutes of a component are the `component_variants` rows naming it on either side,
-- read by key on `a` and by this index on `b`. scripts/export_character_variants.py fills `han_ids`.
CREATE TABLE IF NOT EXISTS han_ids (char TEXT PRIMARY KEY, sequences TEXT NOT NULL) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS component_variant_b ON component_variants(b);
