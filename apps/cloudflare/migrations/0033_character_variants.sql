-- The 異体字 graph, one row per edge per source as data/vocab/kanji-variants.tsv states it
-- (scripts/export_character_variants.py loads it; each source's citation is metadata
-- `variant_sources`). `written` marks the edges under which one ideograph may be written for the
-- other (refs.WRITTEN_FOR), `widens` those of them a character's gallery widens to (all but
-- refs.NOT_WIDENED: simplified, specialized-semantic); the rest relate different characters
-- (borrowed, substitute, …). A character card reads a character's edges in both directions: `a` by
-- the key, `b` by its index.
CREATE TABLE IF NOT EXISTS character_variants (
 a TEXT NOT NULL, b TEXT NOT NULL, relation TEXT NOT NULL, source TEXT NOT NULL, detail TEXT NOT NULL,
 written INTEGER NOT NULL, widens INTEGER NOT NULL,
 PRIMARY KEY(a,b,relation,source)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS character_variant_b ON character_variants(b);
