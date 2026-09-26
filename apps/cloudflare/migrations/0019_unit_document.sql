-- The book a local crop comes from, so Explore can show one book's crops. A publication writes it from
-- the crop's document; for the rows already here it is the page id without its trailing `:<page>`,
-- which is how every published page id is formed.
ALTER TABLE units ADD COLUMN document TEXT;
UPDATE units SET document=rtrim(rtrim(json_extract(data,'$.page_id'),'0123456789'),':')
 WHERE origin='local' AND json_extract(data,'$.page_id') LIKE '%:%';
CREATE INDEX IF NOT EXISTS unit_document_sample ON units(origin,document,shuffle);
