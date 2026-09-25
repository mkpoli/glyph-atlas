-- A document's characters in source order, for a site that shows each occurrence where it stands on
-- its page (rec.aynu.org's character pages). `data` is what the publication knows of the occurrence:
-- page, line, block, position, context, box, label and where the label came from. Where the site
-- publishes the unit, its current reading is read from `units` instead, so a review shows at once.
CREATE TABLE IF NOT EXISTS document_characters (
 document TEXT NOT NULL, ord INTEGER NOT NULL, unit TEXT NOT NULL, data TEXT NOT NULL,
 PRIMARY KEY(document, ord)
) WITHOUT ROWID;
