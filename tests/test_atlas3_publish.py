import gzip
import importlib
import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "atlas3"))

PAGE = 1 << 24


def unit(id, page, box, character="あ", state="pending", revision=0, quiz=1, document="book:a", priority=1):
    data = {"page_id": page, "box": box, "image": f"/atlas/media/{id.replace(':', '')}.webp", "label": character}
    return (id, "local", character, None, None, "printed", "kana", state, revision, quiz, priority, 1, document, "unassessed", None,
            json.dumps(data))


UNITS = ("id", "origin", "character", "family", "visual_group", "production", "category", "state", "revision", "quiz", "priority",
         "shuffle", "document", "style", "written_form", "data")


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
        build = sqlite3.connect(root / "out" / "mirror.sqlite").execute("SELECT value FROM meta WHERE key='build'").fetchone()[0]
        self.dbs["glyph-atlas-v3"].execute("INSERT INTO imports VALUES(?, 0)", (f"build:{build}",))
        self.dbs["glyph-atlas-v3"].commit()
        self.uploaded = []

    def send(self, database, file, stamp):
        db = self.dbs[database]
        if not db.execute("SELECT 1 FROM imports WHERE stamp=?", (stamp,)).fetchone():
            db.executescript("BEGIN;\n" + Path(file).read_text() + "COMMIT;\n")
        return True

    def query(self, database, sql):
        db = self.dbs[database]
        db.row_factory = sqlite3.Row
        if ";" in sql.strip().rstrip(";"):
            # D1 runs a command of several statements in one transaction and answers with the last.
            db.executescript(f"BEGIN; {sql}; COMMIT;")
            return []
        return [dict(r) for r in db.execute(sql)]

    def upload(self, files, prefix="v3/pages/"):
        self.uploaded += [f.name for f in files]
        self.prefixes = getattr(self, "prefixes", []) + [prefix] * len(files)


