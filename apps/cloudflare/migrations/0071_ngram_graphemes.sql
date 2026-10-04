-- A run is found by what a reader types: each member's grapheme rather than the form it is written in.
-- `graphemes` is the run's members each folded to its grapheme's head character (`characters.data
-- .grapheme`), the same fold `/atlas/runs` applies to a query; a character the table does not know stands
-- for itself. A grapheme gathers more than hentaigana: ん followed by し finds ん𛁅 and ん𛁈, ねこ finds ネコ,
-- and a kanji run finds its variant shapes; voicing is kept apart. A member's own character is the one
-- its text takes (0061, 0063), so the triggers that keep the text in step fold it with them, and a run is
-- folded as it is inserted or its members change; one with a member the site no longer holds keeps its
-- text, as `/atlas/runs` shows no such run. Explore's counts stay by the written text (`ngram_counts`, 0060).
-- The runs written before this migration are folded by `scripts/backfill_ngram_graphemes.sh`, a slice at a
-- time after `graphemes_backfill.after`, which is also run again after a grapheme publication: moving a
-- character to another grapheme refolds no run itself. Until it has folded every run, the Worker that
-- reads `graphemes` would find none of the older ones, so this migration keeps the indexes led by the
-- text (0058, 0059) beside those led by the graphemes, and the Worker is deployed after the backfill.
ALTER TABLE unit_ngrams ADD COLUMN graphemes TEXT;
CREATE TABLE IF NOT EXISTS graphemes_backfill (one INTEGER PRIMARY KEY CHECK(one = 1), after TEXT NOT NULL);
INSERT OR IGNORE INTO graphemes_backfill VALUES(1,'');
CREATE INDEX IF NOT EXISTS unit_ngram_graphemes ON unit_ngrams(size,graphemes,hand_order,shuffle,first,vertical);
CREATE INDEX IF NOT EXISTS unit_ngram_graphemes_work ON unit_ngrams(document,size,graphemes,hand_order,shuffle,first,vertical);
CREATE INDEX IF NOT EXISTS unit_ngram_graphemes_source ON unit_ngrams(size,graphemes,document,first,hand_order,vertical);
CREATE TRIGGER IF NOT EXISTS unit_ngram_fold AFTER INSERT ON unit_ngrams
BEGIN
  UPDATE unit_ngrams SET graphemes=coalesce((SELECT coalesce(h.character,m.ch) FROM (SELECT coalesce(iif(EXISTS(SELECT 1 FROM units WHERE id=unit_ngrams.first),(SELECT character FROM units WHERE id=unit_ngrams.first),(SELECT character FROM corpus_units WHERE id=unit_ngrams.first)),(SELECT label FROM corpus_units WHERE id=unit_ngrams.first)) AS ch) m LEFT JOIN characters c ON c.character=m.ch LEFT JOIN characters h ON h.code_point=json_extract(c.data,'$.grapheme.code_point'))
    ||(SELECT coalesce(h.character,m.ch) FROM (SELECT coalesce(iif(EXISTS(SELECT 1 FROM units WHERE id=unit_ngrams.second),(SELECT character FROM units WHERE id=unit_ngrams.second),(SELECT character FROM corpus_units WHERE id=unit_ngrams.second)),(SELECT label FROM corpus_units WHERE id=unit_ngrams.second)) AS ch) m LEFT JOIN characters c ON c.character=m.ch LEFT JOIN characters h ON h.code_point=json_extract(c.data,'$.grapheme.code_point'))
    ||iif(third IS NULL,'',(SELECT coalesce(h.character,m.ch) FROM (SELECT coalesce(iif(EXISTS(SELECT 1 FROM units WHERE id=unit_ngrams.third),(SELECT character FROM units WHERE id=unit_ngrams.third),(SELECT character FROM corpus_units WHERE id=unit_ngrams.third)),(SELECT label FROM corpus_units WHERE id=unit_ngrams.third)) AS ch) m LEFT JOIN characters c ON c.character=m.ch LEFT JOIN characters h ON h.code_point=json_extract(c.data,'$.grapheme.code_point'))),text)
    WHERE first=NEW.first AND size=NEW.size;
