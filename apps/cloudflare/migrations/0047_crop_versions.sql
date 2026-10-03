-- The evidence versions of each crop: the pixels a claim about it is made on (docs/design/form-model.md).
-- A version is the crop's id, the checksum of its source image (a local crop's page or pre-cut file,
-- `image_sha256`; a corpus glyph's `source_revision`) and its box on that image in whole pixels, joined
-- as `{unit}@{pixels}@{x},{y},{w},{h}` (the box empty for a whole pre-cut file), so the review service
-- and the publication scripts (`glyph_atlas.evidence`) name it as this column does. A crop with no image
-- checksum, or a box that is not four whole numbers, has no version. A virtual column costs no storage,
-- and adding one rewrites no row.
ALTER TABLE units ADD COLUMN crop_version TEXT GENERATED ALWAYS AS (CASE
  WHEN coalesce(json_extract(data,'$.image_sha256'),json_extract(data,'$.source_revision')) IS NULL THEN NULL
  WHEN coalesce(json_type(data,'$.box'),'null')='null' THEN id||'@'||coalesce(json_extract(data,'$.image_sha256'),json_extract(data,'$.source_revision'))||'@'
  WHEN json_type(data,'$.box.x')='integer' AND json_type(data,'$.box.y')='integer' AND json_type(data,'$.box.w')='integer' AND json_type(data,'$.box.h')='integer'
    THEN id||'@'||coalesce(json_extract(data,'$.image_sha256'),json_extract(data,'$.source_revision'))||'@'
      ||json_extract(data,'$.box.x')||','||json_extract(data,'$.box.y')||','||json_extract(data,'$.box.w')||','||json_extract(data,'$.box.h')
  END) VIRTUAL;
-- Every version a crop has had on the site, first recorded `at`, with the document and crop image it was
-- shown with. A publication that cuts a crop anew adds its new version and leaves the old one, so what
-- an earlier review saw can still be read. A row is never changed. It is removed only once its crop has
-- left the site, which is what taking a withdrawn document down does (`scripts/withdraw_documents.py`);
-- a retired crop's versions stay.
CREATE TABLE IF NOT EXISTS crop_versions (
 id TEXT PRIMARY KEY, unit TEXT NOT NULL, pixels TEXT NOT NULL, box TEXT, document TEXT, image TEXT, at TEXT NOT NULL
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS crop_version_unit ON crop_versions(unit,at);
CREATE INDEX IF NOT EXISTS crop_version_document ON crop_versions(document) WHERE document IS NOT NULL;
-- A plain insert of a version not yet recorded: an `INSERT OR REPLACE INTO units` passes its conflict
-- policy to the statements of its triggers, and a row that is already here must not be replaced.
CREATE TRIGGER IF NOT EXISTS crop_version_insert AFTER INSERT ON units WHEN NEW.crop_version IS NOT NULL
BEGIN
 INSERT INTO crop_versions(id,unit,pixels,box,document,image,at)
   SELECT NEW.crop_version,NEW.id,coalesce(json_extract(NEW.data,'$.image_sha256'),json_extract(NEW.data,'$.source_revision')),
     json_extract(NEW.data,'$.box.x')||','||json_extract(NEW.data,'$.box.y')||','||json_extract(NEW.data,'$.box.w')||','||json_extract(NEW.data,'$.box.h'),
     NEW.document,json_extract(NEW.data,'$.image'),strftime('%Y-%m-%dT%H:%M:%fZ','now')
   WHERE NOT EXISTS (SELECT 1 FROM crop_versions WHERE id=NEW.crop_version);
END;
CREATE TRIGGER IF NOT EXISTS crop_version_update AFTER UPDATE OF id,data ON units
 WHEN NEW.crop_version IS NOT NULL AND NEW.crop_version IS NOT OLD.crop_version
BEGIN
 INSERT INTO crop_versions(id,unit,pixels,box,document,image,at)
   SELECT NEW.crop_version,NEW.id,coalesce(json_extract(NEW.data,'$.image_sha256'),json_extract(NEW.data,'$.source_revision')),
     json_extract(NEW.data,'$.box.x')||','||json_extract(NEW.data,'$.box.y')||','||json_extract(NEW.data,'$.box.w')||','||json_extract(NEW.data,'$.box.h'),
     NEW.document,json_extract(NEW.data,'$.image'),strftime('%Y-%m-%dT%H:%M:%fZ','now')
   WHERE NOT EXISTS (SELECT 1 FROM crop_versions WHERE id=NEW.crop_version);
END;
CREATE TRIGGER IF NOT EXISTS crop_version_kept BEFORE UPDATE ON crop_versions
BEGIN
 SELECT RAISE(ABORT,'crop_version_immutable');
END;
CREATE TRIGGER IF NOT EXISTS crop_version_kept_delete BEFORE DELETE ON crop_versions
 WHEN EXISTS (SELECT 1 FROM units WHERE id=OLD.unit)
BEGIN
 SELECT RAISE(ABORT,'crop_version_immutable');
END;
