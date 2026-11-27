-- Collected crops a machine found no reason to ask a reviewer about: their nearest crops carry their
-- character and their written form (`atlas review agreed-marks`). `label` is the character the crop was
-- judged under, so a mark stops counting once the crop is filed under another, and a crop whose box is
-- repaired (`cut`) loses its mark: the image judged is gone. A round deals a grapheme's agreed crops
-- after its other crops. scripts/atlas3/quiz_marks.py replaces the table whole.
CREATE TABLE agreed(crop INTEGER PRIMARY KEY, label TEXT NOT NULL);
CREATE TRIGGER agreed_cut AFTER UPDATE OF cut ON crops WHEN NEW.cut <> OLD.cut BEGIN
  DELETE FROM agreed WHERE crop = NEW.id;
END;
