import importlib
import json
import sqlite3
import sys

import pytest
from test_atlas3_publish import (  # noqa: F401 -- `layout` is the fixture
    UNITS,
    crops,
    ids,
    layout,
    run,
    unit,
    write_source,
)

# The scripts folder is on the path once test_atlas3_publish is imported.
votes = importlib.import_module("votes")

ENGINES = [{"name": "NDLkotenOCR", "sha256": "fd75", "provider": "CUDAExecutionProvider"},
           {"name": "Atlas classifier", "sha256": "4360", "provider": "CUDAExecutionProvider"}]


def visual(text="三", image="/atlas/media/a.webp"):
    return json.dumps({"status": "ready", "candidates": [
        {"text": text, "score": 0.77, "engine": "NDLkotenOCR"},
        {"text": "ミ", "engine": "Atlas classifier", "verified": False, "identity_scope": "character", "family": "U+30DF", "score": 0.1}],
        "engines": ENGINES, "votes": [{"text": text, "score": 0.88, "engine": "Atlas classifier", "identity_scope": "character",
                                       "family": "U+4E09", "members": [text]}], "input_image": image, "verified": False},
        ensure_ascii=False)


CONTEXT = json.dumps({"status": "ready", "basis": "transcription", "candidates": [
    {"text": "三", "engine": "Nearby transcription", "offset": 0, "before": "モ載タリ", "after": "十日ノ旅"},
    {"text": "三十", "engine": "Nearby transcription", "offset": 0, "before": "モ載タリ", "after": "日ノ旅行"}],
    "line_id": "hk:1:L13", "anchor": 9, "source_text": "三"}, ensure_ascii=False)
NONE = json.dumps({"status": "unavailable", "candidates": []})
NO_CONTEXT = json.dumps({"status": "unavailable", "candidates": [], "basis": "transcription"})


def test_votes_are_stored_without_what_every_row_repeats_and_restore_whole():
    key, stored = votes.visual(visual())
    guessed = votes.context(CONTEXT)
    assert json.loads(key) == ENGINES and "NDLkotenOCR" not in stored and "Nearby transcription" not in guessed
    back = votes.restore(json.loads(key), stored, guessed)
    assert back["visual"] == {k: v for k, v in json.loads(visual()).items() if k != "input_image"}
    assert back["context"] == json.loads(CONTEXT)
    assert votes.visual(NONE) == (None, None) and votes.context(NO_CONTEXT) is None
    assert votes.restore(None, None, None) == {"visual": json.loads(NONE), "context": json.loads(NO_CONTEXT)}


def facts(remote, monkeypatch, *flags):
    for name in ("crop_facts", "load_history", "history"):
        sys.modules.pop(name, None)
    module = importlib.import_module("crop_facts")
    loader = importlib.import_module("load_history")
    def applied(database, stamp):
        return bool(remote.query(database, f"SELECT 1 AS n FROM imports WHERE stamp = '{stamp}'"))

    for m in (module, loader):
        monkeypatch.setattr(m, "send", remote.send)
        monkeypatch.setattr(m, "applied", applied)
    monkeypatch.setattr(sys, "argv", ["crop_facts.py", *flags])
    module.main()


FORM_UNIT = ["id", "family", "cluster", "rank", "similarity", "image", "split", "clustered", "cluster_form", "cluster_issue", "cluster_character", "cluster_family", "glyph_set", "glyph_form", "glyph_decision", "glyph_issue", "glyph_character", "glyph_family", "form", "issue", "issue_character", "written_family"]
TILE = "/atlas/media/" + "ab" * 32 + ".webp"


def form_unit(id, cluster, rank, clustered=1, form=None, image=TILE):
    return (id, "U+304B", cluster, rank, 0.9, image, "0101010", clustered, form, None, None, None,
            0, None, None, None, None, None, form, None, None, None)


