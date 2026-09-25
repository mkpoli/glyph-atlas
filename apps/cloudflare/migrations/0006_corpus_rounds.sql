-- Quick review deals corpus glyphs after a character's local crops. A glyph is dealt from
-- `corpus_units` until a round or a review first names it; from then on it has a `units` row with
-- origin 'corpus' and is dealt and counted like a local crop.
-- `production` is the record's own; a row published before the column existed reads as unknown
-- until the next corpus publication rewrites it.
ALTER TABLE corpus_units ADD COLUMN production TEXT NOT NULL DEFAULT 'unknown';
-- A round reads one character's glyphs in shuffle order from a seeded point, across all materials
-- or one of them.
CREATE INDEX IF NOT EXISTS corpus_round ON corpus_units(character,shuffle);
CREATE INDEX IF NOT EXISTS corpus_material ON corpus_units(character,production,shuffle);
-- How many assigned glyphs each character has per material, so counting a round's categories
-- reads this table and not a million corpus rows. Every publication that rewrites `corpus_units`
-- regenerates it with the same statement.
CREATE TABLE IF NOT EXISTS corpus_characters (
 character TEXT NOT NULL, production TEXT NOT NULL, n INTEGER NOT NULL, PRIMARY KEY(character,production)
) WITHOUT ROWID;
DELETE FROM corpus_characters;
INSERT INTO corpus_characters SELECT character,production,count(*) FROM corpus_units
 WHERE character IS NOT NULL GROUP BY character,production;
-- `quiz` says whether a crop may be dealt at all, for both origins; its review state says whether
-- it is due. A corpus glyph may be dealt when its record is proxyable and names a written character.
UPDATE units SET quiz=iif(json_extract(data,'$.proxyable') AND json_extract(data,'$.written_character') IS NOT NULL,1,0)
 WHERE origin='corpus';
