-- Hangul labels have a category of their own, `hangul`: the script of the label's first character,
-- as the Worker's `categoryOf` and the publication scripts compute it. Rows written before it
-- existed read `other`. The ranges are the code points of Unicode's Script=Hangul.
UPDATE units SET category=k.category,
 data=iif(json_type(units.data,'$.category') IS NULL,units.data,json_set(units.data,'$.category',k.category))
 FROM (SELECT id,CASE
  WHEN c BETWEEN 0x1100 AND 0x11FF OR c BETWEEN 0x302E AND 0x302F OR c BETWEEN 0x3131 AND 0x318E
   OR c BETWEEN 0x3200 AND 0x321E OR c BETWEEN 0x3260 AND 0x327E OR c BETWEEN 0xA960 AND 0xA97C
   OR c BETWEEN 0xAC00 AND 0xD7A3 OR c BETWEEN 0xD7B0 AND 0xD7C6 OR c BETWEEN 0xD7CB AND 0xD7FB
   OR c BETWEEN 0xFFA0 AND 0xFFBE OR c BETWEEN 0xFFC2 AND 0xFFC7 OR c BETWEEN 0xFFCA AND 0xFFCF
   OR c BETWEEN 0xFFD2 AND 0xFFD7 OR c BETWEEN 0xFFDA AND 0xFFDC THEN 'hangul'
  ELSE 'other' END AS category
 FROM (SELECT id,unicode(json_extract(data,'$.label')) AS c FROM units WHERE category='other')) k
 WHERE units.id=k.id AND k.category='hangul';