def sources(root):
    write_source(root, "unit_votes", ["id", "origin", "visual", "context"], [
        ("ex:a1", "local", visual(), CONTEXT), ("ex:a2", "local", NONE, NO_CONTEXT), ("ex:gone", "local", visual(), CONTEXT),
        ("codh:g1", "corpus", NONE, NO_CONTEXT)])
    write_source(root, "form_families", ["code_point", "char", "label", "count", "cluster_count", "forms", "assigned", "rejected", "revision"],
                 [("U+304B", "か", "か", 2, 1, "[]", 1, 0, "r1")])
    write_source(root, "form_clusters", ["id", "family", "label", "count", "coherence", "shape_position", "size_position", "representatives", "form", "decision", "issue"], [("U+304B:c1", "U+304B", "Cluster 1", 2, 0.9, 0, 0,
                                json.dumps([{"id": "codh:g1", "image": TILE}]), "か", "d1", None)])
    write_source(root, "form_units", FORM_UNIT, [form_unit("codh:g1", "U+304B:c1", 0, form="か"),
                                                 form_unit("codh:gone", "U+304B:c1", 1),
                                                 form_unit("ex:a2", "U+304B:old", 0, clustered=0, image=None)])
    # Copies by key carry no `_rowid`.
    for table in ("form_families", "form_clusters", "form_units"):
        db = sqlite3.connect(root / f"source-{table}.sqlite")
        db.execute(f"ALTER TABLE {table} DROP COLUMN _rowid")
        db.commit()


def drop_0006(root, remote):
    """A layout loaded before migration 0006."""
    for db in (sqlite3.connect(root / "out" / "atlas.sqlite"), remote.dbs["glyph-atlas-v3"]):
        for table in ("votes", "form_units", "form_clusters", "form_families"):
            db.execute(f"DROP TABLE {table}")
        db.commit()


def test_crop_facts_load_with_integer_crop_ids_through_the_mirror(layout, monkeypatch):  # noqa: F811
    root, _publish, remote = layout
    sources(root)
    drop_0006(root, remote)
    known = ids(root)
    db = remote.dbs["glyph-atlas-v3"]

    facts(remote, monkeypatch)
    assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='votes'").fetchone()
    assert not sqlite3.connect(root / "out" / "mirror.sqlite").execute("SELECT 1 FROM codes WHERE field='models'").fetchone()

    facts(remote, monkeypatch, "--send")
    db.row_factory = None
    assert db.execute("SELECT 1 FROM imports WHERE stamp='migration:0006'").fetchone()
    code = sqlite3.connect(root / "out" / "mirror.sqlite").execute("SELECT code FROM codes WHERE field='models'").fetchone()[0]
    assert json.loads(db.execute("SELECT key FROM codes WHERE field='models' AND code=?", (code,)).fetchone()[0]) == ENGINES
    rows = db.execute("SELECT crop, cut, models, visual, context FROM votes").fetchall()
    assert [(r[0], r[1], r[2]) for r in rows] == [(known["ex:a1"], 0, code)]
    assert votes.restore(ENGINES, rows[0][3], rows[0][4])["context"] == json.loads(CONTEXT)
    assert db.execute("SELECT id, key FROM form_clusters").fetchall() == [(1, "U+304B:c1")]
    assert db.execute("SELECT crop, cluster, rank, image, form, clustered FROM form_units ORDER BY crop").fetchall() == sorted([
        (known["codh:g1"], 1, 0, "ab" * 32, "か", 1), (known["ex:a2"], None, 0, None, None, 0)])
    report = (root / "out" / "crop-facts-report.txt").read_text()
    assert "dropped (1) crop the mirror lacks" in report and "dropped (1) no reading" in report
    assert "1 clusters whose representatives are not their first 12 glyphs by rank" in report
    # The local copy replayed the same parts; a second run sends nothing.
    local = sqlite3.connect(root / "out" / "atlas.sqlite")
    assert local.execute("SELECT crop, cluster FROM form_units ORDER BY crop").fetchall() == \
        db.execute("SELECT crop, cluster FROM form_units ORDER BY crop").fetchall()
    stamps = db.execute("SELECT count(*) FROM imports").fetchone()
    facts(remote, monkeypatch, "--send")
    db.row_factory = None
    assert db.execute("SELECT count(*) FROM imports").fetchone() == stamps


