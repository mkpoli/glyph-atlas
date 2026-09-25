-- A production is a node of the tree in `data/vocab/production.yaml`, written as its path. The old
-- values map onto the node each one's sources support: `manuscript` is `handwritten`, `movable-type`
-- is `printed/type`, and `woodblock`, which came from sources that mostly say only 刊 or 版, is
-- `printed` until a publication states more. A row's column, its data and its snapshot are each
-- mapped from their own value.
--
-- Published corpus records in R2 keep the old values, and a round checks each record's production
-- against the scope before dealing it, so the corpus is republished in the same release.
UPDATE units SET production=CASE production
  WHEN 'manuscript' THEN 'handwritten' WHEN 'woodblock' THEN 'printed' ELSE 'printed/type' END
 WHERE production IN ('manuscript','woodblock','movable-type');
UPDATE units SET data=json_set(data,
  '$.production',CASE json_extract(data,'$.production')
   WHEN 'manuscript' THEN 'handwritten' WHEN 'woodblock' THEN 'printed' ELSE 'printed/type' END,
  '$.production_label',CASE json_extract(data,'$.production')
   WHEN 'manuscript' THEN 'Handwritten' WHEN 'woodblock' THEN 'Printed' ELSE 'Movable type' END)
 WHERE json_extract(data,'$.production') IN ('manuscript','woodblock','movable-type');
UPDATE units SET snapshot=json_set(snapshot,
  '$.character.production',CASE json_extract(snapshot,'$.character.production')
   WHEN 'manuscript' THEN 'handwritten' WHEN 'woodblock' THEN 'printed' ELSE 'printed/type' END,
  '$.character.production_label',CASE json_extract(snapshot,'$.character.production')
   WHEN 'manuscript' THEN 'Handwritten' WHEN 'woodblock' THEN 'Printed' ELSE 'Movable type' END)
 WHERE json_extract(snapshot,'$.character.production') IN ('manuscript','woodblock','movable-type');
UPDATE corpus_units SET production=CASE production
  WHEN 'manuscript' THEN 'handwritten' WHEN 'woodblock' THEN 'printed' ELSE 'printed/type' END
 WHERE production IN ('manuscript','woodblock','movable-type');
UPDATE corpus_characters SET production=CASE production
  WHEN 'manuscript' THEN 'handwritten' WHEN 'woodblock' THEN 'printed' ELSE 'printed/type' END
 WHERE production IN ('manuscript','woodblock','movable-type');
