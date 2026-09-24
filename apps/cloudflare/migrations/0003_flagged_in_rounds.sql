-- A flagged crop stays in Quick review: it is dealt first until someone has seen it since its last
-- review. The apply trigger no longer takes a flagged crop out of the quiz, and the flagged rows it
-- took out come back, except those the alignment repair withheld, which are never dealt.
DROP TRIGGER IF EXISTS event_apply;
CREATE TRIGGER IF NOT EXISTS event_apply AFTER INSERT ON events
BEGIN
 UPDATE units SET data=NEW.after_data, revision=NEW.expected_revision+1,
 character=iif(origin='corpus',json_extract(NEW.after_data,'$.written_character'),json_extract(NEW.after_data,'$.label')),
 reading=json_extract(NEW.after_data,'$.reading'),
 family=coalesce(json_extract(NEW.after_data,'$.grapheme.code_point'),json_extract(NEW.after_data,'$.grapheme'),family),
 visual_group=json_extract(NEW.after_data,'$.visual_group.id'),
 category=coalesce(json_extract(NEW.after_data,'$.category'),category),
 state=json_extract(NEW.after_data,'$.state')
 WHERE id=NEW.target;
END;
UPDATE units SET quiz=1 WHERE origin='local' AND state='flagged' AND quiz=0
 AND json_extract(data,'$.repair.quiz') IS NOT 0;
