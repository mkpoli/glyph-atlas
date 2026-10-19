import gzip
import importlib
import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "atlas3"))

PAGE = 1 << 24


def unit(id, page, box, character="あ", state="pending", revision=0, quiz=1, document="book:a"):
    data = {"page_id": page, "box": box, "image": f"/atlas/media/{id.replace(':', '')}.webp", "label": character}
    return (id, "local", character, None, "printed", "kana", state, revision, quiz, 1, document, "unassessed", None,
            json.dumps(data))


UNITS = ("id", "origin", "character", "visual_group", "production", "category", "state", "revision", "quiz", "shuffle",
         "document", "style", "written_form", "data")


def write_source(root, name, cols, rows):
    db = sqlite3.connect(root / f"source-{name}.sqlite")
    db.execute(f"CREATE TABLE {name}(_rowid,{','.join(cols)})")
    db.executemany(f"INSERT INTO {name} VALUES({','.join('?' * (len(cols) + 1))})", [(i + 1, *r) for i, r in enumerate(rows)])
    db.commit()


def catalogue(path, units, ngrams=()):
    db = sqlite3.connect(path)
    db.execute(f"CREATE TABLE units({','.join(UNITS)})")
    db.executemany(f"INSERT INTO units VALUES({','.join('?' * len(UNITS))})", units)
    db.execute("CREATE TABLE unit_ngrams(first,size,second,third,text,document,vertical,hand_order,shuffle,graphemes)")
    db.executemany("INSERT INTO unit_ngrams VALUES(?,?,?,?,?,?,?,?,?,?)", ngrams)
    db.commit()
    return path


class FakeD1:
    """Applies what publish.py sends to two in-memory databases built from the same schemas."""

    def __init__(self, root):
        self.dbs = {"glyph-atlas-v3": sqlite3.connect(root / "out" / "atlas.sqlite.remote"),
                    "glyph-atlas-v3-runs": sqlite3.connect(root / "out" / "atlas-runs.sqlite.remote")}
        for name, local in (("glyph-atlas-v3", "atlas.sqlite"), ("glyph-atlas-v3-runs", "atlas-runs.sqlite")):
            sqlite3.connect(root / "out" / local).backup(self.dbs[name])
            self.dbs[name].execute("CREATE TABLE IF NOT EXISTS imports(stamp TEXT PRIMARY KEY, at INTEGER NOT NULL)")
        self.uploaded = []

    def send(self, database, file, stamp):
        db = self.dbs[database]
        if not db.execute("SELECT 1 FROM imports WHERE stamp=?", (stamp,)).fetchone():
            db.executescript("BEGIN;\n" + Path(file).read_text() + "COMMIT;\n")
        return True

    def query(self, database, sql):
        db = self.dbs[database]
        db.row_factory = sqlite3.Row
        return [dict(r) for r in db.execute(sql)]

    def upload(self, files):
        self.uploaded += [f.name for f in files]


