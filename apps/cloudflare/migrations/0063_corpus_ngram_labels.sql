-- The label a corpus glyph's record is shown with while no written form is settled: its encoded class,
-- else its source transcription (`corpus.details.unit_label`), a CODH kana labelled ん without saying
-- which form of it is written. The corpus n-gram export writes it for the glyphs it places. Such a
-- glyph has no character on either row (`units`, `corpus_units`), so its runs (0061) had no text and
-- could be neither searched nor counted; a member's text now falls back to this label, as the export
-- writes it (`glyph_atlas.ngrams._written`). These four triggers of 0061 are redefined with that
-- fallback, and the last also follows the label; a glyph given a character takes it over the label.
ALTER TABLE corpus_units ADD COLUMN label TEXT;
DROP TRIGGER IF EXISTS unit_ngram_follow;
CREATE TRIGGER unit_ngram_follow AFTER UPDATE OF character,document ON units
BEGIN
  UPDATE unit_ngrams SET text=coalesce(iif(EXISTS(SELECT 1 FROM units WHERE id=unit_ngrams.first),(SELECT character FROM units WHERE id=unit_ngrams.first),(SELECT character FROM corpus_units WHERE id=unit_ngrams.first)),(SELECT label FROM corpus_units WHERE id=unit_ngrams.first))
    ||coalesce(iif(EXISTS(SELECT 1 FROM units WHERE id=unit_ngrams.second),(SELECT character FROM units WHERE id=unit_ngrams.second),(SELECT character FROM corpus_units WHERE id=unit_ngrams.second)),(SELECT label FROM corpus_units WHERE id=unit_ngrams.second))
    ||iif(third IS NULL,'',coalesce(iif(EXISTS(SELECT 1 FROM units WHERE id=unit_ngrams.third),(SELECT character FROM units WHERE id=unit_ngrams.third),(SELECT character FROM corpus_units WHERE id=unit_ngrams.third)),(SELECT label FROM corpus_units WHERE id=unit_ngrams.third)))
    WHERE first=NEW.id OR second=NEW.id OR third=NEW.id;
  UPDATE unit_ngrams SET document=NEW.document WHERE first=NEW.id AND NEW.origin<>'corpus';
END;
DROP TRIGGER IF EXISTS unit_ngram_named;
CREATE TRIGGER unit_ngram_named AFTER INSERT ON units WHEN NEW.origin='corpus'
BEGIN
  UPDATE unit_ngrams SET text=coalesce(iif(EXISTS(SELECT 1 FROM units WHERE id=unit_ngrams.first),(SELECT character FROM units WHERE id=unit_ngrams.first),(SELECT character FROM corpus_units WHERE id=unit_ngrams.first)),(SELECT label FROM corpus_units WHERE id=unit_ngrams.first))
    ||coalesce(iif(EXISTS(SELECT 1 FROM units WHERE id=unit_ngrams.second),(SELECT character FROM units WHERE id=unit_ngrams.second),(SELECT character FROM corpus_units WHERE id=unit_ngrams.second)),(SELECT label FROM corpus_units WHERE id=unit_ngrams.second))
    ||iif(third IS NULL,'',coalesce(iif(EXISTS(SELECT 1 FROM units WHERE id=unit_ngrams.third),(SELECT character FROM units WHERE id=unit_ngrams.third),(SELECT character FROM corpus_units WHERE id=unit_ngrams.third)),(SELECT label FROM corpus_units WHERE id=unit_ngrams.third)))
    WHERE first=NEW.id OR second=NEW.id OR third=NEW.id;
END;
DROP TRIGGER IF EXISTS unit_ngram_unnamed;
CREATE TRIGGER unit_ngram_unnamed AFTER DELETE ON units WHEN OLD.origin='corpus'
BEGIN
  UPDATE unit_ngrams SET text=coalesce(iif(EXISTS(SELECT 1 FROM units WHERE id=unit_ngrams.first),(SELECT character FROM units WHERE id=unit_ngrams.first),(SELECT character FROM corpus_units WHERE id=unit_ngrams.first)),(SELECT label FROM corpus_units WHERE id=unit_ngrams.first))
    ||coalesce(iif(EXISTS(SELECT 1 FROM units WHERE id=unit_ngrams.second),(SELECT character FROM units WHERE id=unit_ngrams.second),(SELECT character FROM corpus_units WHERE id=unit_ngrams.second)),(SELECT label FROM corpus_units WHERE id=unit_ngrams.second))
    ||iif(third IS NULL,'',coalesce(iif(EXISTS(SELECT 1 FROM units WHERE id=unit_ngrams.third),(SELECT character FROM units WHERE id=unit_ngrams.third),(SELECT character FROM corpus_units WHERE id=unit_ngrams.third)),(SELECT label FROM corpus_units WHERE id=unit_ngrams.third)))
    WHERE first=OLD.id OR second=OLD.id OR third=OLD.id;
END;
DROP TRIGGER IF EXISTS corpus_ngram_follow;
CREATE TRIGGER corpus_ngram_follow AFTER UPDATE OF character,label ON corpus_units
  WHEN OLD.character IS NOT NEW.character OR OLD.label IS NOT NEW.label
BEGIN
  UPDATE unit_ngrams SET text=coalesce(iif(EXISTS(SELECT 1 FROM units WHERE id=unit_ngrams.first),(SELECT character FROM units WHERE id=unit_ngrams.first),(SELECT character FROM corpus_units WHERE id=unit_ngrams.first)),(SELECT label FROM corpus_units WHERE id=unit_ngrams.first))
    ||coalesce(iif(EXISTS(SELECT 1 FROM units WHERE id=unit_ngrams.second),(SELECT character FROM units WHERE id=unit_ngrams.second),(SELECT character FROM corpus_units WHERE id=unit_ngrams.second)),(SELECT label FROM corpus_units WHERE id=unit_ngrams.second))
    ||iif(third IS NULL,'',coalesce(iif(EXISTS(SELECT 1 FROM units WHERE id=unit_ngrams.third),(SELECT character FROM units WHERE id=unit_ngrams.third),(SELECT character FROM corpus_units WHERE id=unit_ngrams.third)),(SELECT label FROM corpus_units WHERE id=unit_ngrams.third)))
    WHERE first=NEW.id OR second=NEW.id OR third=NEW.id;
END;
