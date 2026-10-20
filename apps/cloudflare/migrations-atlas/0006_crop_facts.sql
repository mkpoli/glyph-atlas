-- The forms tool in glyph-atlas-v3 (decision record 0008): families and clusters with the decisions the
-- Worker records on them, and the glyphs whose form is decided apart from their cluster. Which glyphs a
-- cluster holds, by rank, is an R2 object named by its content, `v3/forms/<list>.json.gz`; a crop's
-- cluster is `crops.cluster`. Loaded at the cutover by scripts/atlas3/crop_facts.py. Glyphs are crops,
-- named by integer id.

-- A grapheme family the forms tool clusters: `forms` lists the forms a decision may name, `revision` is
-- the clustering's, and `list` names its index object `v3/forms/families/<list>.json.gz` (each cluster's
-- first and last glyphs). `count`, `assigned` and `rejected` count its clustered glyphs, those with a
-- form and those reported.
CREATE TABLE form_families(code_point TEXT PRIMARY KEY, char TEXT NOT NULL, label TEXT NOT NULL,
  count INTEGER NOT NULL, cluster_count INTEGER NOT NULL, forms TEXT NOT NULL, assigned INTEGER NOT NULL,
  rejected INTEGER NOT NULL DEFAULT 0, revision TEXT NOT NULL, list TEXT NOT NULL);
CREATE INDEX form_family_order ON form_families(count DESC, code_point);

-- A cluster, numbered once by the publisher's mirror (`codes`, field 'form_cluster', key `key`); `key` is
-- its id in URLs and in `form_decisions.cluster`, and `list` (`<id>-<digest>`) names its glyph list.
-- `form`, `issue`, `character` and `written_family` are its latest cluster decision's (`decision`), which
-- every glyph of it without its own decision takes; an issue 'mixed' gives them no issue.
CREATE TABLE form_clusters(id INTEGER PRIMARY KEY, key TEXT NOT NULL UNIQUE, family TEXT NOT NULL,
  label TEXT NOT NULL, count INTEGER NOT NULL, coherence REAL NOT NULL, shape_position INTEGER NOT NULL,
  size_position INTEGER NOT NULL, list TEXT NOT NULL, form TEXT, issue TEXT, character TEXT, written_family TEXT,
  decision TEXT);
CREATE INDEX form_cluster_shape ON form_clusters(family, shape_position);
CREATE INDEX form_cluster_size ON form_clusters(family, size_position);

-- A glyph with its own glyph decision (`decision`): its form is this row's, not its cluster's. `cluster`
-- is null for a glyph outside the clustering.
CREATE TABLE form_glyphs(crop INTEGER PRIMARY KEY, family TEXT NOT NULL, cluster INTEGER,
  form TEXT, issue TEXT, character TEXT, written_family TEXT, decision TEXT NOT NULL);
CREATE INDEX form_glyph_cluster ON form_glyphs(cluster) WHERE cluster IS NOT NULL;

-- A cluster decision names the list it was made on (`list`) in place of copying its glyphs into `units`
-- and `crops`; a list any decision names is kept in R2.
ALTER TABLE form_decisions ADD COLUMN list TEXT;
CREATE INDEX form_decision_list ON form_decisions(list) WHERE list IS NOT NULL;

-- The clustering in place: `lists` names the load (its run) and keys the Worker's cached forms answers;
-- `loading` is 1 while a load is sending its rows, and no decision is recorded meanwhile.
CREATE TABLE form_state(id INTEGER PRIMARY KEY CHECK(id = 0), lists TEXT NOT NULL, loading INTEGER NOT NULL);
INSERT INTO form_state VALUES(0, '', 0);
CREATE TRIGGER form_decision_while_loading BEFORE INSERT ON form_decisions
  WHEN (SELECT loading FROM form_state WHERE id = 0)
BEGIN SELECT RAISE(ABORT, 'The forms are being republished.'); END;
