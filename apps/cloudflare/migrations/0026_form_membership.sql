-- A reviewed glyph can leave shape clustering while its identity decision still
-- applies to corpus search and record details. Only clustered rows appear in Forms.
ALTER TABLE form_units ADD COLUMN clustered INTEGER NOT NULL DEFAULT 1 CHECK(clustered IN (0,1));
