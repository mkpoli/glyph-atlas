-- What a doubt rests on. NULL: the classifier's reading of the crop (`p` its probability of the label).
-- 'form': the crop's nearest crops of its 字種, in other books, all carry one other character of it
-- (`atlas review agreed-marks`); `reads_as` is that character, `p` the share of them that carry the
-- crop's own label, and `box` is NULL. Such a mark was given on the crop's image, so a crop whose box is
-- repaired (`cut`) loses it. scripts/atlas3/quiz_marks.py replaces the marks of each basis apart.
ALTER TABLE suspects ADD COLUMN basis TEXT;
CREATE TRIGGER suspects_cut AFTER UPDATE OF cut ON crops WHEN NEW.cut <> OLD.cut BEGIN
  DELETE FROM suspects WHERE crop = NEW.id AND basis = 'form';
END;
