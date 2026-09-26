-- The book a local crop comes from, so Explore can show one book's crops. A publication writes it from
-- the crop's document. The rows already here get the page id without its trailing `:<page>`: every
-- local crop published so far has a page id of that form (checked against the stores behind them).
-- The backfill runs in slices of the rowid range, so each statement stays small: one UPDATE over all
-- 200k rows failed twice on the live database on 2026-09-26 with an internal error after five seconds,
-- rolled back each time. A slice past the last row matches nothing, so the slices reach further than the
-- table does today.
ALTER TABLE units ADD COLUMN document TEXT;
UPDATE units SET document=rtrim(rtrim(json_extract(data,'$.page_id'),'0123456789'),':')
 WHERE rowid>=0 AND rowid<10000 AND origin='local' AND json_extract(data,'$.page_id') LIKE '%:%';
UPDATE units SET document=rtrim(rtrim(json_extract(data,'$.page_id'),'0123456789'),':')
 WHERE rowid>=10000 AND rowid<20000 AND origin='local' AND json_extract(data,'$.page_id') LIKE '%:%';
UPDATE units SET document=rtrim(rtrim(json_extract(data,'$.page_id'),'0123456789'),':')
 WHERE rowid>=20000 AND rowid<30000 AND origin='local' AND json_extract(data,'$.page_id') LIKE '%:%';
UPDATE units SET document=rtrim(rtrim(json_extract(data,'$.page_id'),'0123456789'),':')
 WHERE rowid>=30000 AND rowid<40000 AND origin='local' AND json_extract(data,'$.page_id') LIKE '%:%';
UPDATE units SET document=rtrim(rtrim(json_extract(data,'$.page_id'),'0123456789'),':')
 WHERE rowid>=40000 AND rowid<50000 AND origin='local' AND json_extract(data,'$.page_id') LIKE '%:%';
UPDATE units SET document=rtrim(rtrim(json_extract(data,'$.page_id'),'0123456789'),':')
 WHERE rowid>=50000 AND rowid<60000 AND origin='local' AND json_extract(data,'$.page_id') LIKE '%:%';
UPDATE units SET document=rtrim(rtrim(json_extract(data,'$.page_id'),'0123456789'),':')
 WHERE rowid>=60000 AND rowid<70000 AND origin='local' AND json_extract(data,'$.page_id') LIKE '%:%';
UPDATE units SET document=rtrim(rtrim(json_extract(data,'$.page_id'),'0123456789'),':')
 WHERE rowid>=70000 AND rowid<80000 AND origin='local' AND json_extract(data,'$.page_id') LIKE '%:%';
UPDATE units SET document=rtrim(rtrim(json_extract(data,'$.page_id'),'0123456789'),':')
 WHERE rowid>=80000 AND rowid<90000 AND origin='local' AND json_extract(data,'$.page_id') LIKE '%:%';
UPDATE units SET document=rtrim(rtrim(json_extract(data,'$.page_id'),'0123456789'),':')
 WHERE rowid>=90000 AND rowid<100000 AND origin='local' AND json_extract(data,'$.page_id') LIKE '%:%';
UPDATE units SET document=rtrim(rtrim(json_extract(data,'$.page_id'),'0123456789'),':')
 WHERE rowid>=100000 AND rowid<110000 AND origin='local' AND json_extract(data,'$.page_id') LIKE '%:%';
UPDATE units SET document=rtrim(rtrim(json_extract(data,'$.page_id'),'0123456789'),':')
 WHERE rowid>=110000 AND rowid<120000 AND origin='local' AND json_extract(data,'$.page_id') LIKE '%:%';
UPDATE units SET document=rtrim(rtrim(json_extract(data,'$.page_id'),'0123456789'),':')
 WHERE rowid>=120000 AND rowid<130000 AND origin='local' AND json_extract(data,'$.page_id') LIKE '%:%';
UPDATE units SET document=rtrim(rtrim(json_extract(data,'$.page_id'),'0123456789'),':')
 WHERE rowid>=130000 AND rowid<140000 AND origin='local' AND json_extract(data,'$.page_id') LIKE '%:%';
UPDATE units SET document=rtrim(rtrim(json_extract(data,'$.page_id'),'0123456789'),':')
 WHERE rowid>=140000 AND rowid<150000 AND origin='local' AND json_extract(data,'$.page_id') LIKE '%:%';
UPDATE units SET document=rtrim(rtrim(json_extract(data,'$.page_id'),'0123456789'),':')
 WHERE rowid>=150000 AND rowid<160000 AND origin='local' AND json_extract(data,'$.page_id') LIKE '%:%';
UPDATE units SET document=rtrim(rtrim(json_extract(data,'$.page_id'),'0123456789'),':')
 WHERE rowid>=160000 AND rowid<170000 AND origin='local' AND json_extract(data,'$.page_id') LIKE '%:%';
UPDATE units SET document=rtrim(rtrim(json_extract(data,'$.page_id'),'0123456789'),':')
 WHERE rowid>=170000 AND rowid<180000 AND origin='local' AND json_extract(data,'$.page_id') LIKE '%:%';
UPDATE units SET document=rtrim(rtrim(json_extract(data,'$.page_id'),'0123456789'),':')
 WHERE rowid>=180000 AND rowid<190000 AND origin='local' AND json_extract(data,'$.page_id') LIKE '%:%';
