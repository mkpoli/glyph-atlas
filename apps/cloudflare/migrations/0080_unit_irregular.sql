-- Crops whose box is far narrower or wider than their character's other crops (`atlas review irregular`),
-- which the 要修正 view lists after the flagged ones until someone reviews them. `shape` is how far the
-- box's width-to-height ratio lies from its character's, in robust standard deviations: below zero
-- narrower, above wider. `label` and the box are what was measured: a crop relabelled or re-cut since
-- is no longer irregular.
CREATE TABLE IF NOT EXISTS unit_irregular (id TEXT PRIMARY KEY, label TEXT NOT NULL, x REAL NOT NULL, y REAL NOT NULL,
 w REAL NOT NULL, h REAL NOT NULL, shape REAL NOT NULL) WITHOUT ROWID;
