import importlib.util
import json
from pathlib import Path

spec = importlib.util.spec_from_file_location("refresh", Path(__file__).parents[1] / "scripts" / "refresh_published_units.py")
refresh = importlib.util.module_from_spec(spec)
spec.loader.exec_module(refresh)


def unit(sha="new", quiz=1, state="pending"):
    return {"id": "hk:1", "origin": "local", "character": "イ", "reading": "イ", "family": None, "visual_group": None,
            "production": "unknown", "category": "イ", "state": state, "quiz": quiz, "priority": 0, "shuffle": 5,
            "revision": 1, "data": json.dumps({"image_sha256": sha, "revision": 1}), "snapshot": "{}",
            "context": "{}", "visual": "{}"}


def test_an_unreviewed_unit_takes_the_new_row_with_a_bumped_revision():
    action, sql = refresh.plan(unit(), {"revision": 7, "quiz": 1, "data": json.dumps({"image_sha256": "old"}), "reviewed": False})
    assert action == "replace"
    assert "revision=8" in sql and '"revision":8' in sql and sql.endswith("WHERE id='hk:1' AND revision=7;")


def test_a_reviewed_unit_with_the_same_crop_keeps_its_review_and_takes_only_the_quiz_flag():
    action, sql = refresh.plan(unit(sha="same", quiz=0), {"revision": 3, "quiz": 1, "data": json.dumps({"image_sha256": "same"}), "reviewed": True})
    assert (action, sql) == ("quiz", "UPDATE units SET quiz=0 WHERE id='hk:1' AND revision=3;")


def test_a_reviewed_unit_with_the_same_crop_and_quiz_flag_is_left_alone():
    assert refresh.plan(unit(sha="same"), {"revision": 3, "quiz": 1, "data": json.dumps({"image_sha256": "same"}), "reviewed": True}) == ("skip", None)


def test_a_reviewed_unit_whose_crop_changed_takes_the_new_row():
    action, sql = refresh.plan(unit(sha="repaired"), {"revision": 3, "quiz": 1, "data": json.dumps({"image_sha256": "old"}), "reviewed": True})
    assert action == "replace" and "revision=4" in sql and "state='pending'" in sql


def test_quotes_in_values_are_escaped():
    row = unit(); row["character"] = "it's"
    _, sql = refresh.plan(row, {"revision": 1, "quiz": 1, "data": json.dumps({"image_sha256": "x"}), "reviewed": False})
    assert "character='it''s'" in sql


def test_an_unreviewed_unit_that_did_not_change_is_left_alone_and_keeps_its_revision():
    live = {"revision": 9, "quiz": 1, "data": json.dumps({"image_sha256": "new", "revision": 9}), "reviewed": False}
    assert refresh.plan(unit(), live) == ("skip", None)
