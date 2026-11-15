-- The crops a write of the Worker changed since a publication last took them, each with the generation
-- of its latest such write. Every batch that changes a crop's row lists the crop here in the same batch
-- (routes/review.ts `applyStatements` and `setStyle`, routes/forms.ts `followStatements`). The answers
-- publication (scripts/atlas3/answers.py) copies each listed crop's row into the publisher's local copy,
-- which its counts are built from, and takes the crop off the list.
CREATE TABLE IF NOT EXISTS touched(crop INTEGER PRIMARY KEY, gen INTEGER NOT NULL) WITHOUT ROWID;

-- A publication's snapshot of the listed crops, taken once it has bumped the generation from `through`:
-- `row` is the crop's row as a JSON object of its columns, with the revision the offline store counts for
-- it (`logical`, routes/review.ts `LOGICAL`) and whether it keeps a reviewer's style (`own_style`), or
-- NULL when the database no longer holds the crop. A row stays until the local copy has taken it, so a
-- publication that stopped is finished by the next.
CREATE TABLE IF NOT EXISTS absorbing(crop INTEGER PRIMARY KEY, through INTEGER NOT NULL, row TEXT) WITHOUT ROWID;
