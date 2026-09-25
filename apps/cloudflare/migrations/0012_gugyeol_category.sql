-- 구결자 labels have a category of their own, `gugyeol`: the script of the label's first character,
-- as the Worker's `categoryOf` and the publication scripts compute it. Rows written before it
-- existed read `other`. Gugyeol has no Unicode script property, so the range is the Hanyang
-- private-use convention `data/vocab/characters.tsv` holds the 구결자 at.
UPDATE units SET category=k.category,
 data=iif(json_type(units.data,'$.category') IS NULL,units.data,json_set(units.data,'$.category',k.category))
 FROM (SELECT id,CASE
  WHEN c BETWEEN 0xF67E AND 0xF77C THEN 'gugyeol'
  ELSE 'other' END AS category
 FROM (SELECT id,unicode(json_extract(data,'$.label')) AS c FROM units WHERE category='other')) k
 WHERE units.id=k.id AND k.category='gugyeol';
