-- How many crops each character has in each book, material, round eligibility and stored state. Browse
-- and rounds count from here: counting `units` itself read every crop and its JSON, took 3–40 seconds on
-- the live database, and held up every other query, the images' included, while it ran. The triggers
-- keep it current whoever writes a crop: a publication, a refresh, a review or an undo.
-- A key column that is NULL in `units` is '' here, so each group is one primary key.
CREATE TABLE IF NOT EXISTS unit_counts (
 origin TEXT NOT NULL, character TEXT NOT NULL, document TEXT NOT NULL, state TEXT NOT NULL,
 family TEXT NOT NULL, production TEXT NOT NULL, quiz INTEGER NOT NULL, title TEXT NOT NULL, n INTEGER NOT NULL,
 PRIMARY KEY (origin,character,document,state,family,production,quiz,title)
) WITHOUT ROWID;
CREATE TRIGGER IF NOT EXISTS unit_count_insert AFTER INSERT ON units
BEGIN
 INSERT INTO unit_counts VALUES(NEW.origin,coalesce(NEW.character,''),coalesce(NEW.document,''),NEW.state,
   coalesce(NEW.family,''),NEW.production,NEW.quiz,coalesce(json_extract(NEW.data,'$.source'),''),1)
   ON CONFLICT DO UPDATE SET n=n+1;
END;
CREATE TRIGGER IF NOT EXISTS unit_count_delete AFTER DELETE ON units
BEGIN
 UPDATE unit_counts SET n=n-1 WHERE origin=OLD.origin AND character=coalesce(OLD.character,'')
   AND document=coalesce(OLD.document,'') AND state=OLD.state AND family=coalesce(OLD.family,'')
   AND production=OLD.production AND quiz=OLD.quiz AND title=coalesce(json_extract(OLD.data,'$.source'),'');
 DELETE FROM unit_counts WHERE origin=OLD.origin AND character=coalesce(OLD.character,'')
   AND document=coalesce(OLD.document,'') AND state=OLD.state AND family=coalesce(OLD.family,'')
   AND production=OLD.production AND quiz=OLD.quiz AND title=coalesce(json_extract(OLD.data,'$.source'),'') AND n<=0;
END;
-- A review rewrites `data` on every save; the counts move only when a key does.
CREATE TRIGGER IF NOT EXISTS unit_count_update AFTER UPDATE OF origin,character,document,state,family,production,quiz,data ON units
 WHEN OLD.origin IS NOT NEW.origin OR OLD.character IS NOT NEW.character OR OLD.document IS NOT NEW.document
   OR OLD.state IS NOT NEW.state OR OLD.family IS NOT NEW.family OR OLD.production IS NOT NEW.production
   OR OLD.quiz IS NOT NEW.quiz OR json_extract(OLD.data,'$.source') IS NOT json_extract(NEW.data,'$.source')
BEGIN
 UPDATE unit_counts SET n=n-1 WHERE origin=OLD.origin AND character=coalesce(OLD.character,'')
   AND document=coalesce(OLD.document,'') AND state=OLD.state AND family=coalesce(OLD.family,'')
   AND production=OLD.production AND quiz=OLD.quiz AND title=coalesce(json_extract(OLD.data,'$.source'),'');
 DELETE FROM unit_counts WHERE origin=OLD.origin AND character=coalesce(OLD.character,'')
   AND document=coalesce(OLD.document,'') AND state=OLD.state AND family=coalesce(OLD.family,'')
   AND production=OLD.production AND quiz=OLD.quiz AND title=coalesce(json_extract(OLD.data,'$.source'),'') AND n<=0;
 INSERT INTO unit_counts VALUES(NEW.origin,coalesce(NEW.character,''),coalesce(NEW.document,''),NEW.state,
   coalesce(NEW.family,''),NEW.production,NEW.quiz,coalesce(json_extract(NEW.data,'$.source'),''),1)
   ON CONFLICT DO UPDATE SET n=n+1;
