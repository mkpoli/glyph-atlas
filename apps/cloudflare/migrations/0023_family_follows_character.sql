-- A crop's grapheme family follows its character. A local crop's record names no grapheme, so an event
-- that restores an earlier character (an undo) left the family of the character it replaced, and the
-- crop was listed under the wrong family. The family is now the one the event names, else the
-- character table's family for the event's character, else the character's own code points (ツ゚ is
-- `U+30C4 U+309A`), so every named crop has one and a family is one indexed lookup. Labels run to at
-- most two code points in what is published; four are spelled out.
DROP TRIGGER IF EXISTS event_apply;
CREATE TRIGGER IF NOT EXISTS event_apply AFTER INSERT ON events
BEGIN
 UPDATE units SET data=iif(json_type(data,'$.repair') IS NULL,
  iif(json_type(data,'$.context_box') IS NULL,NEW.after_data,
   json_set(NEW.after_data,'$.context_image',data->'$.context_image','$.context_box',data->'$.context_box')),
  json_set(iif(json_type(data,'$.context_box') IS NULL,NEW.after_data,
   json_set(NEW.after_data,'$.context_image',data->'$.context_image','$.context_box',data->'$.context_box')),
   '$.repair',data->'$.repair')),
 revision=NEW.expected_revision+1,
 character=iif(origin='corpus',json_extract(NEW.after_data,'$.written_character'),json_extract(NEW.after_data,'$.label')),
 reading=json_extract(NEW.after_data,'$.reading'),
 family=coalesce(json_extract(NEW.after_data,'$.grapheme.code_point'),json_extract(NEW.after_data,'$.grapheme'),
  (SELECT json_extract(c.data,'$.grapheme.code_point') FROM characters c WHERE c.character=iif(origin='corpus',json_extract(NEW.after_data,'$.written_character'),json_extract(NEW.after_data,'$.label'))),
  CASE length(iif(origin='corpus',json_extract(NEW.after_data,'$.written_character'),json_extract(NEW.after_data,'$.label'))) WHEN 1 THEN printf('U+%04X',unicode(iif(origin='corpus',json_extract(NEW.after_data,'$.written_character'),json_extract(NEW.after_data,'$.label')))) WHEN 2 THEN printf('U+%04X',unicode(iif(origin='corpus',json_extract(NEW.after_data,'$.written_character'),json_extract(NEW.after_data,'$.label'))))||printf(' U+%04X',unicode(substr(iif(origin='corpus',json_extract(NEW.after_data,'$.written_character'),json_extract(NEW.after_data,'$.label')),2))) WHEN 3 THEN printf('U+%04X',unicode(iif(origin='corpus',json_extract(NEW.after_data,'$.written_character'),json_extract(NEW.after_data,'$.label'))))||printf(' U+%04X',unicode(substr(iif(origin='corpus',json_extract(NEW.after_data,'$.written_character'),json_extract(NEW.after_data,'$.label')),2)))||printf(' U+%04X',unicode(substr(iif(origin='corpus',json_extract(NEW.after_data,'$.written_character'),json_extract(NEW.after_data,'$.label')),3))) WHEN 4 THEN printf('U+%04X',unicode(iif(origin='corpus',json_extract(NEW.after_data,'$.written_character'),json_extract(NEW.after_data,'$.label'))))||printf(' U+%04X',unicode(substr(iif(origin='corpus',json_extract(NEW.after_data,'$.written_character'),json_extract(NEW.after_data,'$.label')),2)))||printf(' U+%04X',unicode(substr(iif(origin='corpus',json_extract(NEW.after_data,'$.written_character'),json_extract(NEW.after_data,'$.label')),3)))||printf(' U+%04X',unicode(substr(iif(origin='corpus',json_extract(NEW.after_data,'$.written_character'),json_extract(NEW.after_data,'$.label')),4))) END),
 visual_group=json_extract(NEW.after_data,'$.visual_group.id'),
 category=coalesce(json_extract(NEW.after_data,'$.category'),category),
 state=json_extract(NEW.after_data,'$.state')
 WHERE id=NEW.target;
END;
UPDATE units SET family=coalesce((SELECT json_extract(c.data,'$.grapheme.code_point') FROM characters c WHERE c.character=units.character),
  CASE length(character) WHEN 1 THEN printf('U+%04X',unicode(character)) WHEN 2 THEN printf('U+%04X',unicode(character))||printf(' U+%04X',unicode(substr(character,2))) WHEN 3 THEN printf('U+%04X',unicode(character))||printf(' U+%04X',unicode(substr(character,2)))||printf(' U+%04X',unicode(substr(character,3))) WHEN 4 THEN printf('U+%04X',unicode(character))||printf(' U+%04X',unicode(substr(character,2)))||printf(' U+%04X',unicode(substr(character,3)))||printf(' U+%04X',unicode(substr(character,4))) END)
 WHERE origin='local' AND family IS NOT coalesce((SELECT json_extract(c.data,'$.grapheme.code_point') FROM characters c WHERE c.character=units.character),
  CASE length(character) WHEN 1 THEN printf('U+%04X',unicode(character)) WHEN 2 THEN printf('U+%04X',unicode(character))||printf(' U+%04X',unicode(substr(character,2))) WHEN 3 THEN printf('U+%04X',unicode(character))||printf(' U+%04X',unicode(substr(character,2)))||printf(' U+%04X',unicode(substr(character,3))) WHEN 4 THEN printf('U+%04X',unicode(character))||printf(' U+%04X',unicode(substr(character,2)))||printf(' U+%04X',unicode(substr(character,3)))||printf(' U+%04X',unicode(substr(character,4))) END);
