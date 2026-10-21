-- The family a crop is filed under: the grapheme its source names for it (a corpus glyph transcribed
-- も, which may be も or one of its hentaigana). A crop with a character joins its character's family
-- (`graphemes.head`); a crop with no character joins this one, so a review that clears a crop's
-- character keeps it in its family as unassigned. Publisher-owned: scripts/atlas3/fill.py fills it once
-- at the cutover, and build.py and publish.py keep it. Its indexes are in indexes-cutover.sql.
ALTER TABLE crops ADD COLUMN family INTEGER;
-- The reviews since a publication also count by family and witness decade (-1 for none), so the counts
-- of the crops with no character and the decades move with each review. The table is empty until the
-- first review on the new layout.
DROP TABLE tally;
CREATE TABLE tally(gen INTEGER, kind INTEGER, char INTEGER, family INTEGER, book INTEGER, production INTEGER,
  style INTEGER, state INTEGER, quiz INTEGER, decade INTEGER, n INTEGER NOT NULL,
  PRIMARY KEY(gen, char, family, kind, book, production, style, state, quiz, decade)) WITHOUT ROWID;
