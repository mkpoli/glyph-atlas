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
    action, _ = refresh.plan(unit(box={"x": 10, "y": 99, "w": 30, "h": 60}, image="/atlas/media/b.webp"), live())
    assert action == "replace"


def test_a_reviewed_unit_whose_crop_changed_is_held_back():
    assert refresh.plan(unit(box={"x": 10, "y": 99, "w": 30, "h": 60}, image="/atlas/media/b.webp"),
                        live(reviewed=True)) == ("hold", None)


def test_an_unreviewed_unit_whose_only_change_is_the_revision_is_replaced():
    action, sql = refresh.plan(unit(revision=2), live(revision=1000001))
    assert action == "replace" and "revision=2 WHERE" in sql


def test_a_reviewed_unit_with_the_same_crop_keeps_its_review_and_may_leave_the_quiz():
    action, sql = refresh.plan(unit(quiz=0), live(reviewed=True, quiz=1))
    assert (action, sql) == ("quiz", "UPDATE units SET quiz=0 WHERE id='hk:1' AND revision=1000001;")


def test_a_reviewed_crop_is_never_put_back_into_the_quiz():
    assert refresh.plan(unit(quiz=1), live(reviewed=True, quiz=0)) == ("skip", None)


def test_an_unreviewed_unit_that_did_not_change_is_left_alone():
    assert refresh.plan(unit(revision=1000001), live(revision=1000001)) == ("skip", None)


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


def test_the_same_json_written_differently_is_unchanged():
    row = live(revision=1000001)
    row["data"] = json.dumps(json.loads(row["data"]), ensure_ascii=True, indent=1)
    assert refresh.plan(unit(revision=1000001), row) == ("skip", None)


WIDE = {"x": 0, "y": 0, "w": 90, "h": 120}


def with_context(row, image="/atlas/media/wide.webp", box=WIDE):
    row["data"] = json.dumps({**json.loads(row["data"]), "context_image": image, "context_box": box},
                             ensure_ascii=False, separators=(",", ":"))
    return row


def test_a_new_context_alone_is_set_in_place_and_keeps_the_revision():
    old = with_context(live(revision=1000001), "/atlas/media/narrow.webp", {"x": 5, "y": 10, "w": 40, "h": 60})
    action, sql = refresh.plan(with_context(unit(revision=1000001)), old)
    assert action == "in-place"
    assert sql == ("UPDATE units SET data=json_set(data, '$.context_image', json('\"/atlas/media/wide.webp\"'), "
                   "'$.context_box', json('{\"x\":0,\"y\":0,\"w\":90,\"h\":120}')) WHERE id='hk:1' AND revision=1000001;")
    assert "revision=" not in sql.split(" WHERE ")[0]


def test_a_reviewed_unit_takes_a_new_context_and_may_leave_the_quiz_in_the_same_statement():
    old = with_context(live(reviewed=True, quiz=1), "/atlas/media/narrow.webp")
    action, sql = refresh.plan(with_context(unit(quiz=0)), old)
    assert action == "in-place" and "json_set(data" in sql and ", quiz=0 WHERE" in sql


def test_a_reviewed_unit_whose_crop_changed_is_held_back_even_with_a_new_context():
    old = with_context(live(reviewed=True), "/atlas/media/narrow.webp")
    assert refresh.plan(with_context(unit(image="/atlas/media/b.webp")), old) == ("hold", None)


def test_an_unreviewed_unit_with_other_changes_is_replaced_whole():
    old = with_context(live(), "/atlas/media/narrow.webp")
    action, sql = refresh.plan(with_context(unit(image="/atlas/media/b.webp")), old)
    assert action == "replace" and "wide.webp" in sql


def test_a_context_that_did_not_change_is_left_alone():
    assert refresh.plan(with_context(unit(revision=1000001)), with_context(live(revision=1000001))) == ("skip", None)


def test_the_statement_runs_in_sqlite_and_keeps_the_key_order():
    import sqlite3
    old = with_context(live(revision=1000001), "/atlas/media/narrow.webp", {"x": 5, "y": 10, "w": 40, "h": 60})
    _, sql = refresh.plan(with_context(unit(revision=1000001)), old)
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE units (id TEXT, revision INTEGER, quiz INTEGER, data TEXT)")
    db.execute("INSERT INTO units VALUES ('hk:1', 1000001, 1, ?)", (old["data"],))
    db.execute(sql)
    stored = db.execute("SELECT data FROM units").fetchone()[0]
    assert list(json.loads(stored)) == list(json.loads(old["data"]))
    assert json.loads(stored)["context_box"] == WIDE and json.loads(stored)["context_image"] == "/atlas/media/wide.webp"


def with_data(row, **fields):
    row["data"] = json.dumps({**json.loads(row["data"]), **fields}, ensure_ascii=False, separators=(",", ":"))
    return row


def test_a_new_repair_status_is_set_in_place_and_the_quiz_follows_it():
    old = with_data(live(revision=1000001, quiz=0), repair={"status": "uncertain", "quiz": False})
    new = with_data(unit(revision=1000001, quiz=1), repair={"status": "confirmed", "quiz": True})
    action, sql = refresh.plan(new, old)
    assert action == "in-place"
    assert sql == ("UPDATE units SET data=json_set(data, '$.repair', json('{\"status\":\"confirmed\",\"quiz\":true}')), "
                   "quiz=1 WHERE id='hk:1' AND revision=1000001;")


def test_a_reviewed_unit_takes_a_new_repair_status_but_is_not_dealt_again():
    old = with_data(live(reviewed=True, quiz=0), repair={"status": "uncertain"})
    new = with_data(unit(quiz=1), repair={"status": "confirmed"})
    action, sql = refresh.plan(new, old)
    assert action == "in-place" and "'$.repair'" in sql and "quiz=" not in sql


def test_an_unreviewed_unit_whose_only_change_is_the_quiz_leaves_it_in_place():
    action, sql = refresh.plan(unit(revision=1000001, quiz=0), live(revision=1000001, quiz=1))
    assert (action, sql) == ("quiz", "UPDATE units SET quiz=0 WHERE id='hk:1' AND revision=1000001;")


def test_a_null_shape_order_is_the_same_as_none():
    assert refresh.plan(with_data(unit(revision=1000001), shape_order=None), live(revision=1000001)) == ("skip", None)
    assert refresh.plan(unit(revision=1000001), with_data(live(revision=1000001), shape_order=None)) == ("skip", None)