@pytest.fixture
def layout(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS3_DIR", str(tmp_path))
    write_source(tmp_path, "units", UNITS, [
        unit("ex:a1", "hl:p1", {"x": 0, "y": 0, "w": 10, "h": 10}, "あ"),
        unit("ex:a2", "hl:p1", {"x": 0, "y": 20, "w": 10, "h": 10}, "い", state="checked", revision=3),
        unit("ex:b1", "hl:p2", {"x": 0, "y": 0, "w": 10, "h": 10}, "う"),
        ("codh:g1", "corpus", "か", None, "printed", "kana", "checked", 2, 0, 1, "codh:b", "unassessed", None, "{}"),
    ])
    write_source(tmp_path, "corpus_units", ("id", "character", "family", "visual_group", "shuffle", "object", "offset",
                                            "size", "production", "named", "style", "document", "label"),
                 [("codh:g1", "が", None, None, 7, "corpus-0001.bin", 0, 100, "printed", 0, "unassessed", "codh:b", None)])
    write_source(tmp_path, "media", ("key", "object", "offset", "size", "content_type"), [])
    write_source(tmp_path, "unit_ngrams", ("first", "size", "second", "third", "text", "document", "vertical", "hand_order",
                                           "shuffle", "graphemes"), [("ex:a1", 2, "ex:a2", None, "あい", "book:a", 1, 1, 0, "あい")])
    write_source(tmp_path, "pages", ("id", "document", "data"), [])
    write_source(tmp_path, "document_dating", ("document", "axis", "kind", "start", "end", "precision", "label", "status"),
                 [("book:a", "witness", "printed", 1700, 1700, "year", "1700", "single")])
    write_source(tmp_path, "characters", ("code_point", "character", "name", "head"), [])
    write_source(tmp_path, "aliases", ("query", "code_point", "rank"), [])
    for name in ("build", "publish"):
        sys.modules.pop(name, None)
    build = importlib.import_module("build")
    build.main()
    publish = importlib.import_module("publish")
    remote = FakeD1(tmp_path)
    monkeypatch.setattr(publish, "send", remote.send)
    monkeypatch.setattr(publish, "query", remote.query)
    monkeypatch.setattr(publish, "upload", remote.upload)
    return tmp_path, publish, remote


def run(publish, monkeypatch, path, *flags):
    monkeypatch.setattr(sys, "argv", ["publish.py", str(path), *flags])
    publish.main()


def crops(remote):
    db = remote.dbs["glyph-atlas-v3"]
    db.row_factory = sqlite3.Row
    return {r["id"]: dict(r) for r in db.execute("SELECT * FROM crops")}


def test_a_new_crop_takes_the_next_slot_of_its_page_and_keeps_it(layout, monkeypatch, tmp_path):
    root, publish, remote = layout
    path = catalogue(tmp_path / "c1.sqlite", [unit("ex:a3", "hl:p1", {"x": 0, "y": 40, "w": 10, "h": 10}, "え")])
    run(publish, monkeypatch, path)
    ids = dict(sqlite3.connect(root / "out" / "mirror.sqlite").execute("SELECT text, id FROM ids"))
    assert ids["ex:a3"] == ids["ex:a1"] + 2
    assert ids["ex:a3"] in crops(remote)
    run(publish, monkeypatch, catalogue(tmp_path / "c2.sqlite", [unit("ex:a3", "hl:p1", {"x": 0, "y": 40, "w": 10, "h": 10}, "え")]))
    assert dict(sqlite3.connect(root / "out" / "mirror.sqlite").execute("SELECT text, id FROM ids"))["ex:a3"] == ids["ex:a3"]


def test_an_unchanged_catalogue_sends_nothing_and_keeps_every_page_revision(layout, monkeypatch, tmp_path):
    _root, publish, remote = layout
    before = remote.query("glyph-atlas-v3", "SELECT id, rev FROM pages")
    run(publish, monkeypatch, catalogue(tmp_path / "same.sqlite", [unit("ex:a1", "hl:p1", {"x": 0, "y": 0, "w": 10, "h": 10}, "あ")]))
    assert remote.query("glyph-atlas-v3", "SELECT id, rev FROM pages") == before
    assert remote.uploaded == []


def test_a_review_made_since_the_mirror_read_is_kept_and_read_back(layout, monkeypatch, tmp_path):
    root, publish, remote = layout
    a1 = dict(sqlite3.connect(root / "out" / "mirror.sqlite").execute("SELECT text, id FROM ids"))["ex:a1"]
    remote.dbs["glyph-atlas-v3"].execute("UPDATE crops SET state=2, revision=1 WHERE id=?", (a1,))
    remote.dbs["glyph-atlas-v3"].commit()
    run(publish, monkeypatch, catalogue(tmp_path / "c.sqlite", [unit("ex:a1", "hl:p1", {"x": 0, "y": 0, "w": 10, "h": 10}, "あ",
                                                                     state="checked", revision=0)]))
    assert crops(remote)[a1]["state"] == 2
    local = sqlite3.connect(root / "out" / "atlas.sqlite").execute("SELECT state, revision FROM crops WHERE id=?", (a1,)).fetchone()
    assert local == (2, 1)


def test_a_newer_review_in_the_catalogue_is_applied_with_its_revision(layout, monkeypatch, tmp_path):
    root, publish, remote = layout
    a1 = dict(sqlite3.connect(root / "out" / "mirror.sqlite").execute("SELECT text, id FROM ids"))["ex:a1"]
    run(publish, monkeypatch, catalogue(tmp_path / "c.sqlite", [unit("ex:a1", "hl:p1", {"x": 0, "y": 0, "w": 10, "h": 10}, "あ",
                                                                     state="checked", revision=4)]))
    assert crops(remote)[a1]["state"] == 1 and crops(remote)[a1]["revision"] == 4


def test_a_publish_keeps_the_crops_and_fields_it_does_not_carry_in_the_page_bundle(layout, monkeypatch, tmp_path):
    root, publish, _remote = layout
    run(publish, monkeypatch, catalogue(tmp_path / "c.sqlite", [unit("ex:a1", "hl:p1", {"x": 0, "y": 0, "w": 10, "h": 10}, "お")]))
    pid = dict(sqlite3.connect(root / "out" / "mirror.sqlite").execute("SELECT text, id FROM ids"))["ex:a1"] // PAGE
    body = json.loads(gzip.decompress((root / "out" / "pages" / f"{pid}-2.json.gz").read_bytes()))
    assert [c["id"] for c in body["crops"]] == ["ex:a1", "ex:a2"]
    assert body["crops"][0]["label"] == "お"
    assert body["crops"][1]["label"] == "い"


def test_a_new_box_over_a_published_crop_stops_the_publish(layout, monkeypatch, tmp_path):
    root, publish, _remote = layout
    with pytest.raises(SystemExit):
        run(publish, monkeypatch, catalogue(tmp_path / "c.sqlite", [unit("ex:recut", "hl:p1", {"x": 1, "y": 1, "w": 10, "h": 10})]))
    assert "ex:recut" not in dict(sqlite3.connect(root / "out" / "mirror.sqlite").execute("SELECT text, id FROM ids"))


def test_a_known_crop_on_another_page_stops_the_publish(layout, monkeypatch, tmp_path):
    _root, publish, _remote = layout
    with pytest.raises(SystemExit):
        run(publish, monkeypatch, catalogue(tmp_path / "c.sqlite", [unit("ex:a1", "hl:p9", {"x": 0, "y": 0, "w": 10, "h": 10})]))


def test_a_dry_run_allocates_nothing(layout, monkeypatch, tmp_path):
    root, publish, remote = layout
    run(publish, monkeypatch, catalogue(tmp_path / "c.sqlite", [unit("ex:new", "hl:p3", {"x": 0, "y": 0, "w": 10, "h": 10})]), "--dry-run")
    assert "ex:new" not in dict(sqlite3.connect(root / "out" / "mirror.sqlite").execute("SELECT text, id FROM ids"))
    assert "ex:new" not in str(crops(remote))


def test_a_publish_stopped_after_allocating_is_finished_by_the_next_run(layout, monkeypatch, tmp_path):
    root, publish, remote = layout

    def fail(*args):
        raise SystemExit("network")

    monkeypatch.setattr(publish, "send", fail)
    with pytest.raises(SystemExit):
        run(publish, monkeypatch, catalogue(tmp_path / "c.sqlite", [unit("ex:new", "hl:p3", {"x": 0, "y": 0, "w": 10, "h": 10})]))
    new = dict(sqlite3.connect(root / "out" / "mirror.sqlite").execute("SELECT text, id FROM ids"))["ex:new"]
    monkeypatch.setattr(publish, "send", remote.send)
    run(publish, monkeypatch, catalogue(tmp_path / "other.sqlite", [unit("ex:other", "hl:p4", {"x": 0, "y": 0, "w": 10, "h": 10})]))
    assert new in crops(remote)
    ids = dict(sqlite3.connect(root / "out" / "mirror.sqlite").execute("SELECT text, id FROM ids"))
    assert len(set(ids.values())) == len(ids)


def test_runs_of_a_published_crop_follow_its_catalogue(layout, monkeypatch, tmp_path):
    _root, publish, remote = layout
    path = catalogue(tmp_path / "c.sqlite", [unit("ex:a1", "hl:p1", {"x": 0, "y": 0, "w": 10, "h": 10}, "あ"),
                                             unit("ex:a3", "hl:p1", {"x": 0, "y": 40, "w": 10, "h": 10}, "え")],
                     [("ex:a1", 2, "ex:a3", None, "あえ", "book:a", 0, 1, 0, "あえ")])
    run(publish, monkeypatch, path)
    runs = remote.query("glyph-atlas-v3-runs", "SELECT graphemes FROM runs")
    stats = {r["graphemes"]: r["n"] for r in remote.query("glyph-atlas-v3-runs", "SELECT graphemes, n FROM run_stats")}
    assert [r["graphemes"] for r in runs] == ["あえ"]
    assert stats == {"あえ": 1}


def test_a_retired_crop_leaves_the_galleries_and_its_runs_but_keeps_its_id(layout, monkeypatch, tmp_path):
    root, publish, remote = layout
    ids = dict(sqlite3.connect(root / "out" / "mirror.sqlite").execute("SELECT text, id FROM ids"))
    gone = unit("ex:a2", "hl:p1", {"x": 0, "y": 20, "w": 10, "h": 10})
    run(publish, monkeypatch, catalogue(tmp_path / "c.sqlite", [(gone[0], "retired", *gone[2:])]))
    assert ids["ex:a2"] not in crops(remote)
    assert remote.query("glyph-atlas-v3-runs", "SELECT * FROM runs") == []
    body = json.loads(gzip.decompress((root / "out" / "pages" / f"{ids['ex:a2'] // PAGE}-2.json.gz").read_bytes()))
    assert {"id": "ex:a2", "retired": True}.items() <= next(c for c in body["crops"] if c["id"] == "ex:a2").items()


def test_a_corpus_catalogue_keeps_the_review_of_a_corpus_glyph(layout, monkeypatch, tmp_path):
    root, publish, remote = layout
    g1 = dict(sqlite3.connect(root / "out" / "mirror.sqlite").execute("SELECT text, id FROM ids"))["codh:g1"]
    before = crops(remote)[g1]
    path = tmp_path / "corpus.sqlite"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE corpus_units(id,character,family,visual_group,shuffle,object,offset,size,production,named,style,document,label)")
    db.execute("INSERT INTO corpus_units VALUES('codh:g1','が',NULL,NULL,7,'corpus-0002.bin',0,120,'printed',0,'unassessed','codh:b',NULL)")
    db.commit()
    run(publish, monkeypatch, path)
    after = crops(remote)[g1]
    assert (after["char"], after["state"], after["quiz"], after["revision"]) == (before["char"], before["state"], before["quiz"], 2)
    body = json.loads(gzip.decompress((root / "out" / "pages" / f"{g1 // PAGE}-2.json.gz").read_bytes()))
    assert body["crops"][0]["record_place"] == ["corpus-0002.bin", 0, 120]
