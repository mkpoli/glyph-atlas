import importlib
import json
import sqlite3
import sys

import pytest
from test_atlas3_history import A1, OLD, event, history, sources, version
from test_atlas3_load import load
from test_atlas3_publish import ids, layout, write_source  # noqa: F401 -- the fixture


def check(monkeypatch):
    sys.modules.pop("check", None)
    module = importlib.import_module("check")
    monkeypatch.setattr(sys, "argv", ["check.py"])
    with pytest.raises(SystemExit) as stopped:
        module.main()
    return stopped.value.code


def test_a_fresh_build_passes_every_check(layout, monkeypatch):  # noqa: F811
    root, _publish, _remote = layout
    (root / "out" / "media").mkdir(exist_ok=True)
    assert check(monkeypatch) == 0


@pytest.mark.parametrize("change", ["UPDATE crops SET state = 1 WHERE kind = 0 AND state = 0",
                                    "UPDATE crops SET year = 1999 WHERE kind = 0",
                                    "UPDATE crops SET flags = 1 WHERE kind = 0",
                                    "DELETE FROM crops WHERE kind = 1"])
def test_a_crop_that_differs_from_its_live_record_fails(layout, monkeypatch, change):  # noqa: F811
    root, _publish, _remote = layout
    (root / "out" / "media").mkdir(exist_ok=True)
    db = sqlite3.connect(root / "out" / "atlas.sqlite")
    db.execute(change)
    db.commit()
    assert check(monkeypatch) == 1


def translated(root, monkeypatch, sources, history):
    """A history of reviews, rounds, a favourite, claims and form decisions, translated by history.py."""
    old_ref = version("ex:a1", "p0", OLD)[0]
    box = {"x": 0, "y": 20, "w": 10, "h": 10}

    def claim(id, subject, predicate):
        return (id, None, subject, predicate, "", "", "fm:1", None, None, "observed", "u1", "2026-10-01T00:00:00Z",
                None, None, None, None, None)

    sources(root,
            events=[event("cf:1", "ex:a2", "r-b:s2", "r-b", 0, "2026-10-01T00:00:01Z", box),
                    event("cf:2", "ex:a2", "u1:s1", "u1", 1, "2026-10-01T00:00:02Z", box, verdict="wrong", issue="crop")],
            seen=[("ex:a1", "u1:s1", json.dumps(A1), "p1", "2026-10-01T00:00:02Z"),
                  ("ex:a1", "r-b:s2", json.dumps(OLD), "p0", "2026-10-01T00:00:01Z"),
                  ("codh:g1", "u1:s1", json.dumps(OLD), "x", "2026-10-01T00:00:02Z")],
            skips=[("ex:b1", "r-b:s2", "r-b", json.dumps(A1), "p1", "2026-10-01T00:00:01Z"),
                   ("ex:b1", "u1:s1", "r-b", json.dumps(A1), "p1", "2026-10-01T00:00:02Z")],
            unit_marks=[("ex:a1", "seen"), ("codh:g1", "seen")],
            favourites=[("user-1", "ex:a1", "2026-10-01T00:00:03Z")],
            assertions=[claim("cf:a", "ex:a1", "has_form"), claim("cf:b", "book:a", "date_produced")],
            assertion_evidence=[("cf:a", "crop", old_ref, None), ("cf:b", "source", "src:1", None)],
            current_claims=[("ex:a1", "has_form", "", "", "accepted", "fm:1", None, "[]", "[]", "[]", old_ref, "r", "t")],
            form_decisions=[(1, "d1", "2026-10-01T00:00:00Z", "u1", "cluster", "あ", None, None, "r",
                             json.dumps(["ex:gone", "codh:g1", "ex:a1"]), "", None, None, None)])
    code, _out = history(monkeypatch, root)
    assert code == 0
    (root / "out" / "media").mkdir(exist_ok=True)


def report(root):
    return max((root / "out").glob("check-*.txt")).read_text()