END;
CREATE TRIGGER IF NOT EXISTS unit_ngram_refold AFTER UPDATE OF first,second,third ON unit_ngrams
BEGIN
  UPDATE unit_ngrams SET graphemes=coalesce((SELECT coalesce(h.character,m.ch) FROM (SELECT coalesce(iif(EXISTS(SELECT 1 FROM units WHERE id=unit_ngrams.first),(SELECT character FROM units WHERE id=unit_ngrams.first),(SELECT character FROM corpus_units WHERE id=unit_ngrams.first)),(SELECT label FROM corpus_units WHERE id=unit_ngrams.first)) AS ch) m LEFT JOIN characters c ON c.character=m.ch LEFT JOIN characters h ON h.code_point=json_extract(c.data,'$.grapheme.code_point'))
    ||(SELECT coalesce(h.character,m.ch) FROM (SELECT coalesce(iif(EXISTS(SELECT 1 FROM units WHERE id=unit_ngrams.second),(SELECT character FROM units WHERE id=unit_ngrams.second),(SELECT character FROM corpus_units WHERE id=unit_ngrams.second)),(SELECT label FROM corpus_units WHERE id=unit_ngrams.second)) AS ch) m LEFT JOIN characters c ON c.character=m.ch LEFT JOIN characters h ON h.code_point=json_extract(c.data,'$.grapheme.code_point'))
    ||iif(third IS NULL,'',(SELECT coalesce(h.character,m.ch) FROM (SELECT coalesce(iif(EXISTS(SELECT 1 FROM units WHERE id=unit_ngrams.third),(SELECT character FROM units WHERE id=unit_ngrams.third),(SELECT character FROM corpus_units WHERE id=unit_ngrams.third)),(SELECT label FROM corpus_units WHERE id=unit_ngrams.third)) AS ch) m LEFT JOIN characters c ON c.character=m.ch LEFT JOIN characters h ON h.code_point=json_extract(c.data,'$.grapheme.code_point'))),text)
    WHERE first=NEW.first AND size=NEW.size;
END;
DROP TRIGGER IF EXISTS unit_ngram_follow;
CREATE TRIGGER unit_ngram_follow AFTER UPDATE OF character,document ON units
BEGIN
  UPDATE unit_ngrams SET text=coalesce(iif(EXISTS(SELECT 1 FROM units WHERE id=unit_ngrams.first),(SELECT character FROM units WHERE id=unit_ngrams.first),(SELECT character FROM corpus_units WHERE id=unit_ngrams.first)),(SELECT label FROM corpus_units WHERE id=unit_ngrams.first))
    ||coalesce(iif(EXISTS(SELECT 1 FROM units WHERE id=unit_ngrams.second),(SELECT character FROM units WHERE id=unit_ngrams.second),(SELECT character FROM corpus_units WHERE id=unit_ngrams.second)),(SELECT label FROM corpus_units WHERE id=unit_ngrams.second))
    ||iif(third IS NULL,'',coalesce(iif(EXISTS(SELECT 1 FROM units WHERE id=unit_ngrams.third),(SELECT character FROM units WHERE id=unit_ngrams.third),(SELECT character FROM corpus_units WHERE id=unit_ngrams.third)),(SELECT label FROM corpus_units WHERE id=unit_ngrams.third))),
    graphemes=(SELECT coalesce(h.character,m.ch) FROM (SELECT coalesce(iif(EXISTS(SELECT 1 FROM units WHERE id=unit_ngrams.first),(SELECT character FROM units WHERE id=unit_ngrams.first),(SELECT character FROM corpus_units WHERE id=unit_ngrams.first)),(SELECT label FROM corpus_units WHERE id=unit_ngrams.first)) AS ch) m LEFT JOIN characters c ON c.character=m.ch LEFT JOIN characters h ON h.code_point=json_extract(c.data,'$.grapheme.code_point'))
    ||(SELECT coalesce(h.character,m.ch) FROM (SELECT coalesce(iif(EXISTS(SELECT 1 FROM units WHERE id=unit_ngrams.second),(SELECT character FROM units WHERE id=unit_ngrams.second),(SELECT character FROM corpus_units WHERE id=unit_ngrams.second)),(SELECT label FROM corpus_units WHERE id=unit_ngrams.second)) AS ch) m LEFT JOIN characters c ON c.character=m.ch LEFT JOIN characters h ON h.code_point=json_extract(c.data,'$.grapheme.code_point'))
    ||iif(third IS NULL,'',(SELECT coalesce(h.character,m.ch) FROM (SELECT coalesce(iif(EXISTS(SELECT 1 FROM units WHERE id=unit_ngrams.third),(SELECT character FROM units WHERE id=unit_ngrams.third),(SELECT character FROM corpus_units WHERE id=unit_ngrams.third)),(SELECT label FROM corpus_units WHERE id=unit_ngrams.third)) AS ch) m LEFT JOIN characters c ON c.character=m.ch LEFT JOIN characters h ON h.code_point=json_extract(c.data,'$.grapheme.code_point')))
    WHERE first=NEW.id OR second=NEW.id OR third=NEW.id;
  UPDATE unit_ngrams SET document=NEW.document WHERE first=NEW.id AND NEW.origin<>'corpus';
