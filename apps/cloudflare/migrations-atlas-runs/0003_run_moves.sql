-- The runs a grapheme claim moved to another key since the cutover, by their first crop and size. A
-- claim on a crop files its runs under the grapheme it reads the crop as (`refileRuns`); the publisher
-- reads these rows back from D1 before it publishes the runs again (scripts/atlas3/publish.py).
CREATE TABLE run_moves(first INTEGER, size INTEGER, PRIMARY KEY(first, size)) WITHOUT ROWID;
