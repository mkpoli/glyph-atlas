-- A reviewer's counts start from the crops they skipped during the rest, found by who and when.
CREATE INDEX IF NOT EXISTS skip_actor ON skips(actor,at);
