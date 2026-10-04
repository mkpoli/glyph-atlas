-- The crops each user has starred, newest first on their favourites page. A crop a publication retires
-- keeps its row; the page shows the crop that replaced it.
CREATE TABLE IF NOT EXISTS favourites (
  user_id TEXT NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
  unit TEXT NOT NULL,
  at TEXT NOT NULL,
  PRIMARY KEY (user_id, unit)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS favourites_recent ON favourites(user_id, at DESC, unit);
