-- When each document was written, copied, printed or composed. Every date a source states is a claim
-- of the assertion ledger (0048): subject the document, predicate `date_<kind>` (data/ledger.json), the
-- date as its value, tier attested, derived or editorial, asserted by `source:<id>` of its
-- data/sources record, and an evidence row naming that source and where in it (`scripts/
-- export_dates_cloudflare.py`). A date a source no longer states is retracted by the publication.
-- `document_dating` is what each axis shows for a document, resolved from those claims at publication
-- by `glyph_atlas.dates.resolve` (`resolver`) for the galleries to read by document: `witness` the
-- copy itself, `composed` its text. `claims` names the assertions it stands on. A publication writes
-- every row with its `export` stamp and then removes the rows of earlier ones.
CREATE TABLE IF NOT EXISTS document_dating (
 document TEXT NOT NULL, axis TEXT NOT NULL CHECK (axis IN ('witness','composed')),
 kind TEXT NOT NULL CHECK (kind IN ('composed','copied','colophon','annotated','printed','edition','exemplar','produced','other')),
 start INTEGER, end INTEGER, precision TEXT NOT NULL, qualifier TEXT, uncertain INTEGER NOT NULL DEFAULT 0,
 text TEXT NOT NULL, label TEXT NOT NULL, status TEXT NOT NULL CHECK (status IN ('single','agreed','disputed')),
 claims TEXT NOT NULL, calendar TEXT NOT NULL, conversion TEXT, source TEXT NOT NULL, resolver TEXT NOT NULL,
 export TEXT NOT NULL, PRIMARY KEY (document, axis), CHECK (start IS NULL OR end IS NULL OR start <= end)
) WITHOUT ROWID;
