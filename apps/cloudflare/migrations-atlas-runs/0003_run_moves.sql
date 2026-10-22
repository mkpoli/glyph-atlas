-- The runs a grapheme claim moved to another key, by their first crop and size, with the crop whose
-- claim moved them. A claim on a crop files its runs under the grapheme it reads the crop as
-- (`refileRuns`); the publisher takes these runs from D1 into its local copy before it compares, files
-- them again from the claims as they then stand once its own runs have landed, and drops the rows of
-- crops no claim reads any longer (scripts/atlas3/publish.py).
CREATE TABLE run_moves(first INTEGER, size INTEGER, crop INTEGER, PRIMARY KEY(first, size, crop)) WITHOUT ROWID;
