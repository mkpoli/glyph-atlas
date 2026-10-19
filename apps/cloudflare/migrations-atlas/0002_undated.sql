-- A character's collected crops with no witness year, which a gallery in year order lists after the
-- dated ones, in the order `crop_char` holds them. `kind` and `year` are in it so the query's
-- conditions on them read no table row.
CREATE INDEX crop_undated ON crops(char, kind, style, shuffle, id, quiz, state, year) WHERE kind = 0 AND year IS NULL;
