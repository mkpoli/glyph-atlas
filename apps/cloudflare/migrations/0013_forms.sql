-- Form assignment on the hosted site. A clustering (`atlas forms cluster`) is published into
-- form_families, form_clusters and form_units by scripts/export_forms_cloudflare.py; decisions are
-- recorded online in form_decisions and applied to the same rows in one batch, so a glyph's
-- current form (`form_units.form`) is always materialised.
--
-- A glyph's form is its own decision when it has one (glyph_set = 1; glyph_form NULL means "not its
-- cluster's form"), otherwise its cluster's. `split` holds its group for "Split into k", one digit
-- per k = 2..8, computed where the embeddings are.
CREATE TABLE IF NOT EXISTS form_families (
 code_point TEXT PRIMARY KEY, char TEXT NOT NULL, label TEXT NOT NULL, count INTEGER NOT NULL,
 cluster_count INTEGER NOT NULL, forms TEXT NOT NULL, assigned INTEGER NOT NULL, revision TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS form_clusters (
 id TEXT PRIMARY KEY, family TEXT NOT NULL, label TEXT NOT NULL, count INTEGER NOT NULL,
 coherence REAL NOT NULL, shape_position INTEGER NOT NULL, size_position INTEGER NOT NULL,
 representatives TEXT NOT NULL, form TEXT, decision TEXT
);
CREATE INDEX IF NOT EXISTS form_cluster_family ON form_clusters(family, shape_position);
CREATE TABLE IF NOT EXISTS form_units (
 id TEXT PRIMARY KEY, family TEXT NOT NULL, cluster TEXT NOT NULL, rank INTEGER NOT NULL,
 similarity REAL NOT NULL, image TEXT, split TEXT NOT NULL,
 cluster_form TEXT, glyph_set INTEGER NOT NULL DEFAULT 0, glyph_form TEXT, glyph_decision TEXT, form TEXT
);
CREATE INDEX IF NOT EXISTS form_unit_cluster ON form_units(cluster, rank);
CREATE INDEX IF NOT EXISTS form_unit_family ON form_units(family, form);
-- Decisions are only ever added; a republished clustering replays them all, in `seq` order within
-- one second, onto its new rows.
CREATE TABLE IF NOT EXISTS form_decisions (
 seq INTEGER PRIMARY KEY, id TEXT NOT NULL UNIQUE, at TEXT NOT NULL, actor TEXT NOT NULL, kind TEXT NOT NULL,
 family TEXT NOT NULL, form TEXT, cluster TEXT, revision TEXT NOT NULL, units TEXT NOT NULL, note TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS form_decision_at ON form_decisions(at);
CREATE INDEX IF NOT EXISTS form_decision_cluster ON form_decisions(cluster, at);
-- One row per glyph a decision names, filled and emptied within a replay.
CREATE TABLE IF NOT EXISTS form_marks (
 id TEXT NOT NULL, at TEXT NOT NULL, seq INTEGER NOT NULL, kind TEXT NOT NULL, form TEXT, decision TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS form_mark_unit ON form_marks(id, at, seq);
-- A corpus glyph's character before any form decision covered it, so a withdrawn form restores it.
CREATE TABLE IF NOT EXISTS form_bases (id TEXT PRIMARY KEY, character TEXT) WITHOUT ROWID;
-- Holds a row while a publication reloads the clustering: a decision taken then would name the
-- glyphs of a half-loaded cluster, so none is recorded until the replay has run.
CREATE TABLE IF NOT EXISTS form_loading (started TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS form_decision_while_loading BEFORE INSERT ON form_decisions
 WHEN EXISTS (SELECT 1 FROM form_loading)
BEGIN SELECT RAISE(ABORT, 'The forms are being republished.'); END;
