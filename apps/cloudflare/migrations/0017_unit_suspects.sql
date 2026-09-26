-- Quick review can show only the crops the character classifier doubts (see `atlas review suspects`).
-- A row marks one crop, local or corpus, by id: the probability the classifier gives its label and the
-- character it reads it as, when one stands out. A crop without a row is not a suspect.
CREATE TABLE IF NOT EXISTS unit_suspects (id TEXT PRIMARY KEY, p REAL NOT NULL, reads_as TEXT) WITHOUT ROWID;