def test_a_correct_history_translation_passes(layout, monkeypatch):  # noqa: F811
    root, _publish, _remote = layout
    translated(root, monkeypatch, sources, history)
    assert check(monkeypatch) == 0
    text = report(root)
    assert "ok   every events row is translated" in text
    assert "ok   every skips row is translated or accounted for (1 an older skip by the same actor)" in text
    assert "ok   the marks recomputed from seen marks and skips are the live ones" in text
    assert "skip every marked crop has its mark as its state: the local copy has not had this translation's marks" in text
    assert "skip every actors.user and favourites.user names an account: no accounts.sqlite" in text


@pytest.mark.parametrize(("change", "line"), [
    ("DELETE FROM events WHERE id = 2", "every events row is translated"),
    ("UPDATE seen SET cut = 0 WHERE cut = -1", "every seen row is translated"),
    ("UPDATE assertion_evidence SET cut = 0 WHERE kind = 'crop'", "every assertion_evidence row is translated"),
    ("UPDATE events SET actor = 3 - actor WHERE id = 1", "every events row is translated"),
    ("UPDATE submissions SET actor = 3 - actor WHERE id = 1", "every submissions row is translated"),
])
def test_a_wrong_or_missing_translated_row_fails(layout, monkeypatch, change, line):  # noqa: F811
    root, _publish, _remote = layout
    translated(root, monkeypatch, sources, history)
    db = sqlite3.connect(root / "out" / "history.sqlite")
    db.execute(change)
    db.commit()
    assert check(monkeypatch) == 1
    assert f"FAIL (1) {line}" in report(root)


def test_a_dropped_row_the_report_does_not_list_fails(layout, monkeypatch):  # noqa: F811
    root, _publish, _remote = layout
    translated(root, monkeypatch, sources, history)
    path = root / "out" / "history-report.txt"
    path.write_text(path.read_text().replace("dropped (1) an older skip by the same actor", ""))
    assert check(monkeypatch) == 1
    assert "1 rows an older skip by the same actor, history-report.txt lists 0" in report(root)


def test_marks_that_differ_from_the_live_ones_fail(layout, monkeypatch):  # noqa: F811
    root, _publish, _remote = layout
    translated(root, monkeypatch, sources, history)
    (root / "source-unit_marks.sqlite").unlink()
    write_source(root, "unit_marks", ["id", "mark"], [("ex:a1", "seen"), ("codh:g1", "hard")])
    assert check(monkeypatch) == 1
    assert "codh:g1: live hard, recomputed seen" in report(root)


@pytest.mark.parametrize(("users", "code"), [(["user-1"], 0), (["user-2"], 1)])
def test_every_actor_and_favourite_user_names_an_account(layout, monkeypatch, users, code):  # noqa: F811
    root, _publish, _remote = layout
    translated(root, monkeypatch, sources, history)
    db = sqlite3.connect(root / "accounts.sqlite")
    db.execute('CREATE TABLE "user"(id TEXT PRIMARY KEY)')
    db.executemany('INSERT INTO "user" VALUES(?)', [(u,) for u in users])
    db.commit()
    assert check(monkeypatch) == code


def test_after_the_load_every_marked_crop_that_was_pending_has_its_mark_as_state(layout, monkeypatch):  # noqa: F811
    root, publish, remote = layout
    translated(root, monkeypatch, sources, history)
    load(publish, remote, monkeypatch)
    assert check(monkeypatch) == 0
    # ex:a1 was pending and takes its mark; codh:g1 was checked and keeps its state.
    assert "ok   every marked crop has its mark as its state (1 not pending at the load keep theirs)" in report(root)
    local = sqlite3.connect(root / "out" / "atlas.sqlite")
    local.execute("UPDATE crops SET state = 0 WHERE id = ?", (ids(root)["ex:a1"],))
    local.commit()
    assert check(monkeypatch) == 1
    assert f"{ids(root)['ex:a1']}: state pending, marked seen" in report(root)
