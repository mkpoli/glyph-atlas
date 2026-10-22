import importlib
import json
import sqlite3
import sys

import pytest
from test_atlas3_history import COLUMNS, history, sources
from test_atlas3_load import load
from test_atlas3_publish import ids, layout  # noqa: F401 -- `layout` is the fixture

BOX = {"x": 1, "y": 2, "w": 3, "h": 4}


def marks(path, suspects, scored=None):
    path.write_text(json.dumps({"suspects": suspects, **({"scored_ids": scored} if scored is not None else {})}))
    return path


def orders(path, rows):
    path.write_text(json.dumps({"orders": rows}))
    return path


def quiz(remote, monkeypatch, *flags, send=None):
    for name in ("quiz_marks", "load_history"):
        sys.modules.pop(name, None)
    module = importlib.import_module("quiz_marks")
    load_history = importlib.import_module("load_history")
    monkeypatch.setattr(load_history, "send", send or remote.send)
    monkeypatch.setattr(module, "query", remote.query)
    monkeypatch.setattr(module, "applied", lambda database, stamp: bool(
        remote.query(database, f"SELECT 1 AS n FROM imports WHERE stamp = '{stamp}'")))
    monkeypatch.setattr(sys, "argv", ["quiz_marks.py", *map(str, flags)])
    module.main()


def held(db):
    db.row_factory = None
    return {"suspects": db.execute("SELECT crop, p, reads_as, label, box FROM suspects ORDER BY crop").fetchall(),
            "shapes": db.execute("SELECT crop, shape_order FROM shapes ORDER BY crop").fetchall()}


def test_marks_load_by_crop_in_precedence_order(layout, monkeypatch, tmp_path):  # noqa: F811
    root, _publish, remote = layout
    crop = ids(root)
    hosted = marks(tmp_path / "hosted.json", {"ex:a1": {"p": 0.01, "reads_as": "ア", "label": "あ", "box": BOX}},
                   scored=["ex:a1", "ex:a2"])
    corpus = marks(tmp_path / "corpus.json", {
        "ex:a1": {"p": 0.02, "reads_as": None, "label": "あ", "box": None},
        "ex:a2": {"p": 0.03, "reads_as": None, "label": "い", "box": None},
        "codh:g1": {"p": 0.0, "reads_as": None, "label": "か", "box": None},
        "ex:gone": {"p": 0.04, "reads_as": None, "label": "え", "box": None}})
    shapes = orders(tmp_path / "quiz-shapes.json", {"ex:a1": 2, "ex:b1": 0, "ex:gone": 5})
    quiz(remote, monkeypatch, "--suspects", hosted, corpus, "--shapes", shapes)
    db = remote.dbs["glyph-atlas-v3"]
    # A dry run sends nothing.
    assert held(db) == {"suspects": [], "shapes": []}
    report = (root / "out" / "quiz-marks-report.txt").read_text()
    assert "1 suspects on crops the mirror lacks, left out" in report
    assert "1 shapes on crops the mirror lacks, left out" in report

    quiz(remote, monkeypatch, "--suspects", hosted, corpus, "--shapes", shapes, "--send")
    # ex:a1 keeps the hosted mark; ex:a2, scored clean there, has none; the corpus glyph takes its own.
    assert held(db) == {
        "suspects": sorted([(crop["ex:a1"], 0.01, "ア", "あ", json.dumps(BOX, sort_keys=True)),
                            (crop["codh:g1"], 0.0, None, "か", None)]),
        "shapes": sorted([(crop["ex:a1"], 2), (crop["ex:b1"], 0)])}
    # The local copy replays the same parts.
    assert held(sqlite3.connect(root / "out" / "atlas.sqlite")) == held(db)
    stamps = db.execute("SELECT count(*) FROM imports").fetchone()[0]
    quiz(remote, monkeypatch, "--suspects", hosted, corpus, "--shapes", shapes, "--send")
    assert db.execute("SELECT count(*) FROM imports").fetchone()[0] == stamps


def test_a_new_file_replaces_its_table_and_leaves_the_other(layout, monkeypatch, tmp_path):  # noqa: F811
    root, _publish, remote = layout
    crop = ids(root)
    first = marks(tmp_path / "a.json", {"ex:a1": {"p": 0.01, "reads_as": None, "label": "あ", "box": None},
                                        "ex:b1": {"p": 0.02, "reads_as": None, "label": "う", "box": None}})
    quiz(remote, monkeypatch, "--suspects", first, "--shapes", orders(tmp_path / "s.json", {"ex:a1": 1}), "--send")
    second = marks(tmp_path / "b.json", {"ex:a1": {"p": 0.03, "reads_as": None, "label": "あ", "box": None}})
    quiz(remote, monkeypatch, "--suspects", second, "--send")
    assert held(remote.dbs["glyph-atlas-v3"]) == {"suspects": [(crop["ex:a1"], 0.03, None, "あ", None)],
                                                 "shapes": [(crop["ex:a1"], 1)]}
    # removed, added and changed: ex:b1 goes (1 row), ex:a1 changes (its DELETE and INSERT).
    assert "suspects\t1\t1\t0\t1\t3\n" in (root / "out" / "quiz-marks-report.txt").read_text()


