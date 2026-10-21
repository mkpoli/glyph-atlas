-- The representations that name forms (docs/design/form-model.md), moved from the old database at the
-- cutover (scripts/atlas3/history.py). A value is kept exactly as written; its id hashes scheme,
-- namespace, version and value. A private-use code point names something only with the namespace and
-- version of its mapping. Rows are never changed or removed. `forms.anchor` names one.
CREATE TABLE representations(id TEXT PRIMARY KEY,
  scheme TEXT NOT NULL CHECK (scheme IN ('unicode','ivs','ids','mj','glyphwiki','pua')),
  value TEXT NOT NULL, namespace TEXT, version TEXT,
  CHECK (scheme<>'pua' OR (namespace IS NOT NULL AND version IS NOT NULL))) WITHOUT ROWID;
CREATE INDEX representation_value ON representations(value);
CREATE TRIGGER representation_kept BEFORE UPDATE ON representations
BEGIN SELECT RAISE(ABORT, 'form_immutable'); END;
CREATE TRIGGER representation_kept_delete BEFORE DELETE ON representations
BEGIN SELECT RAISE(ABORT, 'form_immutable'); END;
