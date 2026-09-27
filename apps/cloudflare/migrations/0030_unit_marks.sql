-- What rounds have made of a crop at its current box: `hard` once two reviewers skipped it there,
-- `seen` once a round showed it there and left it unflagged. An undone round's rows stop counting.
-- A crop with neither has no row. Listings count and order thousands of crops by review state, and
-- read it here by id; working it out from `skips` and `seen` for each crop read the table three times
-- over on every visit. The triggers below keep it current whoever writes: a round, an undo, a review
-- or a refresh that moves a box, a publication that writes a crop anew.
CREATE TABLE IF NOT EXISTS unit_marks (id TEXT PRIMARY KEY, mark TEXT NOT NULL) WITHOUT ROWID;
-- Needs fixing starts from the hard crops.
CREATE INDEX IF NOT EXISTS unit_mark ON unit_marks(mark);
-- An undo finds the crops its round named.
CREATE INDEX IF NOT EXISTS skip_submission ON skips(submission);
CREATE INDEX IF NOT EXISTS seen_submission ON seen(submission);
INSERT INTO unit_marks(id,mark) SELECT u.id,iif((SELECT count(DISTINCT k.actor) FROM skips k JOIN submissions b ON b.id=k.submission AND b.undone=0
  WHERE k.target=u.id AND k.box IS json_extract(u.data,'$.box'))>=2,'hard','seen') FROM units u
  WHERE u.id IN (SELECT target FROM skips UNION SELECT target FROM seen)
  AND ((SELECT count(DISTINCT k.actor) FROM skips k JOIN submissions b ON b.id=k.submission AND b.undone=0
    WHERE k.target=u.id AND k.box IS json_extract(u.data,'$.box'))>=2
  OR EXISTS(SELECT 1 FROM seen s JOIN submissions b ON b.id=s.submission AND b.undone=0
    WHERE s.target=u.id AND s.box IS json_extract(u.data,'$.box')));
CREATE TRIGGER IF NOT EXISTS unit_mark_skip AFTER INSERT ON skips
BEGIN
 DELETE FROM unit_marks WHERE id=NEW.target;
 INSERT INTO unit_marks(id,mark) SELECT u.id,iif((SELECT count(DISTINCT k.actor) FROM skips k JOIN submissions b ON b.id=k.submission AND b.undone=0
   WHERE k.target=u.id AND k.box IS json_extract(u.data,'$.box'))>=2,'hard','seen') FROM units u
   WHERE u.id=NEW.target
   AND ((SELECT count(DISTINCT k.actor) FROM skips k JOIN submissions b ON b.id=k.submission AND b.undone=0
     WHERE k.target=u.id AND k.box IS json_extract(u.data,'$.box'))>=2
   OR EXISTS(SELECT 1 FROM seen s JOIN submissions b ON b.id=s.submission AND b.undone=0
     WHERE s.target=u.id AND s.box IS json_extract(u.data,'$.box')));
END;
CREATE TRIGGER IF NOT EXISTS unit_mark_seen AFTER INSERT ON seen
BEGIN
 DELETE FROM unit_marks WHERE id=NEW.target;
 INSERT INTO unit_marks(id,mark) SELECT u.id,iif((SELECT count(DISTINCT k.actor) FROM skips k JOIN submissions b ON b.id=k.submission AND b.undone=0
   WHERE k.target=u.id AND k.box IS json_extract(u.data,'$.box'))>=2,'hard','seen') FROM units u
   WHERE u.id=NEW.target
   AND ((SELECT count(DISTINCT k.actor) FROM skips k JOIN submissions b ON b.id=k.submission AND b.undone=0
     WHERE k.target=u.id AND k.box IS json_extract(u.data,'$.box'))>=2
   OR EXISTS(SELECT 1 FROM seen s JOIN submissions b ON b.id=s.submission AND b.undone=0
     WHERE s.target=u.id AND s.box IS json_extract(u.data,'$.box')));
END;
-- A skip or seen row taken out by hand, as removing crops from the collection does before the crops
-- themselves, takes its part of the mark with it.
CREATE TRIGGER IF NOT EXISTS unit_mark_skip_removed AFTER DELETE ON skips
BEGIN
 DELETE FROM unit_marks WHERE id=OLD.target;
 INSERT INTO unit_marks(id,mark) SELECT u.id,iif((SELECT count(DISTINCT k.actor) FROM skips k JOIN submissions b ON b.id=k.submission AND b.undone=0
   WHERE k.target=u.id AND k.box IS json_extract(u.data,'$.box'))>=2,'hard','seen') FROM units u
   WHERE u.id=OLD.target
   AND ((SELECT count(DISTINCT k.actor) FROM skips k JOIN submissions b ON b.id=k.submission AND b.undone=0
     WHERE k.target=u.id AND k.box IS json_extract(u.data,'$.box'))>=2
   OR EXISTS(SELECT 1 FROM seen s JOIN submissions b ON b.id=s.submission AND b.undone=0
     WHERE s.target=u.id AND s.box IS json_extract(u.data,'$.box')));