def test_a_run_stopped_after_d1_applied_a_part_is_followed_on_rerun(layout, monkeypatch, tmp_path):  # noqa: F811
    root, _publish, remote = layout
    shapes = orders(tmp_path / "s.json", {"ex:a1": 1})

    def stopped(database, file, stamp):
        remote.send(database, file, stamp)
        raise KeyboardInterrupt  # D1 holds the part; the local copy never replays it

    with pytest.raises(KeyboardInterrupt):
        quiz(remote, monkeypatch, "--shapes", shapes, "--send", send=stopped)
    quiz(remote, monkeypatch, "--shapes", shapes, "--send")
    assert held(sqlite3.connect(root / "out" / "atlas.sqlite")) == held(remote.dbs["glyph-atlas-v3"])


def test_a_difference_taken_again_after_a_revert_is_sent(layout, monkeypatch, tmp_path):  # noqa: F811
    root, _publish, remote = layout
    crop = ids(root)
    x = orders(tmp_path / "x.json", {"ex:a1": 1})
    y = orders(tmp_path / "y.json", {})
    quiz(remote, monkeypatch, "--shapes", y, "--send")
    quiz(remote, monkeypatch, "--shapes", x, "--send")
    quiz(remote, monkeypatch, "--shapes", y, "--send")
    quiz(remote, monkeypatch, "--shapes", x, "--send")
    assert held(remote.dbs["glyph-atlas-v3"])["shapes"] == [(crop["ex:a1"], 1)]
    assert held(sqlite3.connect(root / "out" / "atlas.sqlite")) == held(remote.dbs["glyph-atlas-v3"])


@pytest.mark.parametrize("mark", [{"p": "0.1", "reads_as": None, "label": "a", "box": None},
                                  {"p": 2, "reads_as": None, "label": "a", "box": None},
                                  {"p": 0.1, "reads_as": 3, "label": "a", "box": None},
                                  {"p": 0.1, "reads_as": None, "box": None},
                                  {"p": 0.1, "reads_as": None, "label": "a", "box": {"x": 1}},
                                  {"p": 0.1, "reads_as": None, "label": "a", "box": {**BOX, "x": True}},
                                  {"p": 0.1, "reads_as": None, "label": "a", "box": {**BOX, "w": float("nan")}},
                                  {"p": 0.1, "reads_as": None, "label": "a", "box": {**BOX, "h": float("inf")}}])
def test_a_malformed_mark_is_refused(layout, monkeypatch, tmp_path, mark):  # noqa: F811
    _root, _publish, remote = layout
    with pytest.raises(SystemExit, match="ex:a1"):
        quiz(remote, monkeypatch, "--suspects", marks(tmp_path / "m.json", {"ex:a1": mark}))


@pytest.mark.parametrize("order", ["1", -1, True])
def test_a_malformed_shape_order_is_refused(layout, monkeypatch, tmp_path, order):  # noqa: F811
    _root, _publish, remote = layout
    with pytest.raises(SystemExit, match="non-negative integer"):
        quiz(remote, monkeypatch, "--shapes", orders(tmp_path / "s.json", {"ex:a1": order}))


def test_a_history_load_after_quiz_marks_leaves_them(layout, monkeypatch, tmp_path):  # noqa: F811
    root, publish, remote = layout
    crop = ids(root)
    cutover = {"unit_suspects": [("ex:a1", 0.9, "い", "あ", None)], "unit_shapes": [("ex:a1", 7)]}
    sources(root, **cutover)
    assert history(monkeypatch, root)[0] == 0
    load(publish, remote, monkeypatch)
    quiz(remote, monkeypatch, "--suspects", marks(tmp_path / "m.json", {"ex:b1": {"p": 0.2, "reads_as": None, "label": "う", "box": None}}),
         "--shapes", orders(tmp_path / "s.json", {"ex:b1": 0}), "--send")
    after = held(remote.dbs["glyph-atlas-v3"])
    # The history load of a later copy neither deletes the quiz's marks nor puts the cutover's back.
    for table in COLUMNS:
        (root / f"source-{table}.sqlite").unlink()
    sources(root, **cutover, favourites=[("user-1", "ex:a1", "2026-10-01T00:00:03Z")])
    assert history(monkeypatch, root)[0] == 0
    load(publish, remote, monkeypatch)
    assert remote.dbs["glyph-atlas-v3"].execute("SELECT count(*) FROM favourites").fetchone() == (1,)
    assert held(remote.dbs["glyph-atlas-v3"]) == after == {"suspects": [(crop["ex:b1"], 0.2, None, "う", None)],
                                                          "shapes": [(crop["ex:b1"], 0)]}
    assert held(sqlite3.connect(root / "out" / "atlas.sqlite")) == after
