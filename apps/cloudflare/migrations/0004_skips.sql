-- A quiz crop the reviewer was shown and skipped: no decision, recorded against the reviewer. Other
-- reviewers are dealt it first, the one who skipped it only after a rest, and a crop two reviewers
-- skipped at the same box is hard and leaves the rounds. An undone round's rows stop counting.
CREATE TABLE IF NOT EXISTS skips (
 target TEXT NOT NULL REFERENCES units(id), submission TEXT NOT NULL REFERENCES submissions(id),
 actor TEXT NOT NULL, box TEXT, image_sha256 TEXT NOT NULL, at TEXT NOT NULL, PRIMARY KEY(target,submission)
);
CREATE INDEX IF NOT EXISTS skip_target ON skips(target,actor);
