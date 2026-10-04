-- The runs of corpus glyphs share `unit_ngrams` with the crops' (0043): a corpus publication writes
-- them (`scripts/export_corpus_ngrams.py`), and the counts, a run's occurrences and its probes read both
-- alike. A run is never part crop, part glyph: its members stand on one line of one dataset.
-- A member's character is the one the site shows for it: its `units` row's where it has one (every
-- crop, and a corpus glyph a round or review named), else its `corpus_units` row's, which a form
-- decision moves (`followCorpus`). The triggers below keep a run's text in step through all of these.
DROP TRIGGER IF EXISTS unit_ngram_follow;
CREATE TRIGGER IF NOT EXISTS unit_ngram_follow AFTER UPDATE OF character,document ON units
BEGIN
  UPDATE unit_ngrams SET text=iif(EXISTS(SELECT 1 FROM units WHERE id=unit_ngrams.first),(SELECT character FROM units WHERE id=unit_ngrams.first),(SELECT character FROM corpus_units WHERE id=unit_ngrams.first))
    ||iif(EXISTS(SELECT 1 FROM units WHERE id=unit_ngrams.second),(SELECT character FROM units WHERE id=unit_ngrams.second),(SELECT character FROM corpus_units WHERE id=unit_ngrams.second))
    ||iif(third IS NULL,'',iif(EXISTS(SELECT 1 FROM units WHERE id=unit_ngrams.third),(SELECT character FROM units WHERE id=unit_ngrams.third),(SELECT character FROM corpus_units WHERE id=unit_ngrams.third)))
    WHERE first=NEW.id OR second=NEW.id OR third=NEW.id;
  UPDATE unit_ngrams SET document=NEW.document WHERE first=NEW.id AND NEW.origin<>'corpus';
END;
-- A glyph a round names gets its `units` row, whose character then stands for it.
CREATE TRIGGER IF NOT EXISTS unit_ngram_named AFTER INSERT ON units WHEN NEW.origin='corpus'
BEGIN
  UPDATE unit_ngrams SET text=iif(EXISTS(SELECT 1 FROM units WHERE id=unit_ngrams.first),(SELECT character FROM units WHERE id=unit_ngrams.first),(SELECT character FROM corpus_units WHERE id=unit_ngrams.first))
    ||iif(EXISTS(SELECT 1 FROM units WHERE id=unit_ngrams.second),(SELECT character FROM units WHERE id=unit_ngrams.second),(SELECT character FROM corpus_units WHERE id=unit_ngrams.second))
    ||iif(third IS NULL,'',iif(EXISTS(SELECT 1 FROM units WHERE id=unit_ngrams.third),(SELECT character FROM units WHERE id=unit_ngrams.third),(SELECT character FROM corpus_units WHERE id=unit_ngrams.third)))
    WHERE first=NEW.id OR second=NEW.id OR third=NEW.id;
END;
-- A crop that leaves the site takes its runs along; a corpus glyph's `units` row going leaves the
-- glyph, which reads its published character again.
DROP TRIGGER IF EXISTS unit_ngram_drop;
CREATE TRIGGER IF NOT EXISTS unit_ngram_drop AFTER DELETE ON units WHEN OLD.origin<>'corpus'
BEGIN
  DELETE FROM unit_ngrams WHERE first=OLD.id OR second=OLD.id OR third=OLD.id;
END;
CREATE TRIGGER IF NOT EXISTS unit_ngram_unnamed AFTER DELETE ON units WHEN OLD.origin='corpus'
BEGIN
  UPDATE unit_ngrams SET text=iif(EXISTS(SELECT 1 FROM units WHERE id=unit_ngrams.first),(SELECT character FROM units WHERE id=unit_ngrams.first),(SELECT character FROM corpus_units WHERE id=unit_ngrams.first))
    ||iif(EXISTS(SELECT 1 FROM units WHERE id=unit_ngrams.second),(SELECT character FROM units WHERE id=unit_ngrams.second),(SELECT character FROM corpus_units WHERE id=unit_ngrams.second))
    ||iif(third IS NULL,'',iif(EXISTS(SELECT 1 FROM units WHERE id=unit_ngrams.third),(SELECT character FROM units WHERE id=unit_ngrams.third),(SELECT character FROM corpus_units WHERE id=unit_ngrams.third)))
    WHERE first=OLD.id OR second=OLD.id OR third=OLD.id;
END;
-- A decision that moves an unnamed glyph's character, and a publication that files it under a book.
CREATE TRIGGER IF NOT EXISTS corpus_ngram_follow AFTER UPDATE OF character ON corpus_units WHEN OLD.character IS NOT NEW.character
BEGIN
  UPDATE unit_ngrams SET text=iif(EXISTS(SELECT 1 FROM units WHERE id=unit_ngrams.first),(SELECT character FROM units WHERE id=unit_ngrams.first),(SELECT character FROM corpus_units WHERE id=unit_ngrams.first))
    ||iif(EXISTS(SELECT 1 FROM units WHERE id=unit_ngrams.second),(SELECT character FROM units WHERE id=unit_ngrams.second),(SELECT character FROM corpus_units WHERE id=unit_ngrams.second))
    ||iif(third IS NULL,'',iif(EXISTS(SELECT 1 FROM units WHERE id=unit_ngrams.third),(SELECT character FROM units WHERE id=unit_ngrams.third),(SELECT character FROM corpus_units WHERE id=unit_ngrams.third)))
    WHERE first=NEW.id OR second=NEW.id OR third=NEW.id;
END;
CREATE TRIGGER IF NOT EXISTS corpus_ngram_document AFTER UPDATE OF document ON corpus_units WHEN OLD.document IS NOT NEW.document
BEGIN
  UPDATE unit_ngrams SET document=NEW.document WHERE first=NEW.id;
END;
-- A glyph withdrawn or unpublished takes its runs along.
CREATE TRIGGER IF NOT EXISTS corpus_ngram_drop AFTER DELETE ON corpus_units
BEGIN
  DELETE FROM unit_ngrams WHERE first=OLD.id OR second=OLD.id OR third=OLD.id;
END;
-- A run is placed by its first member's style group and shuffle (0058): a crop's, or a corpus glyph's
-- from its `units` row once named and its published row before, which a publication or
-- `publish_styles.py` restyles. A glyph nobody has judged is `unassessed`, the published default.
DROP TRIGGER IF EXISTS unit_ngram_placed;
CREATE TRIGGER IF NOT EXISTS unit_ngram_placed AFTER INSERT ON unit_ngrams
BEGIN
  UPDATE unit_ngrams SET style_order=coalesce((SELECT style_order FROM units WHERE id=NEW.first),(SELECT style_order FROM corpus_units WHERE id=NEW.first),1),
    shuffle=coalesce((SELECT shuffle FROM units WHERE id=NEW.first),(SELECT shuffle FROM corpus_units WHERE id=NEW.first),0)
    WHERE first=NEW.first AND size=NEW.size;
END;
CREATE TRIGGER IF NOT EXISTS corpus_ngram_restyled AFTER UPDATE OF style,shuffle ON corpus_units
  WHEN NOT EXISTS(SELECT 1 FROM units WHERE id=NEW.id)
BEGIN
  UPDATE unit_ngrams SET style_order=NEW.style_order, shuffle=NEW.shuffle WHERE first=NEW.id;
END;
