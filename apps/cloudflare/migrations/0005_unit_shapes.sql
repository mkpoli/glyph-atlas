-- Quick review shows a round with similar shapes side by side. `shape_order` places a crop among the
-- crops of its own character (see `atlas review shapes`); it is kept apart from `units`, whose rows
-- are written positionally, and a crop without a row here is shown after the ordered ones.
CREATE TABLE IF NOT EXISTS unit_shapes (id TEXT PRIMARY KEY, shape_order INTEGER NOT NULL);
