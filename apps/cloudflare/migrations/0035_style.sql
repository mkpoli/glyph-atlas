-- The style of a crop's letterforms, a value of `data/vocab/style.yaml`, as `glyph_atlas.style` resolves
-- it: the crop's own, else its page's, else its document's. A publication writes it; the rows already
-- here are `unassessed` until `scripts/publish_styles.py` writes the confirmed document styles onto them.
-- `style_order` is what a gallery sorts and filters by: running and cursive script first, then what
-- nobody has judged, then the formal scripts (seal, clerical, regular) and the print faces. A virtual
-- column costs no storage, and adding one rewrites no row.
ALTER TABLE units ADD COLUMN style TEXT NOT NULL DEFAULT 'unassessed';
ALTER TABLE units ADD COLUMN style_order INTEGER GENERATED ALWAYS AS (CASE WHEN style IN ('cursive','running') THEN 0 WHEN style IN ('unassessed','mixed') THEN 1 ELSE 2 END) VIRTUAL;
ALTER TABLE corpus_units ADD COLUMN style TEXT NOT NULL DEFAULT 'unassessed';
ALTER TABLE corpus_units ADD COLUMN style_order INTEGER GENERATED ALWAYS AS (CASE WHEN style IN ('cursive','running') THEN 0 WHEN style IN ('unassessed','mixed') THEN 1 ELSE 2 END) VIRTUAL;