UPDATE units SET document=rtrim(rtrim(json_extract(data,'$.page_id'),'0123456789'),':')
 WHERE rowid>=190000 AND rowid<200000 AND origin='local' AND json_extract(data,'$.page_id') LIKE '%:%';
UPDATE units SET document=rtrim(rtrim(json_extract(data,'$.page_id'),'0123456789'),':')
 WHERE rowid>=200000 AND rowid<210000 AND origin='local' AND json_extract(data,'$.page_id') LIKE '%:%';
UPDATE units SET document=rtrim(rtrim(json_extract(data,'$.page_id'),'0123456789'),':')
 WHERE rowid>=210000 AND rowid<220000 AND origin='local' AND json_extract(data,'$.page_id') LIKE '%:%';
UPDATE units SET document=rtrim(rtrim(json_extract(data,'$.page_id'),'0123456789'),':')
 WHERE rowid>=220000 AND rowid<230000 AND origin='local' AND json_extract(data,'$.page_id') LIKE '%:%';
UPDATE units SET document=rtrim(rtrim(json_extract(data,'$.page_id'),'0123456789'),':')
 WHERE rowid>=230000 AND rowid<240000 AND origin='local' AND json_extract(data,'$.page_id') LIKE '%:%';
UPDATE units SET document=rtrim(rtrim(json_extract(data,'$.page_id'),'0123456789'),':')
 WHERE rowid>=240000 AND rowid<250000 AND origin='local' AND json_extract(data,'$.page_id') LIKE '%:%';
UPDATE units SET document=rtrim(rtrim(json_extract(data,'$.page_id'),'0123456789'),':')
 WHERE rowid>=250000 AND rowid<260000 AND origin='local' AND json_extract(data,'$.page_id') LIKE '%:%';
UPDATE units SET document=rtrim(rtrim(json_extract(data,'$.page_id'),'0123456789'),':')
 WHERE rowid>=260000 AND rowid<270000 AND origin='local' AND json_extract(data,'$.page_id') LIKE '%:%';
UPDATE units SET document=rtrim(rtrim(json_extract(data,'$.page_id'),'0123456789'),':')
 WHERE rowid>=270000 AND rowid<280000 AND origin='local' AND json_extract(data,'$.page_id') LIKE '%:%';
UPDATE units SET document=rtrim(rtrim(json_extract(data,'$.page_id'),'0123456789'),':')
 WHERE rowid>=280000 AND rowid<290000 AND origin='local' AND json_extract(data,'$.page_id') LIKE '%:%';
UPDATE units SET document=rtrim(rtrim(json_extract(data,'$.page_id'),'0123456789'),':')
 WHERE rowid>=290000 AND rowid<300000 AND origin='local' AND json_extract(data,'$.page_id') LIKE '%:%';
UPDATE units SET document=rtrim(rtrim(json_extract(data,'$.page_id'),'0123456789'),':')
 WHERE rowid>=300000 AND rowid<310000 AND origin='local' AND json_extract(data,'$.page_id') LIKE '%:%';
UPDATE units SET document=rtrim(rtrim(json_extract(data,'$.page_id'),'0123456789'),':')
 WHERE rowid>=310000 AND rowid<320000 AND origin='local' AND json_extract(data,'$.page_id') LIKE '%:%';
UPDATE units SET document=rtrim(rtrim(json_extract(data,'$.page_id'),'0123456789'),':')
 WHERE rowid>=320000 AND rowid<330000 AND origin='local' AND json_extract(data,'$.page_id') LIKE '%:%';
UPDATE units SET document=rtrim(rtrim(json_extract(data,'$.page_id'),'0123456789'),':')
 WHERE rowid>=330000 AND rowid<340000 AND origin='local' AND json_extract(data,'$.page_id') LIKE '%:%';
UPDATE units SET document=rtrim(rtrim(json_extract(data,'$.page_id'),'0123456789'),':')
 WHERE rowid>=340000 AND rowid<350000 AND origin='local' AND json_extract(data,'$.page_id') LIKE '%:%';
UPDATE units SET document=rtrim(rtrim(json_extract(data,'$.page_id'),'0123456789'),':')
 WHERE rowid>=350000 AND rowid<360000 AND origin='local' AND json_extract(data,'$.page_id') LIKE '%:%';
UPDATE units SET document=rtrim(rtrim(json_extract(data,'$.page_id'),'0123456789'),':')
 WHERE rowid>=360000 AND rowid<370000 AND origin='local' AND json_extract(data,'$.page_id') LIKE '%:%';
UPDATE units SET document=rtrim(rtrim(json_extract(data,'$.page_id'),'0123456789'),':')
 WHERE rowid>=370000 AND rowid<380000 AND origin='local' AND json_extract(data,'$.page_id') LIKE '%:%';
UPDATE units SET document=rtrim(rtrim(json_extract(data,'$.page_id'),'0123456789'),':')
 WHERE rowid>=380000 AND rowid<390000 AND origin='local' AND json_extract(data,'$.page_id') LIKE '%:%';
UPDATE units SET document=rtrim(rtrim(json_extract(data,'$.page_id'),'0123456789'),':')
 WHERE rowid>=390000 AND rowid<400000 AND origin='local' AND json_extract(data,'$.page_id') LIKE '%:%';
CREATE INDEX IF NOT EXISTS unit_document_sample ON units(origin,document,shuffle);
