import importlib
import json
import sqlite3
import sys

import pytest
from test_atlas3_publish import catalogue, crops, ids, layout, run, unit  # noqa: F401 -- the fixture
from test_atlas3_withdraw import entry_of


@pytest.fixture
def relabel(layout):  # noqa: F811
    sys.modules.pop("relabel", None)
    module = importlib.import_module("relabel")
    db = sqlite3.connect(layout[0] / "out" / "atlas-runs.sqlite")
    db.execute("CREATE TABLE IF NOT EXISTS run_text(first INTEGER, size INTEGER, text TEXT NOT NULL, PRIMARY KEY(first, size)) WITHOUT ROWID")
    db.execute("INSERT OR REPLACE INTO run_text SELECT first, size, graphemes FROM runs")
    db.commit()
    return module


def write(relabel, monkeypatch, path, *items):
    listed = path.with_suffix(".json")
    listed.write_text(json.dumps([{"id": i, "label": a, "other": b} for i, a, b in items], ensure_ascii=False))
    monkeypatch.setattr(sys, "argv", ["relabel.py", str(listed), str(path)])
    relabel.main()
    return path


def labels(remote):
    return {r["id"]: r["text"] for r in remote.query("glyph-atlas-v3", "SELECT c.id, g.text FROM crops c JOIN graphemes g ON g.id=c.char")}


def runs(remote):
    return [(r["graphemes"], r["n"]) for r in remote.query("glyph-atlas-v3-runs", "SELECT graphemes, n FROM run_stats")]


def test_a_crop_goes_to_the_other_character_with_its_run_and_nothing_else_changes(layout, relabel, monkeypatch, tmp_path):  # noqa: F811
    root, publish, remote = layout
    number, before, was = ids(root), crops(remote), entry_of(root, "ex:a1")
    run(publish, monkeypatch, write(relabel, monkeypatch, tmp_path / "r.sqlite", ("ex:a1", "あ", "う")))
    after = crops(remote)
    assert labels(remote)[number["ex:a1"]] == "う"
    changed = {c for c in after[number["ex:a1"]] if after[number["ex:a1"]][c] != before[number["ex:a1"]][c]}
    assert changed <= {"char", "family", "revision"} and after[number["ex:a1"]]["revision"] == before[number["ex:a1"]]["revision"] + 1
    assert all(after[c] == before[c] for c in before if c != number["ex:a1"])
    assert entry_of(root, "ex:a1") == {**was, "label": "う"}
    assert runs(remote) == [("うい", 1)]
    # Asked again, the relabel finds the crop filed there.
    again = write(relabel, monkeypatch, tmp_path / "again.sqlite", ("ex:a1", "あ", "う"), ("ex:b1", "う", "う"))
    assert sqlite3.connect(again).execute("SELECT count(*) FROM units").fetchone() == (0,)


def test_a_run_is_rewritten_where_its_second_crop_moves_and_its_first_stays(layout, relabel, monkeypatch, tmp_path):  # noqa: F811
    root, publish, remote = layout
    second = ids(root)["ex:a2"]
    for db in (sqlite3.connect(root / "out" / "atlas.sqlite"), remote.dbs["glyph-atlas-v3"]):
        db.execute("UPDATE crops SET state=(SELECT state FROM crops WHERE id=?) WHERE id=?", (ids(root)["ex:a1"], second)).connection.commit()
    path = write(relabel, monkeypatch, tmp_path / "r.sqlite", ("ex:a2", "い", "う"))
    assert sqlite3.connect(path).execute("SELECT id, character FROM units ORDER BY id").fetchall() == [("ex:a1", "あ"), ("ex:a2", "う")]
    before = crops(remote)[ids(root)["ex:a1"]]
    run(publish, monkeypatch, path)
    assert labels(remote)[second] == "う" and runs(remote) == [("あう", 1)]
    assert crops(remote)[ids(root)["ex:a1"]] == before


def test_a_crop_the_site_holds_otherwise_is_left_out(layout, relabel, monkeypatch, tmp_path, capsys):  # noqa: F811
    path = write(relabel, monkeypatch, tmp_path / "r.sqlite", ("ex:a1", "い", "う"), ("ex:a2", "い", "う"), ("ex:b1", "う", "ゑ"),
                 ("codh:g1", "か", "う"), ("ex:none", "あ", "う"))
    assert sqlite3.connect(path).execute("SELECT count(*) FROM units").fetchone() == (0,)
    assert [line.split(": ", 1)[1] for line in capsys.readouterr().out.splitlines() if line.startswith("left out")] == [
        "codh:g1: not a collected crop on the site", "ex:a1: filed under あ", "ex:a2: is checked",
        "ex:b1: the site holds no ゑ", "ex:none: not a collected crop on the site"]
    with pytest.raises(SystemExit):
        write(relabel, monkeypatch, path, ("ex:a1", "あ", "う"))


def test_a_crop_reviewed_since_keeps_its_character(layout, relabel, monkeypatch, tmp_path):  # noqa: F811
    root, publish, remote = layout
    number = ids(root)["ex:a1"]
    path = write(relabel, monkeypatch, tmp_path / "r.sqlite", ("ex:a1", "あ", "う"))
    remote.dbs["glyph-atlas-v3"].execute("UPDATE crops SET revision=revision+1 WHERE id=?", (number,)).connection.commit()
    try:
        run(publish, monkeypatch, path)
    except SystemExit:
        pass
    assert labels(remote)[number] == "あ"
