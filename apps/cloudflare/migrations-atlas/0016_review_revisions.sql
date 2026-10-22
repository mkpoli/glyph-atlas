-- The revision the offline store gave a crop when a publication last spoke for it (`revision`), and the
-- crop's revision then (`at`). The crop's revision as the store counts it is `revision` plus every event
-- since but a mark or a publication's re-cut (routes/review.ts `LOGICAL`). A publication writes a row; a
-- crop without one counts its revision less those events.
CREATE TABLE IF NOT EXISTS review_revisions(crop INTEGER PRIMARY KEY, revision INTEGER NOT NULL, at INTEGER NOT NULL);

-- The review export reads reviews in the order they committed, without walking marks.
CREATE INDEX IF NOT EXISTS event_reviews ON events(id) WHERE kind IN (0,1,2);
