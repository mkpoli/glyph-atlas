-- A crop a publication retired in favour of another. A retired crop nobody reviewed, saw or skipped
-- is deleted; one they did stays with origin 'retired', out of every listing and round, so its history
-- still reads. Either way a request for it names its replacement, and a link to it opens that.
CREATE TABLE IF NOT EXISTS unit_redirects (id TEXT PRIMARY KEY, target TEXT NOT NULL) WITHOUT ROWID;
