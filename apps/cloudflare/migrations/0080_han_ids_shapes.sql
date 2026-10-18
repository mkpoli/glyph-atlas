-- A character's regional shapes (/layers/structure `shapes`): the BabelStone descriptions of `han_ids`
-- that name different regions, as a JSON list of {text, regions} (所 as ⿰戶斤 GHTKPV and ⿰戸斤 J), or
-- NULL for a character with one. scripts/export_character_variants.py fills it with the descriptions.
ALTER TABLE han_ids ADD COLUMN shapes TEXT;
