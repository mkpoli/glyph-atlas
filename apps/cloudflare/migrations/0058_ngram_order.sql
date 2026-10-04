-- A run's page lists its occurrences handwritten first: by the style group of the run's first crop
-- (`units.style_order`, 0035), then by that crop's `shuffle`, which keeps the order stable without
-- grouping the occurrences by book. Both are copied onto the run so a page is one index range read in
-- order, with no sort and no unit row read to place a run. The triggers keep them in step with the
-- crop, whichever way a publication writes the runs.
ALTER TABLE unit_ngrams ADD COLUMN style_order INTEGER NOT NULL DEFAULT 1;
ALTER TABLE unit_ngrams ADD COLUMN shuffle INTEGER NOT NULL DEFAULT 0;
UPDATE unit_ngrams SET style_order=coalesce((SELECT style_order FROM units WHERE id=unit_ngrams.first),1),
  shuffle=coalesce((SELECT shuffle FROM units WHERE id=unit_ngrams.first),0);
CREATE TRIGGER IF NOT EXISTS unit_ngram_placed AFTER INSERT ON unit_ngrams
BEGIN
  UPDATE unit_ngrams SET style_order=coalesce((SELECT style_order FROM units WHERE id=NEW.first),1),
    shuffle=coalesce((SELECT shuffle FROM units WHERE id=NEW.first),0)
    WHERE first=NEW.first AND size=NEW.size;
END;
CREATE TRIGGER IF NOT EXISTS unit_ngram_restyled AFTER UPDATE OF style,shuffle ON units
BEGIN
  UPDATE unit_ngrams SET style_order=NEW.style_order, shuffle=NEW.shuffle WHERE first=NEW.id;
END;
-- The count and the page of a run read the same range: the run's text, then the order the page shows.
-- A book's runs lead with the book.
DROP INDEX IF EXISTS unit_ngram_text;
DROP INDEX IF EXISTS unit_ngram_document;
CREATE INDEX IF NOT EXISTS unit_ngram_order ON unit_ngrams(size,text,style_order,shuffle,first,vertical);
CREATE INDEX IF NOT EXISTS unit_ngram_work ON unit_ngrams(document,size,text,style_order,shuffle,first,vertical);