END;
DROP TRIGGER IF EXISTS unit_ngram_named;
CREATE TRIGGER unit_ngram_named AFTER INSERT ON units WHEN NEW.origin='corpus'
BEGIN
  UPDATE unit_ngrams SET text=coalesce(iif(EXISTS(SELECT 1 FROM units WHERE id=unit_ngrams.first),(SELECT character FROM units WHERE id=unit_ngrams.first),(SELECT character FROM corpus_units WHERE id=unit_ngrams.first)),(SELECT label FROM corpus_units WHERE id=unit_ngrams.first))
    ||coalesce(iif(EXISTS(SELECT 1 FROM units WHERE id=unit_ngrams.second),(SELECT character FROM units WHERE id=unit_ngrams.second),(SELECT character FROM corpus_units WHERE id=unit_ngrams.second)),(SELECT label FROM corpus_units WHERE id=unit_ngrams.second))
    ||iif(third IS NULL,'',coalesce(iif(EXISTS(SELECT 1 FROM units WHERE id=unit_ngrams.third),(SELECT character FROM units WHERE id=unit_ngrams.third),(SELECT character FROM corpus_units WHERE id=unit_ngrams.third)),(SELECT label FROM corpus_units WHERE id=unit_ngrams.third))),
    graphemes=(SELECT coalesce(h.character,m.ch) FROM (SELECT coalesce(iif(EXISTS(SELECT 1 FROM units WHERE id=unit_ngrams.first),(SELECT character FROM units WHERE id=unit_ngrams.first),(SELECT character FROM corpus_units WHERE id=unit_ngrams.first)),(SELECT label FROM corpus_units WHERE id=unit_ngrams.first)) AS ch) m LEFT JOIN characters c ON c.character=m.ch LEFT JOIN characters h ON h.code_point=json_extract(c.data,'$.grapheme.code_point'))
    ||(SELECT coalesce(h.character,m.ch) FROM (SELECT coalesce(iif(EXISTS(SELECT 1 FROM units WHERE id=unit_ngrams.second),(SELECT character FROM units WHERE id=unit_ngrams.second),(SELECT character FROM corpus_units WHERE id=unit_ngrams.second)),(SELECT label FROM corpus_units WHERE id=unit_ngrams.second)) AS ch) m LEFT JOIN characters c ON c.character=m.ch LEFT JOIN characters h ON h.code_point=json_extract(c.data,'$.grapheme.code_point'))
    ||iif(third IS NULL,'',(SELECT coalesce(h.character,m.ch) FROM (SELECT coalesce(iif(EXISTS(SELECT 1 FROM units WHERE id=unit_ngrams.third),(SELECT character FROM units WHERE id=unit_ngrams.third),(SELECT character FROM corpus_units WHERE id=unit_ngrams.third)),(SELECT label FROM corpus_units WHERE id=unit_ngrams.third)) AS ch) m LEFT JOIN characters c ON c.character=m.ch LEFT JOIN characters h ON h.code_point=json_extract(c.data,'$.grapheme.code_point')))
    WHERE first=NEW.id OR second=NEW.id OR third=NEW.id;
END;
DROP TRIGGER IF EXISTS unit_ngram_unnamed;
CREATE TRIGGER unit_ngram_unnamed AFTER DELETE ON units WHEN OLD.origin='corpus'
BEGIN
  UPDATE unit_ngrams SET text=coalesce(iif(EXISTS(SELECT 1 FROM units WHERE id=unit_ngrams.first),(SELECT character FROM units WHERE id=unit_ngrams.first),(SELECT character FROM corpus_units WHERE id=unit_ngrams.first)),(SELECT label FROM corpus_units WHERE id=unit_ngrams.first))
    ||coalesce(iif(EXISTS(SELECT 1 FROM units WHERE id=unit_ngrams.second),(SELECT character FROM units WHERE id=unit_ngrams.second),(SELECT character FROM corpus_units WHERE id=unit_ngrams.second)),(SELECT label FROM corpus_units WHERE id=unit_ngrams.second))
    ||iif(third IS NULL,'',coalesce(iif(EXISTS(SELECT 1 FROM units WHERE id=unit_ngrams.third),(SELECT character FROM units WHERE id=unit_ngrams.third),(SELECT character FROM corpus_units WHERE id=unit_ngrams.third)),(SELECT label FROM corpus_units WHERE id=unit_ngrams.third))),
    graphemes=(SELECT coalesce(h.character,m.ch) FROM (SELECT coalesce(iif(EXISTS(SELECT 1 FROM units WHERE id=unit_ngrams.first),(SELECT character FROM units WHERE id=unit_ngrams.first),(SELECT character FROM corpus_units WHERE id=unit_ngrams.first)),(SELECT label FROM corpus_units WHERE id=unit_ngrams.first)) AS ch) m LEFT JOIN characters c ON c.character=m.ch LEFT JOIN characters h ON h.code_point=json_extract(c.data,'$.grapheme.code_point'))
    ||(SELECT coalesce(h.character,m.ch) FROM (SELECT coalesce(iif(EXISTS(SELECT 1 FROM units WHERE id=unit_ngrams.second),(SELECT character FROM units WHERE id=unit_ngrams.second),(SELECT character FROM corpus_units WHERE id=unit_ngrams.second)),(SELECT label FROM corpus_units WHERE id=unit_ngrams.second)) AS ch) m LEFT JOIN characters c ON c.character=m.ch LEFT JOIN characters h ON h.code_point=json_extract(c.data,'$.grapheme.code_point'))
    ||iif(third IS NULL,'',(SELECT coalesce(h.character,m.ch) FROM (SELECT coalesce(iif(EXISTS(SELECT 1 FROM units WHERE id=unit_ngrams.third),(SELECT character FROM units WHERE id=unit_ngrams.third),(SELECT character FROM corpus_units WHERE id=unit_ngrams.third)),(SELECT label FROM corpus_units WHERE id=unit_ngrams.third)) AS ch) m LEFT JOIN characters c ON c.character=m.ch LEFT JOIN characters h ON h.code_point=json_extract(c.data,'$.grapheme.code_point')))
    WHERE first=OLD.id OR second=OLD.id OR third=OLD.id;
