-- A crop's geometry flags (`geometry`: what ainu-records measured on its box, `glyph_atlas.ainu_geometry`)
-- are set in place by a publication and changed by no review. The event trigger keeps them through later
-- reviews and undos, as it keeps the tone and the repair status. They name the box they were measured on,
-- so a review that redraws the box leaves them standing for a box the crop no longer has, and the site
-- shows them only while the two agree. The trigger is 0073's with the key kept from the row.
DROP TRIGGER IF EXISTS event_apply;
CREATE TRIGGER IF NOT EXISTS event_apply AFTER INSERT ON events
BEGIN
 UPDATE units SET data=iif(json_type(data,'$.geometry') IS NULL,iif(json_type(data,'$.tone') IS NULL,
 iif(json_type(data,'$.repair') IS NULL,
  iif(json_type(data,'$.context_box') IS NULL,iif(json_type(data,'$.page_number') IS NULL,NEW.after_data,json_set(NEW.after_data,'$.page_number',data->'$.page_number')),
   json_set(iif(json_type(data,'$.page_number') IS NULL,NEW.after_data,json_set(NEW.after_data,'$.page_number',data->'$.page_number')),'$.context_image',data->'$.context_image','$.context_box',data->'$.context_box')),
  json_set(iif(json_type(data,'$.context_box') IS NULL,iif(json_type(data,'$.page_number') IS NULL,NEW.after_data,json_set(NEW.after_data,'$.page_number',data->'$.page_number')),
   json_set(iif(json_type(data,'$.page_number') IS NULL,NEW.after_data,json_set(NEW.after_data,'$.page_number',data->'$.page_number')),'$.context_image',data->'$.context_image','$.context_box',data->'$.context_box')),
   '$.repair',data->'$.repair')),
 json_set(iif(json_type(data,'$.repair') IS NULL,
  iif(json_type(data,'$.context_box') IS NULL,iif(json_type(data,'$.page_number') IS NULL,NEW.after_data,json_set(NEW.after_data,'$.page_number',data->'$.page_number')),
   json_set(iif(json_type(data,'$.page_number') IS NULL,NEW.after_data,json_set(NEW.after_data,'$.page_number',data->'$.page_number')),'$.context_image',data->'$.context_image','$.context_box',data->'$.context_box')),
  json_set(iif(json_type(data,'$.context_box') IS NULL,iif(json_type(data,'$.page_number') IS NULL,NEW.after_data,json_set(NEW.after_data,'$.page_number',data->'$.page_number')),
   json_set(iif(json_type(data,'$.page_number') IS NULL,NEW.after_data,json_set(NEW.after_data,'$.page_number',data->'$.page_number')),'$.context_image',data->'$.context_image','$.context_box',data->'$.context_box')),
   '$.repair',data->'$.repair')),
  '$.tone',data->'$.tone','$.image_size',data->'$.image_size')),
 json_set(iif(json_type(data,'$.tone') IS NULL,
 iif(json_type(data,'$.repair') IS NULL,
  iif(json_type(data,'$.context_box') IS NULL,iif(json_type(data,'$.page_number') IS NULL,NEW.after_data,json_set(NEW.after_data,'$.page_number',data->'$.page_number')),
   json_set(iif(json_type(data,'$.page_number') IS NULL,NEW.after_data,json_set(NEW.after_data,'$.page_number',data->'$.page_number')),'$.context_image',data->'$.context_image','$.context_box',data->'$.context_box')),
  json_set(iif(json_type(data,'$.context_box') IS NULL,iif(json_type(data,'$.page_number') IS NULL,NEW.after_data,json_set(NEW.after_data,'$.page_number',data->'$.page_number')),
   json_set(iif(json_type(data,'$.page_number') IS NULL,NEW.after_data,json_set(NEW.after_data,'$.page_number',data->'$.page_number')),'$.context_image',data->'$.context_image','$.context_box',data->'$.context_box')),
   '$.repair',data->'$.repair')),
 json_set(iif(json_type(data,'$.repair') IS NULL,
  iif(json_type(data,'$.context_box') IS NULL,iif(json_type(data,'$.page_number') IS NULL,NEW.after_data,json_set(NEW.after_data,'$.page_number',data->'$.page_number')),
   json_set(iif(json_type(data,'$.page_number') IS NULL,NEW.after_data,json_set(NEW.after_data,'$.page_number',data->'$.page_number')),'$.context_image',data->'$.context_image','$.context_box',data->'$.context_box')),
  json_set(iif(json_type(data,'$.context_box') IS NULL,iif(json_type(data,'$.page_number') IS NULL,NEW.after_data,json_set(NEW.after_data,'$.page_number',data->'$.page_number')),
   json_set(iif(json_type(data,'$.page_number') IS NULL,NEW.after_data,json_set(NEW.after_data,'$.page_number',data->'$.page_number')),'$.context_image',data->'$.context_image','$.context_box',data->'$.context_box')),
   '$.repair',data->'$.repair')),
  '$.tone',data->'$.tone','$.image_size',data->'$.image_size')),'$.geometry',data->'$.geometry')),
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
