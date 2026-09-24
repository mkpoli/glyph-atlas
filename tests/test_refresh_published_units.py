import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("refresh", Path(__file__).parents[1] / "scripts" / "refresh_published_units.py")
refresh = importlib.util.module_from_spec(spec)
spec.loader.exec_module(refresh)

BOX = {"x": 10, "y": 20, "w": 30, "h": 40}


def data(box=BOX, image="/atlas/media/a.webp", revision=1, **extra):
    return json.dumps({"image_sha256": "page", "box": box, "crop_box": box, "image": image, "revision": revision, **extra},
                      ensure_ascii=False, separators=(",", ":"))


def unit(box=BOX, image="/atlas/media/a.webp", quiz=1, state="pending", revision=1):
    return {"id": "hk:1", "origin": "local", "character": "イ", "reading": "イ", "family": None, "visual_group": None,
            "production": "unknown", "category": "イ", "state": state, "quiz": quiz, "priority": 0, "shuffle": 5,
            "revision": revision, "data": data(box, image, revision), "snapshot": "{}", "context": "{}", "visual": "{}"}


def live(box=BOX, image="/atlas/media/a.webp", quiz=1, revision=1000001, reviewed=False, **extra):
    return {"revision": revision, "quiz": quiz, "data": data(box, image, revision, **extra), "reviewed": reviewed}


def test_an_unreviewed_changed_unit_takes_the_catalogue_row_and_revision():
    action, sql = refresh.plan(unit(box={"x": 1, "y": 2, "w": 3, "h": 4}, image="/atlas/media/b.webp"), live())
    assert action == "replace"
    assert "revision=1 WHERE" in sql and sql.endswith("WHERE id='hk:1' AND revision=1000001;")


def test_the_box_keeps_its_stored_key_order():
    moved = {"x": 910, "y": 232, "w": 40, "h": 35}
    _, sql = refresh.plan(unit(box=moved, image="/atlas/media/b.webp"), live())
    assert '"box":{"x":910,"y":232,"w":40,"h":35}' in sql


def test_a_crop_cut_from_the_same_page_but_a_new_box_is_a_changed_crop():
    action, _ = refresh.plan(unit(box={"x": 10, "y": 99, "w": 30, "h": 60}, image="/atlas/media/b.webp"),
                             live(reviewed=True))
    assert action == "replace"


def test_a_reviewed_unit_with_the_same_crop_keeps_its_review_and_may_leave_the_quiz():
    action, sql = refresh.plan(unit(quiz=0), live(reviewed=True, quiz=1))
    assert (action, sql) == ("quiz", "UPDATE units SET quiz=0 WHERE id='hk:1' AND revision=1000001;")


def test_a_reviewed_crop_is_never_put_back_into_the_quiz():
    assert refresh.plan(unit(quiz=1), live(reviewed=True, quiz=0)) == ("skip", None)


def test_an_unreviewed_unit_that_did_not_change_is_left_alone():
    assert refresh.plan(unit(revision=1000001), live()) == ("skip", None)


def test_a_catalogue_revision_equal_to_the_live_one_is_refused():
    with pytest.raises(refresh.Collision):
        refresh.plan(unit(image="/atlas/media/b.webp", revision=1000001), live())


def test_a_missing_reviewed_field_is_an_error():
    row = live(); del row["reviewed"]
    with pytest.raises(KeyError):
        refresh.plan(unit(), row)


def test_quotes_in_values_are_escaped():
    row = unit(image="/atlas/media/b.webp"); row["character"] = "it's"
    _, sql = refresh.plan(row, live())
    assert "character='it''s'" in sql