END;
-- The crops already here, in slices of the rowid range, as 0024 does, so each statement stays small. A
-- slice past the last row matches nothing, so the slices reach further than the table does today.
INSERT INTO unit_counts SELECT origin,coalesce(character,''),coalesce(document,''),state,coalesce(family,''),production,quiz,
  coalesce(json_extract(data,'$.source'),''),count(*) FROM units WHERE rowid>=0 AND rowid<20000 GROUP BY 1,2,3,4,5,6,7,8 ON CONFLICT DO UPDATE SET n=n+excluded.n;
INSERT INTO unit_counts SELECT origin,coalesce(character,''),coalesce(document,''),state,coalesce(family,''),production,quiz,
  coalesce(json_extract(data,'$.source'),''),count(*) FROM units WHERE rowid>=20000 AND rowid<40000 GROUP BY 1,2,3,4,5,6,7,8 ON CONFLICT DO UPDATE SET n=n+excluded.n;
INSERT INTO unit_counts SELECT origin,coalesce(character,''),coalesce(document,''),state,coalesce(family,''),production,quiz,
  coalesce(json_extract(data,'$.source'),''),count(*) FROM units WHERE rowid>=40000 AND rowid<60000 GROUP BY 1,2,3,4,5,6,7,8 ON CONFLICT DO UPDATE SET n=n+excluded.n;
INSERT INTO unit_counts SELECT origin,coalesce(character,''),coalesce(document,''),state,coalesce(family,''),production,quiz,
  coalesce(json_extract(data,'$.source'),''),count(*) FROM units WHERE rowid>=60000 AND rowid<80000 GROUP BY 1,2,3,4,5,6,7,8 ON CONFLICT DO UPDATE SET n=n+excluded.n;
INSERT INTO unit_counts SELECT origin,coalesce(character,''),coalesce(document,''),state,coalesce(family,''),production,quiz,
  coalesce(json_extract(data,'$.source'),''),count(*) FROM units WHERE rowid>=80000 AND rowid<100000 GROUP BY 1,2,3,4,5,6,7,8 ON CONFLICT DO UPDATE SET n=n+excluded.n;
INSERT INTO unit_counts SELECT origin,coalesce(character,''),coalesce(document,''),state,coalesce(family,''),production,quiz,
  coalesce(json_extract(data,'$.source'),''),count(*) FROM units WHERE rowid>=100000 AND rowid<120000 GROUP BY 1,2,3,4,5,6,7,8 ON CONFLICT DO UPDATE SET n=n+excluded.n;
INSERT INTO unit_counts SELECT origin,coalesce(character,''),coalesce(document,''),state,coalesce(family,''),production,quiz,
  coalesce(json_extract(data,'$.source'),''),count(*) FROM units WHERE rowid>=120000 AND rowid<140000 GROUP BY 1,2,3,4,5,6,7,8 ON CONFLICT DO UPDATE SET n=n+excluded.n;
INSERT INTO unit_counts SELECT origin,coalesce(character,''),coalesce(document,''),state,coalesce(family,''),production,quiz,
  coalesce(json_extract(data,'$.source'),''),count(*) FROM units WHERE rowid>=140000 AND rowid<160000 GROUP BY 1,2,3,4,5,6,7,8 ON CONFLICT DO UPDATE SET n=n+excluded.n;
INSERT INTO unit_counts SELECT origin,coalesce(character,''),coalesce(document,''),state,coalesce(family,''),production,quiz,
  coalesce(json_extract(data,'$.source'),''),count(*) FROM units WHERE rowid>=160000 AND rowid<180000 GROUP BY 1,2,3,4,5,6,7,8 ON CONFLICT DO UPDATE SET n=n+excluded.n;
INSERT INTO unit_counts SELECT origin,coalesce(character,''),coalesce(document,''),state,coalesce(family,''),production,quiz,
  coalesce(json_extract(data,'$.source'),''),count(*) FROM units WHERE rowid>=180000 AND rowid<200000 GROUP BY 1,2,3,4,5,6,7,8 ON CONFLICT DO UPDATE SET n=n+excluded.n;
