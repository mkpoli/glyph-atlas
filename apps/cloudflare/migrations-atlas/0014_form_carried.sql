-- A glyph's form from a cluster decision of an earlier clustering (decision record 0008): when a glyph's
-- latest cluster decision was made on a cluster of an earlier clustering, its `form_glyphs` row keeps
-- that decision's form, issue, character and written family, and `carried` names the decision. Its
-- current cluster's next decision replaces it. A row whose `decision` is not `carried` is the glyph's
-- own decision; inheriting its cluster again restores the carried decision. Loaded at the cutover by
-- scripts/atlas3/crop_facts.py.
ALTER TABLE form_glyphs ADD COLUMN carried TEXT;
