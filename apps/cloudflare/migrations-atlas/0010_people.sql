-- Review history, the ranking, favourites and the admin pages on glyph-atlas-v3 (decision record 0006).

-- What a review showed and named, beside what it set: `label` is the grapheme the crop has after the
-- event, `shown` the one the reviewer was shown, `named` the character the reviewer named (NULL when
-- none). A named character can leave the label as it was, when the crop's written character was not its
-- label; `text` holds the characters typed for a joined crop, which name no grapheme. An undo shows what
-- the event it reverses showed and names nothing.
ALTER TABLE events ADD COLUMN shown INTEGER;
ALTER TABLE events ADD COLUMN named INTEGER;
-- An admin's flag on a corpus glyph is an event too, so each revision a crop moves has its event.
INSERT INTO codes(field, code, key) VALUES ('kind', 6, 'flag');

-- The history page, newest first: one reviewer's events, and the events showing one label.
CREATE INDEX event_actor_history ON events(actor, at, id);
CREATE INDEX event_shown ON events(shown, at, id) WHERE shown IS NOT NULL;
-- Rounds that passed crops, by the label they were dealt for. A request too long for one statement is
-- loaded in pieces, so the expression reads only one that is whole JSON.
CREATE INDEX submission_label ON submissions(iif(json_valid(request), json_extract(request, '$.input.label'), NULL), at, id)
  WHERE undone = 0;

-- A user's favourites newest first, ties by crop, as the page lists them, read without a sort.
DROP INDEX favourite_recent;
CREATE INDEX favourite_newest ON favourites(user, at DESC, crop);

-- The ranking reads each person's claims and actions by index, never the publisher's claims.
CREATE INDEX assertion_person ON assertions(asserted_by, submission)
  WHERE submission IS NOT NULL AND legacy IS NULL AND predicate <> 'represented_by';
CREATE INDEX action_actor ON assertion_actions(action, actor, assertion);
CREATE INDEX form_decision_actor ON form_decisions(actor);

-- Corpus glyphs anything was written about, in the order the first write named each: a review, a round
-- that showed or skipped it, a claim on it or a flag adds its row. The Flagged view lists flagged
-- glyphs in this order, and a flag leaves a glyph with a row as it is.
CREATE TABLE corpus_rows(crop INTEGER PRIMARY KEY, seq INTEGER NOT NULL UNIQUE);
-- The finding behind an admin's flag on a corpus glyph: which check found it, and the characters the
-- realignment and the transcription read there.
CREATE TABLE corpus_flags(crop INTEGER PRIMARY KEY, finding TEXT NOT NULL CHECK(length(finding) <= 64),
  realigned TEXT, transcribed TEXT, at INTEGER NOT NULL);
-- Flagged corpus glyphs, which the Flagged view reads.
CREATE INDEX crop_corpus_flagged ON crops(id) WHERE kind = 1 AND state = 2;
