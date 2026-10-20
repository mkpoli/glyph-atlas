-- Undoing a review. An undo is an event of its own kind, setting the values the reversed event's
-- crop had before it; `reverses` names that event, so a reversed review stops counting as the crop's
-- standing one. A round's mark that its undo clears names the round's mark event, or none.
INSERT INTO codes(field, code, key) VALUES ('kind', 5, 'undo');
ALTER TABLE events ADD COLUMN reverses INTEGER;
CREATE INDEX event_reverses ON events(reverses) WHERE reverses IS NOT NULL;
-- What the crop held before the event, which an undo restores: its state, the issue it was flagged
-- with, and its redrawn box (NULL: the cut's own). The label before is `shown`.
ALTER TABLE events ADD COLUMN was_state INTEGER;
ALTER TABLE events ADD COLUMN was_issue INTEGER;
ALTER TABLE events ADD COLUMN was_x INTEGER;
ALTER TABLE events ADD COLUMN was_y INTEGER;
ALTER TABLE events ADD COLUMN was_w INTEGER;
ALTER TABLE events ADD COLUMN was_h INTEGER;
-- The issue a crop stays flagged with when the event's own answer names another: a batch correction
-- names only the character of a crop reported for its box, a blank or a merge, which stays reported.
-- The crop's issue is its last event's `kept_issue`, else its `issue`.
ALTER TABLE events ADD COLUMN kept_issue INTEGER;
-- An undo finds the crops its round skipped.
CREATE INDEX skip_submission ON skips(submission) WHERE submission IS NOT NULL;
