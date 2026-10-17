-- The pages a publication's crops were cut from, one row each: `data` is the page as the page view
-- shows it (`export_cloudflare.page_record`): its book, number, size in the page pixels boxes are given
-- in, its IIIF image service, the neighbouring pages that hold crops, and the ids of its crops in
-- reading order. A crop's label and box are read from its `units` row by id, so a review shows on the
-- page at once. Read by id alone, so the table has no other index.
CREATE TABLE IF NOT EXISTS pages (id TEXT PRIMARY KEY, document TEXT NOT NULL, data TEXT NOT NULL) WITHOUT ROWID;
