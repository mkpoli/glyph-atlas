-- A crop flagged as a different character carries the issue `character`. Crops flagged before the
-- reading layer was removed carry `reading` for the same issue; their current state takes the
-- current word. Review events keep the words they were saved with.
UPDATE units SET data=json_set(data,'$.issue','character') WHERE json_extract(data,'$.issue')='reading';
