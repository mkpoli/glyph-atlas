-- Undone submissions, which the catalogue stamps count (`atlas3Stamps.ts`): an undo of a round with no
-- review changes nothing else. The index holds only undone rows, so the count reads only those.
CREATE INDEX submission_undone ON submissions(id) WHERE undone = 1;
