-- Browse facets are cached per version of the data, and an undone round is part of that version: its
-- seen and skipped crops stop counting. The count of undone rounds is read from this index alone.
CREATE INDEX IF NOT EXISTS submission_undone ON submissions(id) WHERE undone=1;