END;
DROP TRIGGER IF EXISTS corpus_ngram_follow;
CREATE TRIGGER corpus_ngram_follow AFTER UPDATE OF character,label ON corpus_units
  WHEN OLD.character IS NOT NEW.character OR OLD.label IS NOT NEW.label
BEGIN
  UPDATE unit_ngrams SET text=coalesce(iif(EXISTS(SELECT 1 FROM units WHERE id=unit_ngrams.first),(SELECT character FROM units WHERE id=unit_ngrams.first),(SELECT character FROM corpus_units WHERE id=unit_ngrams.first)),(SELECT label FROM corpus_units WHERE id=unit_ngrams.first))
    ||coalesce(iif(EXISTS(SELECT 1 FROM units WHERE id=unit_ngrams.second),(SELECT character FROM units WHERE id=unit_ngrams.second),(SELECT character FROM corpus_units WHERE id=unit_ngrams.second)),(SELECT label FROM corpus_units WHERE id=unit_ngrams.second))
    ||iif(third IS NULL,'',coalesce(iif(EXISTS(SELECT 1 FROM units WHERE id=unit_ngrams.third),(SELECT character FROM units WHERE id=unit_ngrams.third),(SELECT character FROM corpus_units WHERE id=unit_ngrams.third)),(SELECT label FROM corpus_units WHERE id=unit_ngrams.third))),
    graphemes=(SELECT coalesce(h.character,m.ch) FROM (SELECT coalesce(iif(EXISTS(SELECT 1 FROM units WHERE id=unit_ngrams.first),(SELECT character FROM units WHERE id=unit_ngrams.first),(SELECT character FROM corpus_units WHERE id=unit_ngrams.first)),(SELECT label FROM corpus_units WHERE id=unit_ngrams.first)) AS ch) m LEFT JOIN characters c ON c.character=m.ch LEFT JOIN characters h ON h.code_point=json_extract(c.data,'$.grapheme.code_point'))
    ||(SELECT coalesce(h.character,m.ch) FROM (SELECT coalesce(iif(EXISTS(SELECT 1 FROM units WHERE id=unit_ngrams.second),(SELECT character FROM units WHERE id=unit_ngrams.second),(SELECT character FROM corpus_units WHERE id=unit_ngrams.second)),(SELECT label FROM corpus_units WHERE id=unit_ngrams.second)) AS ch) m LEFT JOIN characters c ON c.character=m.ch LEFT JOIN characters h ON h.code_point=json_extract(c.data,'$.grapheme.code_point'))
    ||iif(third IS NULL,'',(SELECT coalesce(h.character,m.ch) FROM (SELECT coalesce(iif(EXISTS(SELECT 1 FROM units WHERE id=unit_ngrams.third),(SELECT character FROM units WHERE id=unit_ngrams.third),(SELECT character FROM corpus_units WHERE id=unit_ngrams.third)),(SELECT label FROM corpus_units WHERE id=unit_ngrams.third)) AS ch) m LEFT JOIN characters c ON c.character=m.ch LEFT JOIN characters h ON h.code_point=json_extract(c.data,'$.grapheme.code_point')))
    WHERE first=NEW.id OR second=NEW.id OR third=NEW.id;
END;
