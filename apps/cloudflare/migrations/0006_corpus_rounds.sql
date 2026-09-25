-- Quick review deals corpus glyphs after a character's local crops. A glyph is dealt from
-- `corpus_units` while it is `named=0`. The first round or review that names it writes its `units`
-- row with origin 'corpus', and from then on it is dealt like a local crop, for its own character only.
ALTER TABLE corpus_units ADD COLUMN production TEXT NOT NULL DEFAULT 'unknown';
ALTER TABLE corpus_units ADD COLUMN named INTEGER NOT NULL DEFAULT 0;
-- A publication writes each record's own material. Until the next one, the only source whose material
-- is certain is the old movable-type set; the rest read as unknown. The key range is every id that
-- starts with `codh-omt:` (char(59) is the ';' that follows ':').
UPDATE corpus_units SET production='movable-type' WHERE id>='codh-omt:' AND id<'codh-omt'||char(59);
UPDATE corpus_units SET named=1 WHERE id IN (SELECT id FROM units WHERE origin='corpus');
-- A round reads one character's untouched glyphs in shuffle order from a seeded point, across all
-- materials or one of them, so LIMIT bounds what it reads however many glyphs are named.
CREATE INDEX IF NOT EXISTS corpus_round ON corpus_units(character,named,shuffle);
CREATE INDEX IF NOT EXISTS corpus_material ON corpus_units(character,production,named,shuffle);
-- Assigned glyphs per character and material, and how many of them are named, so a round's category
-- counts read this table and nothing that grows with reviewing. Every publication that rewrites
-- `corpus_units` restores `named` and regenerates it with these statements.
CREATE TABLE IF NOT EXISTS corpus_characters (
 character TEXT NOT NULL, production TEXT NOT NULL, n INTEGER NOT NULL, named INTEGER NOT NULL,
 PRIMARY KEY(character,production)
) WITHOUT ROWID;
DELETE FROM corpus_characters;
INSERT INTO corpus_characters SELECT character,production,count(*),sum(named) FROM corpus_units
 WHERE character IS NOT NULL GROUP BY character,production;
-- Naming a glyph and counting it happen together: an ignored insert fires nothing.
CREATE TRIGGER IF NOT EXISTS corpus_named AFTER INSERT ON units WHEN NEW.origin='corpus'
BEGIN
 UPDATE corpus_characters SET named=named+1
  WHERE (character,production)=(SELECT character,production FROM corpus_units WHERE id=NEW.id AND named=0);
 UPDATE corpus_units SET named=1 WHERE id=NEW.id;
END;
-- `quiz` says whether a crop may be dealt at all, for both origins; its review state says whether it
-- is due. A corpus glyph may be dealt when its record is proxyable and names a written character.
-- Its category and shuffle are the ones the Worker writes when it names a glyph: the script of the
-- label's first character, and the published row's shuffle.
UPDATE units SET quiz=iif(json_extract(data,'$.proxyable') AND json_extract(data,'$.written_character') IS NOT NULL,1,0),
 shuffle=coalesce((SELECT c.shuffle FROM corpus_units c WHERE c.id=units.id),shuffle)
 WHERE origin='corpus';
UPDATE units SET category=k.category FROM (SELECT id,CASE
  WHEN c BETWEEN 0x3041 AND 0x3096 OR c BETWEEN 0x309D AND 0x309F OR c BETWEEN 0x30A1 AND 0x30FA
   OR c BETWEEN 0x30FD AND 0x30FF OR c BETWEEN 0x31F0 AND 0x31FF OR c BETWEEN 0x32D0 AND 0x32FE
   OR c BETWEEN 0x3300 AND 0x3357 OR c BETWEEN 0xFF66 AND 0xFF6F OR c BETWEEN 0xFF71 AND 0xFF9D
   OR c BETWEEN 0x1AFF0 AND 0x1AFF3 OR c BETWEEN 0x1AFF5 AND 0x1AFFB OR c BETWEEN 0x1AFFD AND 0x1AFFE
   OR c BETWEEN 0x1B000 AND 0x1B122 OR c=0x1B132 OR c BETWEEN 0x1B150 AND 0x1B152 OR c=0x1B155
   OR c BETWEEN 0x1B164 AND 0x1B167 OR c=0x1F200 THEN 'kana'
  WHEN c BETWEEN 0x2E80 AND 0x2E99 OR c BETWEEN 0x2E9B AND 0x2EF3 OR c BETWEEN 0x2F00 AND 0x2FD5
   OR c=0x3005 OR c=0x3007 OR c BETWEEN 0x3021 AND 0x3029 OR c BETWEEN 0x3038 AND 0x303B
   OR c BETWEEN 0x3400 AND 0x4DBF OR c BETWEEN 0x4E00 AND 0x9FFF OR c BETWEEN 0xF900 AND 0xFA6D
   OR c BETWEEN 0xFA70 AND 0xFAD9 OR c BETWEEN 0x16FE2 AND 0x16FE3 OR c BETWEEN 0x16FF0 AND 0x16FF6
   OR c BETWEEN 0x20000 AND 0x2A6DF OR c BETWEEN 0x2A700 AND 0x2B81D OR c BETWEEN 0x2B820 AND 0x2CEAD
   OR c BETWEEN 0x2CEB0 AND 0x2EBE0 OR c BETWEEN 0x2EBF0 AND 0x2EE5D OR c BETWEEN 0x2F800 AND 0x2FA1D
   OR c BETWEEN 0x30000 AND 0x3134A OR c BETWEEN 0x31350 AND 0x33479 THEN 'kanji'
  ELSE 'other' END AS category
 FROM (SELECT id,unicode(json_extract(data,'$.label')) AS c FROM units WHERE origin='corpus')) k
 WHERE units.id=k.id;
