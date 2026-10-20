-- The forms tool in glyph-atlas-v3 (decision record 0008): families and clusters with the decisions the
-- Worker records on them, and the few glyphs whose form is decided apart from their cluster. Which
-- glyphs a cluster holds, by rank, is an immutable R2 list per cluster, `v3/forms/<lists>/<cluster>.json.gz`.
-- Loaded at the cutover by scripts/atlas3/crop_facts.py. Glyphs are crops, named by integer id.

-- A grapheme family the forms tool clusters: `forms` lists the forms a decision may name, `revision` is
-- the clustering's and `lists` the R2 folder of its clusters' lists. `count`, `assigned` and `rejected`
-- count its clustered glyphs, those with a form and those reported.
CREATE TABLE form_families(code_point TEXT PRIMARY KEY, char TEXT NOT NULL, label TEXT NOT NULL,
  count INTEGER NOT NULL, cluster_count INTEGER NOT NULL, forms TEXT NOT NULL, assigned INTEGER NOT NULL,
  rejected INTEGER NOT NULL DEFAULT 0, revision TEXT NOT NULL, lists TEXT NOT NULL);
CREATE INDEX form_family_order ON form_families(count DESC, code_point);

-- A cluster, numbered once by the publisher's mirror (`codes`, field 'form_cluster', key `key`); `key` is
-- its id in URLs and in `form_decisions.cluster`. `form`, `issue`, `character` and `written_family` are
-- its latest cluster decision's (`decision`), which every glyph of it without its own decision takes;
-- an issue 'mixed' gives them no issue.
CREATE TABLE form_clusters(id INTEGER PRIMARY KEY, key TEXT NOT NULL UNIQUE, family TEXT NOT NULL,
  label TEXT NOT NULL, count INTEGER NOT NULL, coherence REAL NOT NULL, shape_position INTEGER NOT NULL,
  size_position INTEGER NOT NULL, form TEXT, issue TEXT, character TEXT, written_family TEXT, decision TEXT);
CREATE INDEX form_cluster_shape ON form_clusters(family, shape_position);
CREATE INDEX form_cluster_size ON form_clusters(family, size_position);

-- A glyph whose form is not its cluster's: one with its own glyph decision (`decision`), or one outside
-- the clustering kept for a decision made on it (no `cluster`; `own` 0 when it keeps what its former
-- cluster's decision gave it). The applied form is this row's, else the cluster's.
CREATE TABLE form_glyphs(crop INTEGER PRIMARY KEY, family TEXT NOT NULL, cluster INTEGER,
  own INTEGER NOT NULL CHECK(own IN (0, 1)), form TEXT, issue TEXT, character TEXT, written_family TEXT, decision TEXT);
CREATE INDEX form_glyph_cluster ON form_glyphs(cluster) WHERE cluster IS NOT NULL;
