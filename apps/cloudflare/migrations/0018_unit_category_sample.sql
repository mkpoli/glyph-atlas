-- Explore deals a script's crops in `shuffle` order from a point the seed picks; this index serves the
-- filter and the order together, as `unit_sample` does for every script.
CREATE INDEX IF NOT EXISTS unit_category_sample ON units(origin,category,shuffle);
