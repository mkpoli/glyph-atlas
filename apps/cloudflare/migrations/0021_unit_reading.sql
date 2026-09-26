-- Explore's search finds a crop by its reading as well as its character; this index serves the reading
-- half as `unit_character` serves the other.
CREATE INDEX IF NOT EXISTS unit_reading ON units(origin,reading);
