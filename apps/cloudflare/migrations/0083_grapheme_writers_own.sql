-- Whether a writer is a character of its own (shape_families.STANDS_FOR): 0 for a form with no reading
-- or definition that only stands for the graphemes it writes (𠮮 for 后 and 𠯑), so a card shows those
-- as its 字種 in place of the form itself. Every row 0081 loaded is a character of its own.
ALTER TABLE grapheme_writers ADD COLUMN own INTEGER NOT NULL DEFAULT 1;
