-- The structure layer (docs/decisions/0012-structure-layer.md): a canonical component tree, with no
-- stroke design, that a form may realize (`has_structure`). `label` is the canonical IDS text, `tree`
-- the canonical tree as JSON, and the id is derived from the label, the scheme and the policy
-- (`glyph_atlas.structures`); `version` is the source file the row was read from. Rows are never changed or removed.
CREATE TABLE IF NOT EXISTS structures(id TEXT PRIMARY KEY, label TEXT NOT NULL, tree TEXT NOT NULL,
  scheme TEXT NOT NULL, version TEXT NOT NULL, policy TEXT NOT NULL,
  created_by TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS structure_kept BEFORE UPDATE ON structures
BEGIN SELECT RAISE(ABORT, 'structure_immutable'); END;
CREATE TRIGGER IF NOT EXISTS structure_kept_delete BEFORE DELETE ON structures
BEGIN SELECT RAISE(ABORT, 'structure_immutable'); END;
