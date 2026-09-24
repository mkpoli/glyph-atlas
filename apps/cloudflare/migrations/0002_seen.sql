-- A quiz crop the reviewer was shown and left unflagged. Recording it keeps the crop out of later
-- rounds while it still has the box it was seen with. It changes neither the unit nor its revision,
-- so it needs no trigger, and an undone round's rows stop counting through its submission.
CREATE TABLE IF NOT EXISTS seen (
 target TEXT NOT NULL REFERENCES units(id), submission TEXT NOT NULL REFERENCES submissions(id),
 box TEXT, image_sha256 TEXT NOT NULL, at TEXT NOT NULL, PRIMARY KEY(target,submission)
);
CREATE INDEX IF NOT EXISTS seen_target ON seen(target);
