-- Forms and their representations (docs/design/form-model.md, `glyph_atlas.representation`). A form is
-- a category of written appearance with an id of its own; the claims that a crop is written in it, that
-- a representation names it, and how it relates to other forms are rows of the ledger (0048). A form
-- named by choosing an encoded character, an IVS or a description is that value's broad form:
-- `anchor` is the representation it is the encoded form of, at most one form each, and its id is
-- derived from the representation. Rows are never changed or removed. The local review service keeps
-- the same tables in its store, made from this file.
CREATE TABLE IF NOT EXISTS forms (
 id TEXT PRIMARY KEY, anchor TEXT UNIQUE, created_by TEXT NOT NULL, created_at TEXT NOT NULL
);
-- A representation's value is kept exactly as written; its id hashes scheme, namespace, version and
-- value. A private-use code point names something only with the namespace and version of its mapping.
CREATE TABLE IF NOT EXISTS representations (
 id TEXT PRIMARY KEY,
 scheme TEXT NOT NULL CHECK (scheme IN ('unicode','ivs','ids','mj','glyphwiki','pua')),
 value TEXT NOT NULL, namespace TEXT, version TEXT,
 CHECK (scheme<>'pua' OR (namespace IS NOT NULL AND version IS NOT NULL))
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS representation_value ON representations(value);
CREATE TRIGGER IF NOT EXISTS form_kept BEFORE UPDATE ON forms
BEGIN
 SELECT RAISE(ABORT,'form_immutable');
END;
CREATE TRIGGER IF NOT EXISTS form_kept_delete BEFORE DELETE ON forms
BEGIN
 SELECT RAISE(ABORT,'form_immutable');
END;
CREATE TRIGGER IF NOT EXISTS representation_kept BEFORE UPDATE ON representations
BEGIN
 SELECT RAISE(ABORT,'form_immutable');
END;
CREATE TRIGGER IF NOT EXISTS representation_kept_delete BEFORE DELETE ON representations
BEGIN
 SELECT RAISE(ABORT,'form_immutable');
END;
-- Written forms are form claims now. The journal (0038) stays as the record of what was saved and is no
-- longer written; `units.written_form` stays empty, since dropping a column rewrites every row of
-- `units`, more than one D1 statement may take.
DROP TRIGGER IF EXISTS written_form_apply;
DROP TRIGGER IF EXISTS written_form_revision_guard;
