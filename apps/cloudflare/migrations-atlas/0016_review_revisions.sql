-- The revision the offline store gave a crop when a publication last spoke for it (`revision`), and the
-- crop's revision then (`at`). The crop's revision as the store counts it is `revision` plus every event
-- since that it counts: not a mark, nor a publication's own event (routes/review.ts `LOGICAL`). A crop no
-- publication has spoken for counts its revision less those events and less its `bumps`. A publication
-- that changes what a reviewer sees, or puts a crop back, records an event of its own (kind 8), the
-- boundary no earlier view or undo crosses; it writes a row here only when it speaks for the crop.
CREATE TABLE IF NOT EXISTS review_revisions(crop INTEGER PRIMARY KEY, revision INTEGER NOT NULL, at INTEGER NOT NULL);

-- The review export reads reviews in the order they committed, without walking marks.
CREATE INDEX IF NOT EXISTS event_reviews ON events(id) WHERE kind IN (0,1,2);

-- What a publication event set, as bits: 1 char, 2 state, 4 quiz, 8 cut, 16 kind, 32 flags. An undo
-- leaves those columns as the publication set them.
ALTER TABLE events ADD COLUMN published INTEGER;
-- Revisions a crop moved on with no event: a never-reviewed corpus glyph following a forms decision
-- (forms.ts followCorpus). The store counts none of them.
ALTER TABLE crops ADD COLUMN bumps INTEGER NOT NULL DEFAULT 0;
