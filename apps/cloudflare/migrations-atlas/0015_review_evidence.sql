-- What each review saved on this site showed the reviewer and what its crop was published as, frozen in
-- a content-addressed R2 object (`v3/review-evidence/<sha256>.json.gz`) before the submission commits;
-- a failed write leaves an object nothing names. Reviews moved from the old site are archived by page
-- (`v3/review-legacy/<page>.json.gz`), found by the event's `legacy` id.
ALTER TABLE submissions ADD COLUMN evidence TEXT CHECK(evidence IS NULL OR (length(evidence)=64 AND evidence NOT GLOB '*[^0-9a-f]*'));

-- The revision the offline store gave a crop when a publication last spoke for it (`revision`), and the
-- crop's revision then (`at`). The crop's revision as the store counts it is `revision` plus every event
-- since but a mark (routes/review.ts `LOGICAL`). A publication writes a row; a crop without one counts its
-- revision less its marks.
CREATE TABLE review_revisions(crop INTEGER PRIMARY KEY, revision INTEGER NOT NULL, at INTEGER NOT NULL);

-- The review export reads reviews in the order they committed, without walking marks.
CREATE INDEX event_reviews ON events(id) WHERE kind IN (0,1,2);
