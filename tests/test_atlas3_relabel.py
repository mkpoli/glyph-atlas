import importlib
import json
import sqlite3
import sys

import pytest
from test_atlas3_publish import (  # noqa: F401 -- the fixture
    catalogue,
    copied_by_a_stopped_publication,
    crops,
    ids,
    layout,
    run,
    unit,
)
from test_atlas3_withdraw import entry_of


@pytest.fixture
def relabel(layout, monkeypatch):  # noqa: F811
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
    assert changed <= {"char", "family", "revision", "category"} and after[number["ex:a1"]]["revision"] == before[number["ex:a1"]]["revision"] + 1
    assert all(after[c] == before[c] for c in before if c != number["ex:a1"])
    assert entry_of(root, "ex:a1") == {**was, "label": "う", "script": "hiragana"}
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
    monkeypatch.setattr(relabel, "script_of", lambda c: relabel.Script.HANGUL)
    write(relabel, monkeypatch, tmp_path / "hangul.sqlite", ("ex:a1", "あ", "う"))
    assert "left out: ex:a1: う is neither Han nor kana" in capsys.readouterr().out


def test_a_crop_takes_the_script_category_and_jibo_of_its_new_character(layout, relabel, monkeypatch, tmp_path):  # noqa: F811
    for script, category, jibo in ((relabel.Script.HAN, "kanji", None), (relabel.Script.HENTAIGANA, "kana", "宇")):
        monkeypatch.setattr(relabel, "script_of", lambda c, script=script: script)
        monkeypatch.setattr(relabel.refs, "jibo_of_unit", lambda c, jibo=jibo: jibo)
        path = write(relabel, monkeypatch, tmp_path / f"{category}.sqlite", ("ex:a1", "あ", "う"))
        found, data = sqlite3.connect(path).execute("SELECT category, data FROM units WHERE id='ex:a1'").fetchone()
        assert (found, json.loads(data)["script"], json.loads(data).get("jibo")) == (category, str(script), jibo)
    # A 字母 the crop's published entry holds goes with the character it was of.
    held = relabel.entry_of
    monkeypatch.setattr(relabel, "entry_of", lambda atlas, cid: {**held(atlas, cid), "jibo": "安"})
    monkeypatch.setattr(relabel.refs, "jibo_of_unit", lambda c: None)
    path = write(relabel, monkeypatch, tmp_path / "none.sqlite", ("ex:a1", "あ", "う"))
    assert "jibo" not in json.loads(sqlite3.connect(path).execute("SELECT data FROM units WHERE id='ex:a1'").fetchone()[0])


def test_the_script_of_a_marked_kana_is_its_bases(relabel):
    assert [relabel.script_of(c) for c in ("ツ゚", "か゚", "𛂙", "夷")] == [relabel.Script.KATAKANA, relabel.Script.HIRAGANA,
                                                                      relabel.Script.HENTAIGANA, relabel.Script.HAN]


def refused(publish, remote, monkeypatch, path):
    before, uploaded = crops(remote), list(remote.uploaded)
    with pytest.raises(SystemExit, match="1 crops were reviewed or published since"):
        run(publish, monkeypatch, path)
    assert crops(remote) == before and remote.uploaded == uploaded


def test_a_crop_reviewed_on_the_site_since_stops_the_publish(layout, relabel, monkeypatch, tmp_path):  # noqa: F811
    root, publish, remote = layout
    path = write(relabel, monkeypatch, tmp_path / "r.sqlite", ("ex:a1", "あ", "う"))
    remote.dbs["glyph-atlas-v3"].execute("UPDATE crops SET revision=revision+1 WHERE id=?", (ids(root)["ex:a1"],)).connection.commit()
    refused(publish, remote, monkeypatch, path)


def test_a_review_the_local_copy_took_since_is_not_put_back(layout, relabel, monkeypatch, tmp_path):  # noqa: F811
    root, publish, remote = layout
    path = write(relabel, monkeypatch, tmp_path / "r.sqlite", ("ex:a1", "あ", "う"))
    copied_by_a_stopped_publication(remote, ids(root)["ex:a1"], state=1)
    refused(publish, remote, monkeypatch, path)


def test_a_page_published_since_stops_the_publish_for_a_crop_listed_as_it_stands(layout, relabel, monkeypatch, tmp_path):  # noqa: F811
    root, publish, remote = layout
    for db in (sqlite3.connect(root / "out" / "atlas.sqlite"), remote.dbs["glyph-atlas-v3"]):
        db.execute("UPDATE crops SET state=(SELECT state FROM crops WHERE id=?) WHERE id=?", (ids(root)["ex:a1"], ids(root)["ex:a2"]))
        db.commit()
    path = write(relabel, monkeypatch, tmp_path / "r.sqlite", ("ex:a2", "い", "う"))
    run(publish, monkeypatch, catalogue(tmp_path / "recut.sqlite", [unit("ex:a1", "hl:p1", {"x": 1, "y": 1, "w": 12, "h": 12}, "あ")]))
    before = crops(remote)
    with pytest.raises(SystemExit, match="2 crops were reviewed or published since"):
        run(publish, monkeypatch, path)
    assert crops(remote) == before


def test_a_crop_in_a_reviewers_style_is_left_out(layout, relabel, monkeypatch, tmp_path, capsys):  # noqa: F811
    root, _publish, _remote = layout
    local = sqlite3.connect(root / "out" / "atlas.sqlite")
    local.execute("CREATE TABLE IF NOT EXISTS reviewer_styles(crop INTEGER PRIMARY KEY)")
    local.execute("INSERT INTO reviewer_styles VALUES(?)", (ids(root)["ex:a1"],))
    local.commit()
    path = write(relabel, monkeypatch, tmp_path / "r.sqlite", ("ex:a1", "あ", "う"))
    assert sqlite3.connect(path).execute("SELECT count(*) FROM units").fetchone() == (0,)
    assert "left out: ex:a1: has a reviewer's style" in capsys.readouterr().out
