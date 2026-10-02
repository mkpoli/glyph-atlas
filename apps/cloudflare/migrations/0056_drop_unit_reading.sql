-- A crop is its character; it carries no reading. The key leaves each crop's record, a rowid range at
-- a time so no statement rewrites the whole table's records at once; then the trigger is 0029's without
-- the `reading` column, and the column goes with its index. Events keep what they recorded.
UPDATE units SET data=json_remove(data,'$.reading') WHERE rowid>=0 AND rowid<25000 AND json_type(data,'$.reading') IS NOT NULL;
UPDATE units SET data=json_remove(data,'$.reading') WHERE rowid>=25000 AND rowid<50000 AND json_type(data,'$.reading') IS NOT NULL;
UPDATE units SET data=json_remove(data,'$.reading') WHERE rowid>=50000 AND rowid<75000 AND json_type(data,'$.reading') IS NOT NULL;
UPDATE units SET data=json_remove(data,'$.reading') WHERE rowid>=75000 AND rowid<100000 AND json_type(data,'$.reading') IS NOT NULL;
UPDATE units SET data=json_remove(data,'$.reading') WHERE rowid>=100000 AND rowid<125000 AND json_type(data,'$.reading') IS NOT NULL;
UPDATE units SET data=json_remove(data,'$.reading') WHERE rowid>=125000 AND rowid<150000 AND json_type(data,'$.reading') IS NOT NULL;
UPDATE units SET data=json_remove(data,'$.reading') WHERE rowid>=150000 AND rowid<175000 AND json_type(data,'$.reading') IS NOT NULL;
UPDATE units SET data=json_remove(data,'$.reading') WHERE rowid>=175000 AND rowid<200000 AND json_type(data,'$.reading') IS NOT NULL;
UPDATE units SET data=json_remove(data,'$.reading') WHERE rowid>=200000 AND rowid<225000 AND json_type(data,'$.reading') IS NOT NULL;
UPDATE units SET data=json_remove(data,'$.reading') WHERE rowid>=225000 AND rowid<250000 AND json_type(data,'$.reading') IS NOT NULL;
UPDATE units SET data=json_remove(data,'$.reading') WHERE rowid>=250000 AND rowid<275000 AND json_type(data,'$.reading') IS NOT NULL;
UPDATE units SET data=json_remove(data,'$.reading') WHERE rowid>=275000 AND rowid<300000 AND json_type(data,'$.reading') IS NOT NULL;
UPDATE units SET data=json_remove(data,'$.reading') WHERE rowid>=300000 AND rowid<325000 AND json_type(data,'$.reading') IS NOT NULL;
UPDATE units SET data=json_remove(data,'$.reading') WHERE rowid>=325000 AND rowid<350000 AND json_type(data,'$.reading') IS NOT NULL;
UPDATE units SET data=json_remove(data,'$.reading') WHERE rowid>=350000 AND json_type(data,'$.reading') IS NOT NULL;
DROP TRIGGER IF EXISTS event_apply;
CREATE TRIGGER IF NOT EXISTS event_apply AFTER INSERT ON events
BEGIN
 UPDATE units SET data=iif(json_type(data,'$.repair') IS NULL,
  iif(json_type(data,'$.context_box') IS NULL,iif(json_type(data,'$.page_number') IS NULL,NEW.after_data,json_set(NEW.after_data,'$.page_number',data->'$.page_number')),
   json_set(iif(json_type(data,'$.page_number') IS NULL,NEW.after_data,json_set(NEW.after_data,'$.page_number',data->'$.page_number')),'$.context_image',data->'$.context_image','$.context_box',data->'$.context_box')),
  json_set(iif(json_type(data,'$.context_box') IS NULL,iif(json_type(data,'$.page_number') IS NULL,NEW.after_data,json_set(NEW.after_data,'$.page_number',data->'$.page_number')),
   json_set(iif(json_type(data,'$.page_number') IS NULL,NEW.after_data,json_set(NEW.after_data,'$.page_number',data->'$.page_number')),'$.context_image',data->'$.context_image','$.context_box',data->'$.context_box')),
   '$.repair',data->'$.repair')),
 revision=NEW.expected_revision+1,
 character=iif(origin='corpus',json_extract(NEW.after_data,'$.written_character'),json_extract(NEW.after_data,'$.label')),
 family=coalesce(json_extract(NEW.after_data,'$.grapheme.code_point'),json_extract(NEW.after_data,'$.grapheme'),
  (SELECT json_extract(c.data,'$.grapheme.code_point') FROM characters c WHERE c.character=iif(origin='corpus',json_extract(NEW.after_data,'$.written_character'),json_extract(NEW.after_data,'$.label'))),
  CASE length(iif(origin='corpus',json_extract(NEW.after_data,'$.written_character'),json_extract(NEW.after_data,'$.label'))) WHEN 1 THEN printf('U+%04X',unicode(iif(origin='corpus',json_extract(NEW.after_data,'$.written_character'),json_extract(NEW.after_data,'$.label')))) WHEN 2 THEN printf('U+%04X',unicode(iif(origin='corpus',json_extract(NEW.after_data,'$.written_character'),json_extract(NEW.after_data,'$.label'))))||printf(' U+%04X',unicode(substr(iif(origin='corpus',json_extract(NEW.after_data,'$.written_character'),json_extract(NEW.after_data,'$.label')),2))) WHEN 3 THEN printf('U+%04X',unicode(iif(origin='corpus',json_extract(NEW.after_data,'$.written_character'),json_extract(NEW.after_data,'$.label'))))||printf(' U+%04X',unicode(substr(iif(origin='corpus',json_extract(NEW.after_data,'$.written_character'),json_extract(NEW.after_data,'$.label')),2)))||printf(' U+%04X',unicode(substr(iif(origin='corpus',json_extract(NEW.after_data,'$.written_character'),json_extract(NEW.after_data,'$.label')),3))) WHEN 4 THEN printf('U+%04X',unicode(iif(origin='corpus',json_extract(NEW.after_data,'$.written_character'),json_extract(NEW.after_data,'$.label'))))||printf(' U+%04X',unicode(substr(iif(origin='corpus',json_extract(NEW.after_data,'$.written_character'),json_extract(NEW.after_data,'$.label')),2)))||printf(' U+%04X',unicode(substr(iif(origin='corpus',json_extract(NEW.after_data,'$.written_character'),json_extract(NEW.after_data,'$.label')),3)))||printf(' U+%04X',unicode(substr(iif(origin='corpus',json_extract(NEW.after_data,'$.written_character'),json_extract(NEW.after_data,'$.label')),4))) END),
 visual_group=json_extract(NEW.after_data,'$.visual_group.id'),
 category=coalesce(json_extract(NEW.after_data,'$.category'),category),
 state=json_extract(NEW.after_data,'$.state')
 WHERE id=NEW.target;
END;
DROP INDEX IF EXISTS unit_reading;
ALTER TABLE units DROP COLUMN reading;
