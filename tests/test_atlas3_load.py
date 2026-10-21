import importlib
import json
import sqlite3
import sys

from test_atlas3_history import A1, COLUMNS, event, history, sources, sub
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
            seen=[("ex:a1", "u1:s1", json.dumps(A1), "p1", "2026-10-01T00:00:02Z")], unit_marks=[("ex:a1", "seen")],
            forms=[("fm:1", "rp:1", "u1", "2026-10-01T00:00:00Z")], representations=[("rp:1", "ids", "⿰亻木", None, None)])
    assert history(monkeypatch, root)[0] == 0
    a1 = ids(root)["ex:a1"]
    before = crops(remote)[a1]
    # A layout built before migration 0008 lacks `representations`; the load applies it.
    for built_ in (remote.dbs["glyph-atlas-v3"], sqlite3.connect(root / "out" / "atlas.sqlite")):
        built_.execute("DROP TABLE representations")
        built_.commit()
    load(publish, remote, monkeypatch)
    db = remote.dbs["glyph-atlas-v3"]
    seen_state = db.execute("SELECT code FROM codes WHERE field='state' AND key='seen'").fetchone()[0]
    assert crops(remote)[a1]["state"] == seen_state and crops(remote)[a1]["revision"] == before["revision"] + 1
    assert db.execute("SELECT count(*) FROM events").fetchone()[0] == 2
    assert db.execute("SELECT count(*) FROM seen").fetchone()[0] == 1
    # Migration 0008 is applied once, and the representations forms name arrive with the forms.
    assert [tuple(r) for r in db.execute("SELECT * FROM representations")] == [("rp:1", "ids", "⿰亻木", None, None)]
    assert db.execute("SELECT count(*) FROM imports WHERE stamp = 'migration:0008'").fetchone()[0] == 1
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
    assert [(c["cut"], c["box"], c["at"]) for c in json.loads(cut[0])["cuts"]] == [(-1, {"x": 5, "y": 5, "w": 10, "h": 10}, "2026-10-03T00:00:00Z")]
    # Every crop the old site recorded versions of gets when its current cut was recorded.
    facts = sqlite3.connect(root / "out" / "history-cuts.sqlite").execute("SELECT id, cut_at, encodings FROM cut_facts ORDER BY id").fetchall()
    assert facts == [("ex:a1", "2026-10-03T00:00:00Z", "[]"), ("ex:a2", "2026-10-03T00:00:00Z", "[]")]


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


def test_a_later_copy_sends_only_the_difference(layout, monkeypatch):  # noqa: F811
    root, publish, remote = layout
    first = {"events": [event("cf:1", "ex:a2", "u1:s1", "u1", 1, "2026-10-01T00:00:02Z", {"x": 0, "y": 20, "w": 10, "h": 10})],
             "seen": [("ex:a1", "u1:s1", json.dumps(A1), "p1", "2026-10-01T00:00:02Z")], "unit_marks": [("ex:a1", "seen")]}
    sources(root, **first)
    assert history(monkeypatch, root)[0] == 0
    load(publish, remote, monkeypatch)
    db = remote.dbs["glyph-atlas-v3"]
    tables = ("actors", "submissions", "events", "seen", "crops")
    before = {t: [tuple(r) for r in db.execute(f'SELECT * FROM "{t}" ORDER BY 1')] for t in tables}
    stamps = {r[0] for r in db.execute("SELECT stamp FROM imports")}
    # A later copy: a favourite and a round by a new actor, stamped before the others, so neither its
    # actor key nor its time sorts it last.
    for table in COLUMNS:
        (root / f"source-{table}.sqlite").unlink()
    sources(root, **first, favourites=[("user-1", "ex:a1", "2026-10-01T00:00:03Z")],
            submissions=[sub("u1:s1", "u1", "2026-10-01T00:00:02Z"), sub("r-b:s2", "r-b", "2026-10-01T00:00:01Z"),
                         sub("r-a:s3", "r-a", "2026-10-01T00:00:00Z")])
    assert history(monkeypatch, root)[0] == 0
    load(publish, remote, monkeypatch)
    sent = {r[0] for r in db.execute("SELECT stamp FROM imports")} - stamps
    # One part of rows, and no marks: the mark was set by the first load.
    assert len(sent) == 1 and ".rows-" in next(iter(sent))
    (run,) = [p for p in (root / "out" / "history-load").glob("history-*") if p.is_dir() and next(iter(sent)).startswith(p.name + ".")]
    figures = dict(line.split(": ", 1) for line in (run / "figures.txt").read_text().splitlines())
    changed = {t: f for t, f in figures.items() if f != "0 new, 0 changed, 0 gone"}
    assert changed == {"actors": "1 new, 0 changed, 0 gone", "submissions": "1 new, 0 changed, 0 gone",
                       "favourites": "1 new, 0 changed, 0 gone", "actor_stats": "1 new, 0 changed, 0 gone"}
    # Every row loaded before keeps its id; the new ones follow.
    after = {t: [tuple(r) for r in db.execute(f'SELECT * FROM "{t}" ORDER BY 1')] for t in tables}
    for t in tables:
        assert after[t][:len(before[t])] == before[t]
    assert db.execute("SELECT id FROM actors WHERE key='r-a'").fetchone()[0] == max(r[0] for r in before["actors"]) + 1
    assert db.execute("SELECT id FROM submissions WHERE key='r-a:s3'").fetchone()[0] == max(r[0] for r in before["submissions"]) + 1
    # The same copy again sends nothing.
    load(publish, remote, monkeypatch)
    assert {r[0] for r in db.execute("SELECT stamp FROM imports")} - stamps == sent