def test_votes_a_publish_wrote_are_kept_by_the_load(layout, monkeypatch):  # noqa: F811
    root, _publish, remote = layout
    sources(root)
    a1 = ids(root)["ex:a1"]
    for db in (sqlite3.connect(root / "out" / "atlas.sqlite"), remote.dbs["glyph-atlas-v3"]):
        db.execute("INSERT INTO votes VALUES(?, 0, NULL, NULL, '{}')", (a1,))
        db.commit()
    facts(remote, monkeypatch, "--send")
    remote.dbs["glyph-atlas-v3"].row_factory = None
    assert remote.dbs["glyph-atlas-v3"].execute("SELECT context FROM votes WHERE crop=?", (a1,)).fetchone() == ("{}",)


def voted(path, units):
    db = sqlite3.connect(path)
    db.execute(f"CREATE TABLE units({','.join(UNITS)},visual,context)")
    db.executemany(f"INSERT INTO units VALUES({','.join('?' * (len(UNITS) + 2))})", units)
    db.commit()
    return path


def test_a_publish_keeps_each_crops_votes_for_its_cut(layout, monkeypatch, tmp_path):  # noqa: F811
    root, publish, remote = layout
    db = remote.dbs["glyph-atlas-v3"]
    a1, a2 = ids(root)["ex:a1"], ids(root)["ex:a2"]
    box1, box2 = {"x": 0, "y": 0, "w": 10, "h": 10}, {"x": 0, "y": 20, "w": 10, "h": 10}
    run(publish, monkeypatch, voted(tmp_path / "c1.sqlite", [(*unit("ex:a1", "hl:p1", box1), visual(), CONTEXT),
                                                             (*unit("ex:a2", "hl:p1", box2, "い", "checked", 3), visual("二"), NO_CONTEXT)]))
    db.row_factory = None
    assert db.execute("SELECT crop, cut, context IS NULL FROM votes ORDER BY crop").fetchall() == [(a1, 0, 0), (a2, 0, 1)]
    # The same readings send nothing.
    stamps = db.execute("SELECT count(*) FROM imports").fetchone()
    run(publish, monkeypatch, voted(tmp_path / "c2.sqlite", [(*unit("ex:a1", "hl:p1", box1), visual(), CONTEXT)]))
    db.row_factory = None
    assert db.execute("SELECT count(*) FROM imports").fetchone() == stamps
    # A re-cut takes the new image's readings for its new cut; no reading removes the crop's votes.
    run(publish, monkeypatch, voted(tmp_path / "c3.sqlite", [(*unit("ex:a1", "hl:p1", {"x": 1, "y": 1, "w": 9, "h": 9}), visual("王"), CONTEXT),
                                                             (*unit("ex:a2", "hl:p1", box2, "い", "checked", 3), NONE, NO_CONTEXT)]))
    cut, stored = db.execute("SELECT cut, visual FROM votes WHERE crop=?", (a1,)).fetchone()
    assert cut == crops(remote)[a1]["cut"] == 1 and json.loads(stored)["votes"][0]["text"] == "王"
    db.row_factory = None
    assert not db.execute("SELECT 1 FROM votes WHERE crop=?", (a2,)).fetchone()
    # A retired crop's votes go with it.
    gone = unit("ex:a1", "hl:p1", {"x": 1, "y": 1, "w": 9, "h": 9})
    run(publish, monkeypatch, voted(tmp_path / "c4.sqlite", [((gone[0], "retired", *gone[2:]) + (NONE, NO_CONTEXT))]))
    db.row_factory = None
    assert db.execute("SELECT count(*) FROM votes").fetchone() == (0,)
    local = sqlite3.connect(root / "out" / "atlas.sqlite")
    assert local.execute("SELECT count(*) FROM votes").fetchone() == (0,)


def test_votes_wait_for_migration_0006(layout, monkeypatch, tmp_path):  # noqa: F811
    root, publish, remote = layout
    drop_0006(root, remote)
    with pytest.raises(SystemExit):
        run(publish, monkeypatch, voted(tmp_path / "c.sqlite", [(*unit("ex:a1", "hl:p1", {"x": 0, "y": 0, "w": 10, "h": 10}),
                                                                  visual(), CONTEXT)]))
