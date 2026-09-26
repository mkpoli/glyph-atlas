-- A cluster decision can carry an issue instead of a form: 'mixed' says the cluster holds more than one
-- form or character and leaves its glyphs as they are; 'character' (with `character` when known) and
-- 'crop' report every glyph that follows the cluster, as a glyph report does for single glyphs.
-- `form_clusters.issue` holds the cluster's current issue. A glyph keeps its cluster's report in
-- `cluster_issue`, `cluster_character` and `cluster_family` beside its own `glyph_*` report, and
-- `issue`, `issue_character` and `issue_family` hold the report that applies to it (its own when it
-- has a glyph decision, else its cluster's), materialised like `form`.
ALTER TABLE form_clusters ADD COLUMN issue TEXT;
ALTER TABLE form_units ADD COLUMN cluster_issue TEXT;
ALTER TABLE form_units ADD COLUMN cluster_character TEXT;
ALTER TABLE form_units ADD COLUMN cluster_family TEXT;
ALTER TABLE form_units ADD COLUMN issue TEXT;
ALTER TABLE form_units ADD COLUMN issue_character TEXT;
ALTER TABLE form_units ADD COLUMN issue_family TEXT;
UPDATE form_units SET issue=glyph_issue,issue_character=glyph_character,issue_family=glyph_family WHERE glyph_set=1;
