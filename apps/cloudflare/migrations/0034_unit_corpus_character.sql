-- A corpus category's glyphs a person moved into it, in character and id order: a gallery widened to a
-- character's variants merges them with the category's own glyphs (`corpus_character`) without
-- sorting. Partial, so the local crops' plans (`unit_character`) are unchanged.
CREATE INDEX IF NOT EXISTS unit_corpus_character ON units(character,id) WHERE origin='corpus';
