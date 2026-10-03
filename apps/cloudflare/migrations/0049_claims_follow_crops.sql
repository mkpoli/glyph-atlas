-- A claim about a crop stands only on the crop's current evidence version (0047, 0048). A publication
-- that recuts a crop, writes it anew or deletes it leaves no claim standing on it, so its resolved rows
-- go with the old version, and the claims stay in the ledger to be reassessed on the new one. A slot is
-- resolved again when a claim, an action or a publication names it, so a crop cut back to an earlier
-- version, or a corpus glyph that gains its row after a publication brought its claims, shows those
-- claims from the next one.
CREATE TRIGGER IF NOT EXISTS current_claim_recut AFTER UPDATE OF data ON units
 WHEN NEW.crop_version IS NOT OLD.crop_version
BEGIN
 DELETE FROM current_claims WHERE subject=NEW.id AND crop_version IS NOT NEW.crop_version;
END;
-- `INSERT OR REPLACE` removes the row it replaces without firing the delete trigger, so a crop written
-- anew is checked here.
CREATE TRIGGER IF NOT EXISTS current_claim_rewritten AFTER INSERT ON units
BEGIN
 DELETE FROM current_claims WHERE subject=NEW.id AND crop_version IS NOT NEW.crop_version;
END;
CREATE TRIGGER IF NOT EXISTS current_claim_removed AFTER DELETE ON units
BEGIN
 DELETE FROM current_claims WHERE subject=OLD.id AND crop_version IS NOT NULL;
END;
