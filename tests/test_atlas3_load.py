import importlib
import json
import sqlite3
import sys

import pytest
from test_atlas3_history import A1, COLUMNS, event, history, sources
from test_atlas3_publish import crops, ids, layout  # noqa: F401 -- `layout` is the fixture


def load(publish, remote, monkeypatch, *flags):
    sys.modules.pop("load_history", None)
    module = importlib.import_module("load_history")
    monkeypatch.setattr(module, "send", remote.send)
    monkeypatch.setattr(module, "applied", lambda database, stamp: bool(
        remote.query(database, f"SELECT 1 AS n FROM imports WHERE stamp = '{stamp}'")))
    monkeypatch.setattr(sys, "argv", ["load_history.py", *flags])
    module.main()


def test_history_loads_once_and_a_mark_becomes_the_crops_state(layout, monkeypatch):  # noqa: F811
    root, publish, remote = layout
    sources(root, events=[event("cf:1", "ex:a2", "u1:s1", "u1", 1, "2026-10-01T00:00:02Z", {"x": 0, "y": 20, "w": 10, "h": 10})],
            seen=[("ex:a1", "u1:s1", json.dumps(A1), "p1", "2026-10-01T00:00:02Z")], unit_marks=[("ex:a1", "seen")])
    assert history(monkeypatch, root)[0] == 0
    a1 = ids(root)["ex:a1"]
    before = crops(remote)[a1]
    load(publish, remote, monkeypatch)
    db = remote.dbs["glyph-atlas-v3"]
    seen_state = db.execute("SELECT code FROM codes WHERE field='state' AND key='seen'").fetchone()[0]
    assert crops(remote)[a1]["state"] == seen_state and crops(remote)[a1]["revision"] == before["revision"] + 1
    assert db.execute("SELECT count(*) FROM events").fetchone()[0] == 2
    assert db.execute("SELECT count(*) FROM seen").fetchone()[0] == 1
    # The local copy replays the same parts, and the mirror holds the mark states.
    local = sqlite3.connect(root / "out" / "atlas.sqlite")
    assert local.execute("SELECT state, revision FROM crops WHERE id=?", (a1,)).fetchone() == (seen_state, before["revision"] + 1)
    assert sqlite3.connect(root / "out" / "mirror.sqlite").execute(
        "SELECT code FROM codes WHERE field='state' AND key='seen'").fetchone() == (seen_state,)
    # D1 and the local copy hold every migration the rows need.
    for copy in (db, local):
        assert tuple(copy.execute("SELECT 1 FROM pragma_table_info('events') WHERE name='shown'").fetchone()) == (1,)
        assert tuple(copy.execute("SELECT 1 FROM pragma_table_info('events') WHERE name='reverses'").fetchone()) == (1,)
        assert tuple(copy.execute("SELECT 1 FROM sqlite_master WHERE name='corpus_rows'").fetchone()) == (1,)
    # A second run sends nothing.
    load(publish, remote, monkeypatch)
    assert db.execute("SELECT count(*) FROM events").fetchone()[0] == 2
    # The crop with an earlier box comes out as a catalogue naming its cut -1.
    cut = sqlite3.connect(root / "out" / "history-cuts.sqlite").execute("SELECT data FROM units WHERE id='ex:a1'").fetchone()
    assert [(c["cut"], c["box"]) for c in json.loads(cut[0])["cuts"]] == [(-1, {"x": 5, "y": 5, "w": 10, "h": 10})]


def test_a_dry_run_changes_nothing(layout, monkeypatch):  # noqa: F811
    root, publish, remote = layout
    sources(root)
    assert history(monkeypatch, root)[0] == 0
    load(publish, remote, monkeypatch, "--dry-run")
    assert remote.dbs["glyph-atlas-v3"].execute("SELECT count(*) FROM actors").fetchone() == (0,)
    assert not sqlite3.connect(root / "out" / "mirror.sqlite").execute("SELECT 1 FROM codes WHERE key='seen'").fetchone()


def test_a_row_past_d1s_statement_limit_arrives_whole(layout, monkeypatch):  # noqa: F811
    root, publish, remote = layout
    big = json.dumps({"input": {"note": "あ" * 120_000}})
    sources(root, submissions=[("u1:s1", "u1", big, "{}", "2026-10-01T00:00:02Z", 0)])
    assert history(monkeypatch, root)[0] == 0
    load(publish, remote, monkeypatch)
    parts = list((root / "out" / "history-load").glob("history-*/rows-*.sql"))
    assert max(len(line.encode()) for p in parts for line in p.read_text().splitlines()) < 100_000
    assert remote.dbs["glyph-atlas-v3"].execute("SELECT request FROM submissions WHERE key='u1:s1'").fetchone() == (big,)


def test_another_translation_partly_loaded_needs_replace(layout, monkeypatch):  # noqa: F811
    root, publish, remote = layout
    sources(root)
    assert history(monkeypatch, root)[0] == 0
    module = importlib.import_module("load_history")
    # Rows of one translation loaded, its marks not yet set.
    monkeypatch.setattr(module, "marks", lambda history, atlas: ([], 0))
    load(publish, remote, monkeypatch)
    for table in COLUMNS:
        (root / f"source-{table}.sqlite").unlink()
    sources(root, favourites=[("user-1", "ex:a1", "2026-10-01T00:00:03Z")])
    assert history(monkeypatch, root)[0] == 0
    with pytest.raises(SystemExit):
        load(publish, remote, monkeypatch)
    load(publish, remote, monkeypatch, "--replace")
    assert remote.dbs["glyph-atlas-v3"].execute("SELECT count(*) FROM favourites").fetchone() == (1,)
