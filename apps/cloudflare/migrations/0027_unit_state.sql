-- The Flagged view starts from the crops stored as flagged; without this index finding them reads
-- every crop.
CREATE INDEX IF NOT EXISTS unit_state ON units(origin,state);
