-- A corpus glyph's character before a forms decision first moved it (decision record 0008): the Worker
-- moves `crops.char` of an unnamed corpus glyph (kind 1, revision 0) listed in `corpus_follow` to its
-- decided form, and back to this character once no decision names a form or report for it. `char` is
-- a `graphemes` id, null for a glyph that had none.
CREATE TABLE form_bases(crop INTEGER PRIMARY KEY, char INTEGER);