@pytest.fixture
def layout(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS3_DIR", str(tmp_path))
    write_source(tmp_path, "units", UNITS, [
        unit("ex:a1", "hl:p1", {"x": 0, "y": 0, "w": 10, "h": 10}, "あ"),
        unit("ex:a2", "hl:p1", {"x": 0, "y": 20, "w": 10, "h": 10}, "い", state="checked", revision=3),
        unit("ex:b1", "hl:p2", {"x": 0, "y": 0, "w": 10, "h": 10}, "う"),
        ("codh:g1", "corpus", "か", None, None, "printed", "kana", "checked", 2, 0, 1, 1, "codh:b", "unassessed", None, "{}"),
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
    for name in ("build", "publish", "media_map"):
        sys.modules.pop(name, None)
    build = importlib.import_module("build")
    build.main()
    publish = importlib.import_module("publish")
    remote = FakeD1(tmp_path)
    monkeypatch.setattr(publish, "send", remote.send)
    monkeypatch.setattr(publish, "query", remote.query)
    monkeypatch.setattr(publish, "upload", remote.upload)
    # A publish ends by publishing its answers; test_atlas3_answers.py runs them.
    remote.answered = 0
    monkeypatch.setattr(publish, "answer", lambda: setattr(remote, "answered", remote.answered + 1))
    return tmp_path, publish, remote


def run(publish, monkeypatch, path, *flags):
    monkeypatch.setattr(sys, "argv", ["publish.py", str(path), *flags])
    publish.main()


def ids(root):
    return dict(sqlite3.connect(root / "out" / "mirror.sqlite").execute("SELECT text, id FROM ids"))


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
    assert [r["graphemes"] for r in remote.query("glyph-atlas-v3-runs", "SELECT graphemes FROM runs")] == ["あい"]
    assert remote.query("glyph-atlas-v3-runs", "SELECT graphemes, n FROM run_stats") == [{"graphemes": "あい", "n": 1}]


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


def test_a_page_bundle_carries_the_whole_record_but_what_the_crop_row_holds(layout, monkeypatch, tmp_path):
    root, publish, _remote = layout
    row = list(unit("ex:a1", "hl:p1", {"x": 0, "y": 0, "w": 10, "h": 10}, "お"))
    data = json.loads(row[-1])
    data.update(production_evidence=[{"source": "colophon"}], context=True, source_scale=[1, 1], state="pending")
    row[-1] = json.dumps(data)
    run(publish, monkeypatch, catalogue(tmp_path / "c.sqlite", [tuple(row)]))
    pid = dict(sqlite3.connect(root / "out" / "mirror.sqlite").execute("SELECT text, id FROM ids"))["ex:a1"] // PAGE
    entry = json.loads(gzip.decompress((root / "out" / "pages" / f"{pid}-2.json.gz").read_bytes()))["crops"][0]
    assert entry["production_evidence"] == [{"source": "colophon"}] and entry["context"] is True and entry["source_scale"] == [1, 1]
    assert "state" not in entry and "page_id" not in entry and entry["id"] == "ex:a1"


def test_a_new_record_replaces_the_old_and_keeps_an_unchanged_image_place(layout, monkeypatch, tmp_path):
    root, publish, _remote = layout
    pid = dict(sqlite3.connect(root / "out" / "mirror.sqlite").execute("SELECT text, id FROM ids"))["ex:a1"] // PAGE
    path = root / "out" / "pages" / f"{pid}-1.json.gz"
    body = json.loads(gzip.decompress(path.read_bytes()))
    body["crops"][0].update(repair={"by": "old"}, image_place=["packs/p.bin", 0, 9])
    path.write_bytes(gzip.compress(json.dumps(body).encode()))
    row = list(unit("ex:a1", "hl:p1", {"x": 0, "y": 0, "w": 10, "h": 10}, "お"))
    data = json.loads(row[-1])
    data.update(issue="crop", written_character="於", crop_editable=True)
    row[-1] = json.dumps(data)
    run(publish, monkeypatch, catalogue(tmp_path / "c.sqlite", [tuple(row)]))
    entry = json.loads(gzip.decompress((root / "out" / "pages" / f"{pid}-2.json.gz").read_bytes()))["crops"][0]
    assert "repair" not in entry and entry["image_place"] == ["packs/p.bin", 0, 9]
    assert not {"issue", "written_character", "crop_editable"} & set(entry)


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
    gone = unit("ex:a2", "hl:p1", {"x": 0, "y": 20, "w": 10, "h": 10}, revision=3)
    run(publish, monkeypatch, catalogue(tmp_path / "c.sqlite", [(gone[0], "retired", *gone[2:])]))
    assert ids["ex:a2"] not in crops(remote)
    assert remote.query("glyph-atlas-v3-runs", "SELECT * FROM runs") == []
    assert remote.query("glyph-atlas-v3-runs", "SELECT * FROM run_stats") == []
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


def test_a_retirement_older_than_a_review_is_refused(layout, monkeypatch, tmp_path):
    root, publish, remote = layout
    gone = unit("ex:a2", "hl:p1", {"x": 0, "y": 20, "w": 10, "h": 10}, revision=1)
    with pytest.raises(SystemExit):
        run(publish, monkeypatch, catalogue(tmp_path / "c.sqlite", [(gone[0], "retired", *gone[2:])]))
    ids = dict(sqlite3.connect(root / "out" / "mirror.sqlite").execute("SELECT text, id FROM ids"))
    assert ids["ex:a2"] in crops(remote)


def test_a_re_cut_onto_another_crops_glyph_stops_the_publish(layout, monkeypatch, tmp_path):
    _root, publish, _remote = layout
    with pytest.raises(SystemExit):
        run(publish, monkeypatch, catalogue(tmp_path / "c.sqlite", [unit("ex:a1", "hl:p1", {"x": 0, "y": 20, "w": 10, "h": 10})]))


def test_a_known_crop_with_another_box_is_re_cut_under_its_own_id(layout, monkeypatch, tmp_path):
    root, publish, remote = layout
    a2 = ids(root)["ex:a2"]
    before = crops(remote)[a2]
    run(publish, monkeypatch, catalogue(tmp_path / "c.sqlite", [unit("ex:a2", "hl:p1", {"x": 2, "y": 22, "w": 12, "h": 9}, "い",
                                                                     state="checked", revision=3)]))
    after = crops(remote)[a2]
    assert ids(root)["ex:a2"] == a2 and after["cut"] == before["cut"] + 1
    assert {k: after[k] for k in ("char", "state", "revision")} == {k: before[k] for k in ("char", "state", "revision")}
    entry = next(e for e in json.loads(gzip.decompress((root / "out" / "pages" / f"{a2 // PAGE}-2.json.gz").read_bytes()))["crops"]
                 if e["id"] == "ex:a2")
    assert entry["box"] == {"x": 2, "y": 22, "w": 12, "h": 9}


def test_a_re_cut_takes_a_marked_crop_back_to_pending_with_its_event(layout, monkeypatch, tmp_path):
    root, publish, remote = layout
    a1 = ids(root)["ex:a1"]
    # A round marked the crop seen at its first cut: its state is the mark, one revision on.
    for db in (remote.dbs["glyph-atlas-v3"], sqlite3.connect(root / "out" / "atlas.sqlite")):
        db.execute("UPDATE crops SET state=3, revision=1 WHERE id=?", (a1,))
        db.execute("INSERT INTO reviews(crop, gen, at, char, state) SELECT id, 0, 0, char, 3 FROM crops WHERE id=?", (a1,))
        db.commit()
    run(publish, monkeypatch, catalogue(tmp_path / "c.sqlite", [unit("ex:a1", "hl:p1", {"x": 1, "y": 1, "w": 9, "h": 9})]))
    after = crops(remote)[a1]
    assert (after["cut"], after["state"], after["revision"]) == (1, 0, 2)
    db = remote.dbs["glyph-atlas-v3"]
    assert [tuple(r) for r in db.execute("SELECT crop, revision, cut, actor, submission, kind, state FROM events")] == [(a1, 2, 1, 0, 0, 7, 0)]
    assert [tuple(r) for r in db.execute("SELECT state FROM reviews")] == [(0,)]
    local = sqlite3.connect(root / "out" / "atlas.sqlite")
    assert local.execute("SELECT state, revision FROM crops WHERE id=?", (a1,)).fetchone() == (0, 2)
    # A crop that is not marked keeps its state through a re-cut.
    run(publish, monkeypatch, catalogue(tmp_path / "d.sqlite", [unit("ex:a1", "hl:p1", {"x": 2, "y": 2, "w": 8, "h": 8})]))
    assert db.execute("SELECT count(*) FROM events").fetchone()[0] == 1


def test_fields_a_catalogue_does_not_carry_keep_their_values(layout, monkeypatch, tmp_path):
    root, publish, remote = layout
    a1 = dict(sqlite3.connect(root / "out" / "mirror.sqlite").execute("SELECT text, id FROM ids"))["ex:a1"]
    for db in (remote.dbs["glyph-atlas-v3"], sqlite3.connect(root / "out" / "atlas.sqlite")):
        db.execute("UPDATE crops SET form=7, cluster=8, cut=3, flags=4 WHERE id=?", (a1,))
        db.commit()
    run(publish, monkeypatch, catalogue(tmp_path / "c.sqlite", [unit("ex:a1", "hl:p1", {"x": 0, "y": 0, "w": 10, "h": 10}, "お")]))
    c = crops(remote)[a1]
    assert (c["form"], c["cluster"], c["cut"], c["flags"]) == (7, 8, 3, 4)


def test_a_corpus_overlay_sets_the_reviewed_style(layout, monkeypatch, tmp_path):
    root, publish, remote = layout
    g1 = dict(sqlite3.connect(root / "out" / "mirror.sqlite").execute("SELECT text, id FROM ids"))["codh:g1"]
    path = tmp_path / "corpus.sqlite"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE corpus_units(id,character,family,visual_group,shuffle,object,offset,size,production,named,style,document,label)")
    db.execute("INSERT INTO corpus_units VALUES('codh:g1','が',NULL,NULL,7,'corpus-0001.bin',0,100,'printed',0,'unassessed','codh:b',NULL)")
    db.execute(f"CREATE TABLE units({','.join(UNITS)})")
    db.execute(f"INSERT INTO units VALUES({','.join('?' * len(UNITS))})",
               ("codh:g1", "corpus", "か", None, None, "printed", "kana", "checked", 5, 0, 1, 1, "codh:b", "cursive", None, "{}"))
    db.commit()
    run(publish, monkeypatch, path)
    styles = dict(sqlite3.connect(root / "out" / "mirror.sqlite").execute("SELECT key, code FROM codes WHERE field='style'"))
    assert crops(remote)[g1]["style"] == styles["cursive"]
    assert crops(remote)[g1]["revision"] == 5


def test_a_change_of_page_facts_alone_moves_the_page_revision(layout, monkeypatch, tmp_path):
    root, publish, remote = layout
    path = catalogue(tmp_path / "c.sqlite", [])
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE pages(id, document, data)")
    db.execute("INSERT INTO pages VALUES('hl:p2', 'book:a', ?)", (json.dumps({"iiif": "https://example.org/p2"}),))
    db.commit()
    run(publish, monkeypatch, path)
    pid = dict(sqlite3.connect(root / "out" / "mirror.sqlite").execute("SELECT key, id FROM pages"))["hl:p2"]
    assert remote.query("glyph-atlas-v3", f"SELECT rev FROM pages WHERE id={pid}") == [{"rev": 2}]
    body = json.loads(gzip.decompress((root / "out" / "pages" / f"{pid}-2.json.gz").read_bytes()))
    assert body["page"] == {"iiif": "https://example.org/p2"} and [c["id"] for c in body["crops"]] == ["ex:b1"]


def test_a_publish_stopped_before_its_bundles_reached_r2_is_finished_by_the_next_run(layout, monkeypatch, tmp_path):
    root, publish, remote = layout

    def fail(files):
        raise SystemExit("network")

    monkeypatch.setattr(publish, "upload", fail)
    with pytest.raises(SystemExit):
        run(publish, monkeypatch, catalogue(tmp_path / "c.sqlite", [unit("ex:new", "hl:p5", {"x": 0, "y": 0, "w": 10, "h": 10})]))
    monkeypatch.setattr(publish, "upload", remote.upload)
    run(publish, monkeypatch, catalogue(tmp_path / "other.sqlite", [unit("ex:other", "hl:p6", {"x": 0, "y": 0, "w": 10, "h": 10})]))
    ids = dict(sqlite3.connect(root / "out" / "mirror.sqlite").execute("SELECT text, id FROM ids"))
    assert ids["ex:new"] in crops(remote) and ids["ex:other"] in crops(remote)
    assert sqlite3.connect(root / "out" / "mirror.sqlite").execute("SELECT count(*) FROM pending").fetchone() == (0,)


def test_a_page_never_splits_across_parts(layout, monkeypatch, tmp_path):
    root, publish, remote = layout
    monkeypatch.setattr(publish, "PART_BYTES", 1)
    new = [unit(f"ex:n{i}", f"hl:q{i % 3}", {"x": 0, "y": 20 * i, "w": 10, "h": 10}) for i in range(9)]
    run(publish, monkeypatch, catalogue(tmp_path / "c.sqlite", new))
    run_dir = max((root / "out" / "publish").iterdir())
    parts = sorted(run_dir.glob("atlas-*.sql"))
    assert len(parts) > 1
    for part in parts:
        text = part.read_text()
        for pid in {int(x.split(",")[0]) // (1 << 24) for x in text.split("INSERT INTO crops(")[1:] for x in [x.split("VALUES(")[1]]}:
            assert f"UPDATE pages SET rev=1, next_slot=max(next_slot,3) WHERE id={pid};" in text
    assert len(crops(remote)) == 4 + 9


def test_a_new_corpus_glyph_stays_in_its_own_book(layout, monkeypatch, tmp_path):
    root, publish, _remote = layout
    path = tmp_path / "corpus.sqlite"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE corpus_units(id,character,family,visual_group,shuffle,object,offset,size,production,named,style,document,label)")
    db.execute("INSERT INTO corpus_units VALUES('codh:x1','き',NULL,NULL,7,'corpus-0003.bin',0,100,'printed',0,'unassessed','codh:b:x',NULL)")
    db.commit()
    run(publish, monkeypatch, path)
    pages = dict(sqlite3.connect(root / "out" / "mirror.sqlite").execute("SELECT id, key FROM pages"))
    assert pages[ids(root)["codh:x1"] // PAGE] == "corpus:codh:codh:b:x:0"


def test_two_new_boxes_on_one_glyph_stop_the_publish(layout, monkeypatch, tmp_path):
    root, publish, _remote = layout
    with pytest.raises(SystemExit):
        run(publish, monkeypatch, catalogue(tmp_path / "c.sqlite", [unit("ex:d1", "hl:p7", {"x": 0, "y": 0, "w": 10, "h": 10}),
                                                                   unit("ex:d2", "hl:p7", {"x": 1, "y": 0, "w": 10, "h": 10})]))
    assert "ex:d1" not in ids(root)


def test_a_review_newer_on_the_site_than_in_the_catalogue_is_reported(layout, monkeypatch, tmp_path, capsys):
    root, publish, remote = layout
    a1 = ids(root)["ex:a1"]
    for db in (remote.dbs["glyph-atlas-v3"], sqlite3.connect(root / "out" / "atlas.sqlite")):
        db.execute("UPDATE crops SET state=2, revision=5 WHERE id=?", (a1,))
        db.commit()
    run(publish, monkeypatch, catalogue(tmp_path / "c.sqlite", [unit("ex:a1", "hl:p1", {"x": 0, "y": 0, "w": 10, "h": 10},
                                                                     state="checked", revision=1)]))
    assert "1 crops have a newer review on the site" in capsys.readouterr().out
    assert crops(remote)[a1]["state"] == 2


def test_a_mirror_from_another_build_is_refused(layout, monkeypatch, tmp_path):
    root, publish, _remote = layout
    mirror = sqlite3.connect(root / "out" / "mirror.sqlite")
    mirror.execute("UPDATE meta SET value='other' WHERE key='build'")
    mirror.commit()
    with pytest.raises(SystemExit):
        run(publish, monkeypatch, catalogue(tmp_path / "c.sqlite", [unit("ex:z", "hl:p8", {"x": 0, "y": 0, "w": 10, "h": 10})]))


def test_a_crop_with_no_style_is_published_as_unassessed(layout, monkeypatch, tmp_path):
    root, publish, remote = layout
    row = list(unit("ex:new", "hl:p3", {"x": 0, "y": 0, "w": 10, "h": 10}))
    row[UNITS.index("style")] = None
    run(publish, monkeypatch, catalogue(tmp_path / "c.sqlite", [tuple(row)]))
    unassessed = remote.query("glyph-atlas-v3", "SELECT code FROM codes WHERE field='style' AND key='unassessed'")[0]["code"]
    assert crops(remote)[ids(root)["ex:new"]]["style"] == unassessed


def test_a_build_takes_every_migration_of_the_new_layout(layout):
    root, _publish, _remote = layout
    names = {r[0] for r in sqlite3.connect(root / "out" / "atlas.sqlite").execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert {"crop_char", "crop_undated"} <= names


def test_a_crop_retired_before_the_new_layout_gets_a_number_and_a_retired_entry(layout, monkeypatch, tmp_path):
    root, publish, remote = layout
    row = list(unit("ex:old", "hl:p1", {"x": 40, "y": 40, "w": 10, "h": 10}))
    row[UNITS.index("origin")] = "retired"
    path = catalogue(tmp_path / "c.sqlite", [tuple(row)])
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE unit_redirects(id, target)")
    db.execute("INSERT INTO unit_redirects VALUES('ex:old', 'ex:a1')")
    db.commit()
    run(publish, monkeypatch, path)
    old = ids(root)["ex:old"]
    assert old // PAGE == ids(root)["ex:a1"] // PAGE and old not in crops(remote)
    entry = next(e for e in json.loads(gzip.decompress((root / "out" / "pages" / f"{old // PAGE}-2.json.gz").read_bytes()))["crops"]
                 if e["id"] == "ex:old")
    assert entry["retired"] is True and entry["replaced_by"] == ids(root)["ex:a1"] and entry["box"] == {"x": 40, "y": 40, "w": 10, "h": 10}


def test_a_crop_retired_later_names_its_replacement(layout, monkeypatch, tmp_path):
    root, publish, _remote = layout
    gone = list(unit("ex:b1", "hl:p2", {"x": 0, "y": 0, "w": 10, "h": 10}, "う"))
    gone[UNITS.index("origin")] = "retired"
    run(publish, monkeypatch, catalogue(tmp_path / "gone.sqlite", [tuple(gone)]))
    path = catalogue(tmp_path / "named.sqlite", [tuple(gone)])
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE unit_redirects(id, target)")
    db.execute("INSERT INTO unit_redirects VALUES('ex:b1', 'ex:a1')")
    db.commit()
    run(publish, monkeypatch, path)
    b1 = ids(root)["ex:b1"]
    entry = next(e for e in json.loads(gzip.decompress((root / "out" / "pages" / f"{b1 // PAGE}-3.json.gz").read_bytes()))["crops"]
                 if e["id"] == "ex:b1")
    assert entry["retired"] is True and entry["replaced_by"] == ids(root)["ex:a1"]


def retired_catalogue(path, units, redirects):
    path = catalogue(path, units)
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE unit_redirects(id, target)")
    db.executemany("INSERT INTO unit_redirects VALUES(?, ?)", redirects)
    db.commit()
    return path


def retired_unit(id, page, box, character="う"):
    row = list(unit(id, page, box, character))
    row[UNITS.index("origin")] = "retired"
    return tuple(row)


def bundle_entry_of(root, text_id):
    cid = ids(root)[text_id]
    rev = sqlite3.connect(root / "out" / "atlas.sqlite").execute("SELECT rev FROM pages WHERE id=?", (cid // PAGE,)).fetchone()[0]
    path = root / "out" / "pages" / f"{cid // PAGE}-{rev}.json.gz"
    return next(e for e in json.loads(gzip.decompress(path.read_bytes()))["crops"] if e["id"] == text_id), path


def test_a_crop_retired_for_a_crop_new_in_the_same_catalogue_names_its_number(layout, monkeypatch, tmp_path):
    root, publish, _remote = layout
    path = retired_catalogue(tmp_path / "c.sqlite", [
        retired_unit("ex:b1", "hl:p2", {"x": 0, "y": 0, "w": 10, "h": 10}),
        unit("ex:z9", "hl:p9", {"x": 0, "y": 0, "w": 10, "h": 10})], [("ex:b1", "ex:z9")])
    run(publish, monkeypatch, path)
    entry, _ = bundle_entry_of(root, "ex:b1")
    assert entry["replaced_by"] == ids(root)["ex:z9"]


def test_a_replacement_is_found_along_the_redirect_chain(layout, monkeypatch, tmp_path):
    root, publish, _remote = layout
    path = retired_catalogue(tmp_path / "c.sqlite", [retired_unit("ex:b1", "hl:p2", {"x": 0, "y": 0, "w": 10, "h": 10})],
                             [("ex:b1", "ex:gone"), ("ex:gone", "ex:a1")])
    run(publish, monkeypatch, path)
    entry, _ = bundle_entry_of(root, "ex:b1")
    assert entry["replaced_by"] == ids(root)["ex:a1"]


def test_a_replacement_passes_a_retired_crop_on_the_chain(layout, monkeypatch, tmp_path):
    root, publish, _remote = layout
    run(publish, monkeypatch, retired_catalogue(tmp_path / "c1.sqlite", [retired_unit("ex:b1", "hl:p2", {"x": 0, "y": 0, "w": 10, "h": 10})], []))
    path = retired_catalogue(tmp_path / "c2.sqlite", [retired_unit("ex:old", "hl:p2", {"x": 40, "y": 40, "w": 10, "h": 10})],
                             [("ex:old", "ex:b1"), ("ex:b1", "ex:a1")])
    run(publish, monkeypatch, path)
    entry, _ = bundle_entry_of(root, "ex:old")
    assert entry["replaced_by"] == ids(root)["ex:a1"]


@pytest.mark.parametrize("redirects", [[("ex:b1", "ex:nowhere")], [("ex:b1", "ex:x"), ("ex:x", "ex:y"), ("ex:y", "ex:x")]])
def test_a_chain_with_no_live_crop_leaves_the_replacement_unset(layout, monkeypatch, tmp_path, capsys, redirects):
    root, publish, remote = layout
    path = retired_catalogue(tmp_path / "c.sqlite", [retired_unit("ex:b1", "hl:p2", {"x": 0, "y": 0, "w": 10, "h": 10})], redirects)
    run(publish, monkeypatch, path)
    entry, _ = bundle_entry_of(root, "ex:b1")
    assert entry["retired"] is True and "replaced_by" not in entry and ids(root)["ex:b1"] not in crops(remote)
    assert "1 retired crops have no live crop on their redirect chain" in capsys.readouterr().out


def test_a_replacement_whose_chain_lost_its_live_crop_is_removed(layout, monkeypatch, tmp_path):
    root, publish, _remote = layout
    run(publish, monkeypatch, retired_catalogue(tmp_path / "c1.sqlite", [retired_unit("ex:b1", "hl:p2", {"x": 0, "y": 0, "w": 10, "h": 10})],
                                                [("ex:b1", "ex:a1")]))
    run(publish, monkeypatch, retired_catalogue(tmp_path / "c2.sqlite", [retired_unit("ex:b1", "hl:p2", {"x": 0, "y": 0, "w": 10, "h": 10})],
                                                [("ex:b1", "ex:nowhere")]))
    entry, _ = bundle_entry_of(root, "ex:b1")
    assert entry["retired"] is True and "replaced_by" not in entry


def test_the_replacements_pass_names_a_retired_crop_no_catalogue_carries(layout, monkeypatch, tmp_path):
    root, publish, remote = layout
    run(publish, monkeypatch, retired_catalogue(tmp_path / "c.sqlite", [retired_unit("ex:b1", "hl:p2", {"x": 0, "y": 0, "w": 10, "h": 10})], []))
    assert "replaced_by" not in bundle_entry_of(root, "ex:b1")[0]
    write_source(root, "unit_redirects", ("id", "target"), [("ex:b1", "ex:gone"), ("ex:gone", "ex:a1"), ("ex:a2", "ex:a1")])
    sent = len(remote.uploaded)
    monkeypatch.setattr(sys, "argv", ["publish.py", "--replacements"])
    publish.main()
    entry, _ = bundle_entry_of(root, "ex:b1")
    assert entry["replaced_by"] == ids(root)["ex:a1"] and len(remote.uploaded) == sent + 1
    publish.main()
    assert len(remote.uploaded) == sent + 1


def test_a_replacement_held_as_a_text_id_is_rewritten_as_a_number(layout, monkeypatch, tmp_path):
    root, publish, remote = layout
    path = retired_catalogue(tmp_path / "c.sqlite", [retired_unit("ex:b1", "hl:p2", {"x": 0, "y": 0, "w": 10, "h": 10})],
                             [("ex:b1", "ex:a1")])
    run(publish, monkeypatch, path)
    entry, bundle = bundle_entry_of(root, "ex:b1")
    body = json.loads(gzip.decompress(bundle.read_bytes()))
    for e in body["crops"]:
        if e["id"] == "ex:b1":
            e["replaced_by"] = "ex:a1"
    bundle.write_bytes(gzip.compress(json.dumps(body).encode()))
    sent = len(remote.uploaded)
    run(publish, monkeypatch, path)
    entry, again = bundle_entry_of(root, "ex:b1")
    assert again != bundle and entry["replaced_by"] == ids(root)["ex:a1"] and len(remote.uploaded) == sent + 1
    run(publish, monkeypatch, path)
    assert len(remote.uploaded) == sent + 1


def test_two_crops_trading_glyphs_stop_the_publish(layout, monkeypatch, tmp_path):
    _root, publish, remote = layout
    before = crops(remote)
    with pytest.raises(SystemExit):
        run(publish, monkeypatch, catalogue(tmp_path / "c.sqlite", [unit("ex:a1", "hl:p1", {"x": 0, "y": 20, "w": 10, "h": 10}, "あ"),
                                                                    unit("ex:a2", "hl:p1", {"x": 0, "y": 0, "w": 10, "h": 10}, "い",
                                                                         state="checked", revision=3)]))
    assert crops(remote) == before


def test_a_retired_crop_put_back_with_another_box_is_published_again(layout, monkeypatch, tmp_path):
    root, publish, remote = layout
    gone = list(unit("ex:b1", "hl:p2", {"x": 0, "y": 0, "w": 10, "h": 10}, "う"))
    gone[UNITS.index("origin")] = "retired"
    run(publish, monkeypatch, catalogue(tmp_path / "gone.sqlite", [tuple(gone)]))
    assert ids(root)["ex:b1"] not in crops(remote)
    run(publish, monkeypatch, catalogue(tmp_path / "back.sqlite", [unit("ex:b1", "hl:p2", {"x": 3, "y": 3, "w": 10, "h": 10}, "う")]))
    assert ids(root)["ex:b1"] in crops(remote)


def test_a_re_cut_keeps_the_version_it_replaced_among_the_crops_cuts(layout, monkeypatch, tmp_path):
    root, publish, _remote = layout
    a2 = ids(root)["ex:a2"]
    run(publish, monkeypatch, catalogue(tmp_path / "c1.sqlite", [unit("ex:a2", "hl:p1", {"x": 2, "y": 22, "w": 12, "h": 9}, "い",
                                                                      state="checked", revision=3)]))
    run(publish, monkeypatch, catalogue(tmp_path / "c2.sqlite", [unit("ex:a2", "hl:p1", {"x": 3, "y": 23, "w": 12, "h": 9}, "い",
                                                                      state="checked", revision=3)]))
    # A later record with the same box carries the cuts along.
    run(publish, monkeypatch, catalogue(tmp_path / "c3.sqlite", [unit("ex:a2", "hl:p1", {"x": 3, "y": 23, "w": 12, "h": 9}, "う",
                                                                      state="checked", revision=3)]))
    rev = sqlite3.connect(root / "out" / "atlas.sqlite").execute("SELECT rev FROM pages WHERE id=?", (a2 // PAGE,)).fetchone()[0]
    entry = next(e for e in json.loads(gzip.decompress((root / "out" / "pages" / f"{a2 // PAGE}-{rev}.json.gz").read_bytes()))["crops"]
                 if e["id"] == "ex:a2")
    assert [(c["cut"], c["box"]) for c in entry["cuts"]] == [(0, {"x": 0, "y": 20, "w": 10, "h": 10}),
                                                            (1, {"x": 2, "y": 22, "w": 12, "h": 9})]
    assert entry["box"] == {"x": 3, "y": 23, "w": 12, "h": 9}


def test_a_crop_put_back_after_retirement_continues_from_its_cut(layout, monkeypatch, tmp_path):
    root, publish, remote = layout
    a2 = ids(root)["ex:a2"]
    box_b, box_c = {"x": 2, "y": 22, "w": 12, "h": 9}, {"x": 4, "y": 24, "w": 12, "h": 9}
    run(publish, monkeypatch, catalogue(tmp_path / "b.sqlite", [unit("ex:a2", "hl:p1", box_b, "い", state="checked", revision=3)]))
    gone = list(unit("ex:a2", "hl:p1", box_b, "い", state="checked", revision=3))
    gone[UNITS.index("origin")] = "retired"
    run(publish, monkeypatch, catalogue(tmp_path / "gone.sqlite", [tuple(gone)]))
    run(publish, monkeypatch, catalogue(tmp_path / "c.sqlite", [unit("ex:a2", "hl:p1", box_c, "い", state="checked", revision=3)]))
    assert crops(remote)[a2]["cut"] == 2
    rev = sqlite3.connect(root / "out" / "atlas.sqlite").execute("SELECT rev FROM pages WHERE id=?", (a2 // PAGE,)).fetchone()[0]
    entry = next(e for e in json.loads(gzip.decompress((root / "out" / "pages" / f"{a2 // PAGE}-{rev}.json.gz").read_bytes()))["crops"]
                 if e["id"] == "ex:a2")
    assert [(c["cut"], c["box"]) for c in entry["cuts"]] == [(0, {"x": 0, "y": 20, "w": 10, "h": 10}), (1, box_b)]
    assert entry["box"] == box_c and not entry.get("retired")


def test_a_publish_needing_a_statement_over_d1s_limit_allocates_nothing(layout, monkeypatch, tmp_path):
    root, publish, _remote = layout
    row = list(unit("ex:new", "hl:p3", {"x": 0, "y": 0, "w": 10, "h": 10}))
    row[UNITS.index("visual_group")] = "x" * 100_000
    db = sqlite3.connect(tmp_path / "c.sqlite")
    db.execute(f"CREATE TABLE units({','.join(UNITS)})")
    db.execute(f"INSERT INTO units VALUES({','.join('?' * len(UNITS))})", row)
    db.commit()
    with pytest.raises(SystemExit, match="over D1's limit"):
        run(publish, monkeypatch, tmp_path / "c.sqlite")
    assert "ex:new" not in ids(root)


def corpus_catalogue(path, rows):
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE corpus_units(id,character,family,visual_group,shuffle,object,offset,size,production,named,style,document,label)")
    db.executemany("INSERT INTO corpus_units VALUES(?,?,?,NULL,7,'corpus-0003.bin',0,100,'printed',0,'unassessed','codh:b',?)", rows)
    db.commit()
    return path


def test_a_glyph_is_filed_under_the_family_its_source_names(layout, monkeypatch, tmp_path):
    root, publish, remote = layout
    run(publish, monkeypatch, corpus_catalogue(tmp_path / "corpus.sqlite",
                                               [("codh:m1", None, "U+3082", "も"), ("codh:m2", "𛄝", "U+3082", "も")]))
    graphemes = dict(remote.dbs["glyph-atlas-v3"].execute("SELECT text, id FROM graphemes"))
    found = crops(remote)
    assert (found[ids(root)["codh:m1"]]["char"], found[ids(root)["codh:m1"]]["family"]) == (None, graphemes["も"])
    # A glyph with a character keeps its source's family too: it joins its character's family, and the
    # family it is filed under should a review clear its character.
    assert (found[ids(root)["codh:m2"]]["char"], found[ids(root)["codh:m2"]]["family"]) == (graphemes["𛄝"], graphemes["も"])


def test_a_build_files_a_glyph_with_no_character_under_its_family(layout, monkeypatch, tmp_path):
    root, _publish, _remote = layout
    (tmp_path / "source-corpus_units.sqlite").unlink()
    write_source(tmp_path, "corpus_units", ("id", "character", "family", "visual_group", "shuffle", "object", "offset",
                                            "size", "production", "named", "style", "document", "label"),
                 [("codh:m1", None, "U+3082", None, 7, "corpus-0001.bin", 0, 100, "printed", 0, "unassessed", "codh:b", "も")])
    sys.modules.pop("build", None)
    monkeypatch.setattr(sys, "argv", ["build.py", "--replace"])
    importlib.import_module("build").main()
    atlas = sqlite3.connect(root / "out" / "atlas.sqlite")
    family, char = atlas.execute("SELECT family, char FROM crops WHERE kind=1").fetchone()
    assert char is None and atlas.execute("SELECT text FROM graphemes WHERE id=?", (family,)).fetchone()[0] == "も"


def test_a_layout_without_the_family_column_is_refused_by_a_publish(layout, monkeypatch, tmp_path):
    root, publish, _remote = layout
    local = sqlite3.connect(root / "out" / "atlas.sqlite")
    local.executescript("".join(f"DROP INDEX {name};" for (name,) in local.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND sql LIKE '%family%'").fetchall()) + "ALTER TABLE crops DROP COLUMN family;")
    local.close()
    with pytest.raises(SystemExit, match="fill.py"):
        run(publish, monkeypatch, corpus_catalogue(tmp_path / "corpus.sqlite", [("codh:m1", None, "U+3082", "も")]))


def test_a_publish_that_sent_something_publishes_the_answers_after(layout, monkeypatch, tmp_path):
    _root, publish, remote = layout
    run(publish, monkeypatch, catalogue(tmp_path / "dry.sqlite", [unit("ex:a1", "hl:p1", {"x": 0, "y": 0, "w": 10, "h": 10}, "お")]), "--dry-run")
    run(publish, monkeypatch, catalogue(tmp_path / "same.sqlite", [unit("ex:a1", "hl:p1", {"x": 0, "y": 0, "w": 10, "h": 10}, "あ")]))
    assert remote.answered == 0
    run(publish, monkeypatch, catalogue(tmp_path / "c.sqlite", [unit("ex:a1", "hl:p1", {"x": 0, "y": 0, "w": 10, "h": 10}, "お")]))
    assert remote.answered == 1


def entry_of(root, cid):
    rev = sqlite3.connect(root / "out" / "atlas.sqlite").execute("SELECT rev FROM pages WHERE id=?", (cid // PAGE,)).fetchone()[0]
    return next(e for e in json.loads(gzip.decompress((root / "out" / "pages" / f"{cid // PAGE}-{rev}.json.gz").read_bytes()))["crops"]
                if e["slot"] == cid % PAGE)


def test_a_cut_keeps_when_it_was_recorded_and_hands_it_to_its_earlier_cut_on_a_re_cut(layout, monkeypatch, tmp_path):
    root, publish, _remote = layout
    a2, g1 = ids(root)["ex:a2"], ids(root)["codh:g1"]
    encodings = [{"pixels": "r0", "image": "/atlas/media/g.webp", "at": "2026-09-26T10:58:08.863Z"}]
    # The history load's facts: set on entries this catalogue does not otherwise publish, a corpus glyph's too.
    path = tmp_path / "facts.sqlite"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE cut_facts(id TEXT PRIMARY KEY, cut_at TEXT, encodings TEXT NOT NULL)")
    db.executemany("INSERT INTO cut_facts VALUES(?,?,?)", [("ex:a2", "2026-10-03T00:00:00.000Z", "[]"),
                                                          ("codh:g1", "2026-10-13T16:53:07.985Z", json.dumps(encodings))])
    db.commit()
    run(publish, monkeypatch, path)
    assert entry_of(root, a2)["cut_at"] == "2026-10-03T00:00:00.000Z" and "encodings" not in entry_of(root, a2)
    assert entry_of(root, g1)["cut_at"] == "2026-10-13T16:53:07.985Z" and entry_of(root, g1)["encodings"] == encodings
    assert "record_place" in entry_of(root, g1)
    # A record with the same box carries both along.
    run(publish, monkeypatch, catalogue(tmp_path / "same.sqlite", [unit("ex:a2", "hl:p1", {"x": 0, "y": 20, "w": 10, "h": 10}, "う",
                                                                        state="checked", revision=3)]))
    assert entry_of(root, a2)["cut_at"] == "2026-10-03T00:00:00.000Z"
    # A re-cut gives the cut it replaced that time, and its own cut the time of the publish.
    run(publish, monkeypatch, catalogue(tmp_path / "recut.sqlite", [unit("ex:a2", "hl:p1", {"x": 2, "y": 22, "w": 12, "h": 9}, "う",
                                                                         state="checked", revision=3)]))
    e = entry_of(root, a2)
    assert [(c["cut"], c.get("at")) for c in e["cuts"]] == [(0, "2026-10-03T00:00:00.000Z")]
    assert e["cut_at"] > "2026-10-03" and e["cut_at"].endswith("Z")