INSERT INTO unit_counts SELECT origin,coalesce(character,''),coalesce(document,''),state,coalesce(family,''),production,quiz,
  coalesce(json_extract(data,'$.source'),''),count(*) FROM units WHERE rowid>=200000 AND rowid<220000 GROUP BY 1,2,3,4,5,6,7,8 ON CONFLICT DO UPDATE SET n=n+excluded.n;
INSERT INTO unit_counts SELECT origin,coalesce(character,''),coalesce(document,''),state,coalesce(family,''),production,quiz,
  coalesce(json_extract(data,'$.source'),''),count(*) FROM units WHERE rowid>=220000 AND rowid<240000 GROUP BY 1,2,3,4,5,6,7,8 ON CONFLICT DO UPDATE SET n=n+excluded.n;
INSERT INTO unit_counts SELECT origin,coalesce(character,''),coalesce(document,''),state,coalesce(family,''),production,quiz,
  coalesce(json_extract(data,'$.source'),''),count(*) FROM units WHERE rowid>=240000 AND rowid<260000 GROUP BY 1,2,3,4,5,6,7,8 ON CONFLICT DO UPDATE SET n=n+excluded.n;
INSERT INTO unit_counts SELECT origin,coalesce(character,''),coalesce(document,''),state,coalesce(family,''),production,quiz,
  coalesce(json_extract(data,'$.source'),''),count(*) FROM units WHERE rowid>=260000 AND rowid<280000 GROUP BY 1,2,3,4,5,6,7,8 ON CONFLICT DO UPDATE SET n=n+excluded.n;
INSERT INTO unit_counts SELECT origin,coalesce(character,''),coalesce(document,''),state,coalesce(family,''),production,quiz,
  coalesce(json_extract(data,'$.source'),''),count(*) FROM units WHERE rowid>=280000 AND rowid<300000 GROUP BY 1,2,3,4,5,6,7,8 ON CONFLICT DO UPDATE SET n=n+excluded.n;
INSERT INTO unit_counts SELECT origin,coalesce(character,''),coalesce(document,''),state,coalesce(family,''),production,quiz,
  coalesce(json_extract(data,'$.source'),''),count(*) FROM units WHERE rowid>=300000 AND rowid<320000 GROUP BY 1,2,3,4,5,6,7,8 ON CONFLICT DO UPDATE SET n=n+excluded.n;
INSERT INTO unit_counts SELECT origin,coalesce(character,''),coalesce(document,''),state,coalesce(family,''),production,quiz,
  coalesce(json_extract(data,'$.source'),''),count(*) FROM units WHERE rowid>=320000 AND rowid<340000 GROUP BY 1,2,3,4,5,6,7,8 ON CONFLICT DO UPDATE SET n=n+excluded.n;
INSERT INTO unit_counts SELECT origin,coalesce(character,''),coalesce(document,''),state,coalesce(family,''),production,quiz,
  coalesce(json_extract(data,'$.source'),''),count(*) FROM units WHERE rowid>=340000 AND rowid<360000 GROUP BY 1,2,3,4,5,6,7,8 ON CONFLICT DO UPDATE SET n=n+excluded.n;
INSERT INTO unit_counts SELECT origin,coalesce(character,''),coalesce(document,''),state,coalesce(family,''),production,quiz,
  coalesce(json_extract(data,'$.source'),''),count(*) FROM units WHERE rowid>=360000 AND rowid<380000 GROUP BY 1,2,3,4,5,6,7,8 ON CONFLICT DO UPDATE SET n=n+excluded.n;
INSERT INTO unit_counts SELECT origin,coalesce(character,''),coalesce(document,''),state,coalesce(family,''),production,quiz,
  coalesce(json_extract(data,'$.source'),''),count(*) FROM units WHERE rowid>=380000 AND rowid<400000 GROUP BY 1,2,3,4,5,6,7,8 ON CONFLICT DO UPDATE SET n=n+excluded.n;
