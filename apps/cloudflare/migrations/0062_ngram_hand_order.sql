-- A run's page lists its occurrences by how their letterforms were made (`hand_order`), since the
-- style the order read (0058) is `unassessed` on nearly every crop and so ordered nothing. A crop's
-- production (`data/vocab/production.yaml`) places it, and a judged style where its production says
-- nothing:
--   0  shaped by hand: `handwritten`, `inscribed` and below, and the prints whose letters were cut or
--      drawn for the page: `printed` (a print whose method nobody records, in this corpus a woodblock
--      book), `printed/woodblock`, `printed/engraved`, `printed/lithograph`, `printed/stencil`; or,
--      with any other production, a running or cursive style;
--   1  not known: `unknown`, `mixed`, and anything else;
--   2  set from type: `printed/type` and below, `printed/phototype`, `printed/digital`, `typewritten`.
-- Type wins over a style, so movable type cut in a running hand still comes last. A virtual column on
-- each table costs no storage, and adding one rewrites no row.
ALTER TABLE units ADD COLUMN hand_order INTEGER GENERATED ALWAYS AS (CASE
  WHEN production='printed/type' OR production LIKE 'printed/type/%' OR production IN ('printed/phototype','printed/digital','typewritten') THEN 2
  WHEN production IN ('handwritten','inscribed','printed','printed/woodblock','printed/engraved','printed/lithograph','printed/stencil')
    OR production LIKE 'inscribed/%' OR style IN ('cursive','running') THEN 0
  ELSE 1 END) VIRTUAL;
ALTER TABLE corpus_units ADD COLUMN hand_order INTEGER GENERATED ALWAYS AS (CASE
  WHEN production='printed/type' OR production LIKE 'printed/type/%' OR production IN ('printed/phototype','printed/digital','typewritten') THEN 2
  WHEN production IN ('handwritten','inscribed','printed','printed/woodblock','printed/engraved','printed/lithograph','printed/stencil')
    OR production LIKE 'inscribed/%' OR style IN ('cursive','running') THEN 0
  ELSE 1 END) VIRTUAL;
-- The run carries its first member's group under the new name, in the indexes that hold it (0058, 0059).
DROP TRIGGER IF EXISTS unit_ngram_placed;
DROP TRIGGER IF EXISTS unit_ngram_restyled;
DROP TRIGGER IF EXISTS corpus_ngram_restyled;
ALTER TABLE unit_ngrams RENAME COLUMN style_order TO hand_order;
-- The runs already written keep the style group they had until `scripts/backfill_hand_order.sh` places
-- them, a slice of the table at a time: with the corpus runs there are millions, more than one statement
-- of a migration may rewrite. Its cursor lives here.
CREATE TABLE IF NOT EXISTS hand_order_backfill (one INTEGER PRIMARY KEY CHECK(one = 1), after TEXT NOT NULL);
INSERT OR IGNORE INTO hand_order_backfill VALUES(1,'');
-- A run takes its first member's group and shuffle when it is written: a crop's, or a corpus glyph's
-- from its `units` row once named and its published row before. A publication that rewrites a crop's
-- production in place (`refresh_published_units.py`), a restyle, or a decision that moves a glyph's
-- published row moves its runs.
CREATE TRIGGER IF NOT EXISTS unit_ngram_placed AFTER INSERT ON unit_ngrams
BEGIN
  UPDATE unit_ngrams SET hand_order=coalesce((SELECT hand_order FROM units WHERE id=NEW.first),(SELECT hand_order FROM corpus_units WHERE id=NEW.first),1),
    shuffle=coalesce((SELECT shuffle FROM units WHERE id=NEW.first),(SELECT shuffle FROM corpus_units WHERE id=NEW.first),0)
    WHERE first=NEW.first AND size=NEW.size;
END;
CREATE TRIGGER IF NOT EXISTS unit_ngram_restyled AFTER UPDATE OF production,style,shuffle ON units
BEGIN
  UPDATE unit_ngrams SET hand_order=NEW.hand_order, shuffle=NEW.shuffle WHERE first=NEW.id;
END;
CREATE TRIGGER IF NOT EXISTS corpus_ngram_restyled AFTER UPDATE OF production,style,shuffle ON corpus_units
  WHEN NOT EXISTS(SELECT 1 FROM units WHERE id=NEW.id)
BEGIN
  UPDATE unit_ngrams SET hand_order=NEW.hand_order, shuffle=NEW.shuffle WHERE first=NEW.id;
END;
