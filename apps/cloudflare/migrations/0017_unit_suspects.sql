-- Quick review can show only the crops the character classifier doubts (see `atlas review suspects`).
-- A row marks one crop, local or corpus, by id: the probability the classifier gives its label and the
-- character it reads it as, when one stands out. `label` and `box` are what the classifier was shown;
-- a crop relabelled or re-cut since then is no longer a suspect. A crop without a row is not one.
CREATE TABLE IF NOT EXISTS unit_suspects (id TEXT PRIMARY KEY, p REAL NOT NULL, reads_as TEXT, label TEXT NOT NULL,
 box TEXT) WITHOUT ROWID;
