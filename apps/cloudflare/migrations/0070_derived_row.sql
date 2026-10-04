-- A character's derived forms as one row (refs.derived_row): `forms` is the JSON list
-- [[form, routes], …] in rank order, each route a list of [was, became] as made. One row per character
-- keeps a full export at one row write per character, and lets scripts/export_character_variants.py
-- replace one key range of characters per import. The table is empty until that export runs.
DROP TABLE IF EXISTS character_derived;
CREATE TABLE character_derived (a TEXT PRIMARY KEY, forms TEXT NOT NULL) WITHOUT ROWID;
