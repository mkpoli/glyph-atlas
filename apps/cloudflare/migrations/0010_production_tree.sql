-- A production is a node of the tree in `data/vocab/production.yaml`, written as its path. The old
-- values map onto the node each one's sources support: `manuscript` is `handwritten`, `movable-type`
-- is `printed/type`, and `woodblock`, which came from sources that mostly say only 刊 or 版, is
-- `printed` until a publication states more. A crop's record keeps its value and label in its data.
UPDATE units SET production=CASE production
  WHEN 'manuscript' THEN 'handwritten' WHEN 'woodblock' THEN 'printed' ELSE 'printed/type' END
 WHERE production IN ('manuscript','woodblock','movable-type');
UPDATE units SET
 data=json_set(data,'$.production',production,'$.production_label',CASE production
  WHEN 'handwritten' THEN 'Handwritten' WHEN 'printed' THEN 'Printed' ELSE 'Movable type' END)
 WHERE production IN ('handwritten','printed','printed/type')
  AND json_extract(data,'$.production') IN ('manuscript','woodblock','movable-type');
UPDATE units SET
 snapshot=json_set(snapshot,'$.character.production',production,'$.character.production_label',CASE production
  WHEN 'handwritten' THEN 'Handwritten' WHEN 'printed' THEN 'Printed' ELSE 'Movable type' END)
 WHERE production IN ('handwritten','printed','printed/type')
  AND json_extract(snapshot,'$.character.production') IN ('manuscript','woodblock','movable-type');
UPDATE corpus_units SET production=CASE production
  WHEN 'manuscript' THEN 'handwritten' WHEN 'woodblock' THEN 'printed' ELSE 'printed/type' END
 WHERE production IN ('manuscript','woodblock','movable-type');
UPDATE corpus_characters SET production=CASE production
  WHEN 'manuscript' THEN 'handwritten' WHEN 'woodblock' THEN 'printed' ELSE 'printed/type' END
 WHERE production IN ('manuscript','woodblock','movable-type');
