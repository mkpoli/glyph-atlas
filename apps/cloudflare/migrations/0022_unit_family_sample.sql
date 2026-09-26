-- Explore deals one grapheme's crops in `shuffle` order from a point the seed picks; this index serves
-- the filter and the order together, as `unit_category_sample` does for a script.
CREATE INDEX IF NOT EXISTS unit_family_sample ON units(origin,family,shuffle);