END;
CREATE TRIGGER IF NOT EXISTS unit_mark_seen_removed AFTER DELETE ON seen
BEGIN
 DELETE FROM unit_marks WHERE id=OLD.target;
 INSERT INTO unit_marks(id,mark) SELECT u.id,iif((SELECT count(DISTINCT k.actor) FROM skips k JOIN submissions b ON b.id=k.submission AND b.undone=0
   WHERE k.target=u.id AND k.box IS json_extract(u.data,'$.box'))>=2,'hard','seen') FROM units u
   WHERE u.id=OLD.target
   AND ((SELECT count(DISTINCT k.actor) FROM skips k JOIN submissions b ON b.id=k.submission AND b.undone=0
     WHERE k.target=u.id AND k.box IS json_extract(u.data,'$.box'))>=2
   OR EXISTS(SELECT 1 FROM seen s JOIN submissions b ON b.id=s.submission AND b.undone=0
     WHERE s.target=u.id AND s.box IS json_extract(u.data,'$.box')));
END;
CREATE TRIGGER IF NOT EXISTS unit_mark_undo AFTER UPDATE OF undone ON submissions WHEN OLD.undone IS NOT NEW.undone
BEGIN
 DELETE FROM unit_marks WHERE id IN (SELECT target FROM skips WHERE submission=NEW.id UNION SELECT target FROM seen WHERE submission=NEW.id);
 INSERT INTO unit_marks(id,mark) SELECT u.id,iif((SELECT count(DISTINCT k.actor) FROM skips k JOIN submissions b ON b.id=k.submission AND b.undone=0
   WHERE k.target=u.id AND k.box IS json_extract(u.data,'$.box'))>=2,'hard','seen') FROM units u
   WHERE u.id IN (SELECT target FROM skips WHERE submission=NEW.id UNION SELECT target FROM seen WHERE submission=NEW.id)
   AND ((SELECT count(DISTINCT k.actor) FROM skips k JOIN submissions b ON b.id=k.submission AND b.undone=0
     WHERE k.target=u.id AND k.box IS json_extract(u.data,'$.box'))>=2
   OR EXISTS(SELECT 1 FROM seen s JOIN submissions b ON b.id=s.submission AND b.undone=0
     WHERE s.target=u.id AND s.box IS json_extract(u.data,'$.box')));
END;
CREATE TRIGGER IF NOT EXISTS unit_mark_box AFTER UPDATE OF data ON units
 WHEN json_extract(OLD.data,'$.box') IS NOT json_extract(NEW.data,'$.box')
BEGIN
 DELETE FROM unit_marks WHERE id=NEW.id;
 INSERT INTO unit_marks(id,mark) SELECT u.id,iif((SELECT count(DISTINCT k.actor) FROM skips k JOIN submissions b ON b.id=k.submission AND b.undone=0
   WHERE k.target=u.id AND k.box IS json_extract(u.data,'$.box'))>=2,'hard','seen') FROM units u
   WHERE u.id=NEW.id
   AND ((SELECT count(DISTINCT k.actor) FROM skips k JOIN submissions b ON b.id=k.submission AND b.undone=0
     WHERE k.target=u.id AND k.box IS json_extract(u.data,'$.box'))>=2
   OR EXISTS(SELECT 1 FROM seen s JOIN submissions b ON b.id=s.submission AND b.undone=0
     WHERE s.target=u.id AND s.box IS json_extract(u.data,'$.box')));
END;
-- A crop written anew, `INSERT OR REPLACE` included, may hold another box than its mark was made at.
-- Only a crop some round reached can have one.
CREATE TRIGGER IF NOT EXISTS unit_mark_insert AFTER INSERT ON units
 WHEN EXISTS(SELECT 1 FROM skips WHERE target=NEW.id) OR EXISTS(SELECT 1 FROM seen WHERE target=NEW.id)
BEGIN
 DELETE FROM unit_marks WHERE id=NEW.id;
 INSERT INTO unit_marks(id,mark) SELECT u.id,iif((SELECT count(DISTINCT k.actor) FROM skips k JOIN submissions b ON b.id=k.submission AND b.undone=0
   WHERE k.target=u.id AND k.box IS json_extract(u.data,'$.box'))>=2,'hard','seen') FROM units u
   WHERE u.id=NEW.id
   AND ((SELECT count(DISTINCT k.actor) FROM skips k JOIN submissions b ON b.id=k.submission AND b.undone=0
     WHERE k.target=u.id AND k.box IS json_extract(u.data,'$.box'))>=2
   OR EXISTS(SELECT 1 FROM seen s JOIN submissions b ON b.id=s.submission AND b.undone=0
     WHERE s.target=u.id AND s.box IS json_extract(u.data,'$.box')));
END;
