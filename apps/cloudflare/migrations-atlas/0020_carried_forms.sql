-- The described form (an ideographic description) or registered figure (a variation sequence) each crop
-- is claimed to have, under the crop's character or, with none, the family it is filed under, so a
-- character's card reads the forms its crops carry by key. One row a crop. The triggers keep it with the
-- claims and with the crop's character, one put back after a withdrawal among them; a row of an earlier
-- cut or of a withdrawn crop stays, and the reader compares `cut` with the crop's.
CREATE TABLE IF NOT EXISTS carried_forms(crop INTEGER PRIMARY KEY, grapheme INTEGER, form TEXT NOT NULL, cut INTEGER);
CREATE INDEX IF NOT EXISTS carried_form_grapheme ON carried_forms(grapheme, form);
CREATE TRIGGER IF NOT EXISTS carried_form_claimed AFTER INSERT ON current_claims
  WHEN NEW.predicate = 'has_form' AND NEW.scope = '' AND NEW.slot = '' AND NEW.crop IS NOT NULL AND NEW.object IS NOT NULL
    AND NEW.status IN ('asserted','accepted','adjudicated')
BEGIN INSERT OR REPLACE INTO carried_forms(crop, grapheme, form, cut)
  SELECT k.id, coalesce(k.char, k.family), NEW.object, NEW.cut FROM crops k
    JOIN forms f ON f.id = NEW.object JOIN representations r ON r.id = f.anchor
  WHERE k.id = NEW.crop AND r.scheme IN ('ids','ivs'); END;
CREATE TRIGGER IF NOT EXISTS carried_form_dropped AFTER DELETE ON current_claims
  WHEN OLD.predicate = 'has_form' AND OLD.scope = '' AND OLD.slot = '' AND OLD.crop IS NOT NULL
BEGIN DELETE FROM carried_forms WHERE crop = OLD.crop; END;
CREATE TRIGGER IF NOT EXISTS carried_form_moved AFTER UPDATE OF char, family ON crops
  WHEN coalesce(OLD.char, OLD.family) IS NOT coalesce(NEW.char, NEW.family)
BEGIN UPDATE carried_forms SET grapheme = coalesce(NEW.char, NEW.family) WHERE crop = NEW.id; END;
CREATE TRIGGER IF NOT EXISTS carried_form_put_back AFTER INSERT ON crops
BEGIN UPDATE carried_forms SET grapheme = coalesce(NEW.char, NEW.family) WHERE crop = NEW.id; END;
INSERT OR REPLACE INTO carried_forms(crop, grapheme, form, cut)
  SELECT k.id, coalesce(k.char, k.family), c.object, c.cut FROM current_claims c
    JOIN forms f ON f.id = c.object JOIN representations r ON r.id = f.anchor JOIN crops k ON k.id = c.crop
  WHERE c.predicate = 'has_form' AND c.scope = '' AND c.slot = '' AND c.object IS NOT NULL
    AND c.status IN ('asserted','accepted','adjudicated') AND r.scheme IN ('ids','ivs');
