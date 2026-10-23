import gc
import gzip
import importlib
import json
import re
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "atlas3"))

PAGE = 1 << 24


# Record packs named by their content (`pack_names`).
PACK = {n: f"packs/{str(n) * 64}.bin" for n in (1, 2, 3)}

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
        if ";" in sql.strip().rstrip(";"):
            # D1 runs a command of several statements in one transaction and answers with the last.
            db.executescript(f"BEGIN; {sql}; COMMIT;")
            return []
        # Rows as dicts for this read alone, so a test reading the database itself gets tuples.
        cursor = db.cursor()
        cursor.row_factory = sqlite3.Row
        return [dict(r) for r in cursor.execute(sql)]

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
                 [("codh:g1", "が", None, None, 7, PACK[1], 0, 100, "printed", 0, "unassessed", "codh:b", None)])
    write_source(tmp_path, "media", ("key", "object", "offset", "size", "content_type"), [])
    write_source(tmp_path, "unit_ngrams", ("first", "size", "second", "third", "text", "document", "vertical", "hand_order",
                                           "shuffle", "graphemes"), [("ex:a1", 2, "ex:a2", None, "あい", "book:a", 1, 1, 0, "あい")])
    write_source(tmp_path, "pages", ("id", "document", "data"), [])
    write_source(tmp_path, "document_dating", ("document", "axis", "kind", "start", "end", "precision", "label", "status"),
                 [("book:a", "witness", "printed", 1700, 1700, "year", "1700", "single")])
    write_source(tmp_path, "characters", ("code_point", "character", "name", "head"), [])
    write_source(tmp_path, "aliases", ("query", "code_point", "rank"), [])
    for name in ("build", "publish", "media_map", "run_answers"):
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
    # The crop's revision moves on by one; the store's revision is recorded beside it.
    assert crops(remote)[a1]["state"] == 1 and crops(remote)[a1]["revision"] == 1
    # A change a reviewer sees: a publication event, the boundary, at the crop's new revision, with the
    # store's revision recorded there.
    assert remote.query("glyph-atlas-v3", "SELECT revision, at FROM review_revisions WHERE crop=" + str(a1)) == [{"revision": 4, "at": 1}]
    # It names what it set (publish.SET_BITS): the state, as a character it set to none would be named too.
    assert remote.query("glyph-atlas-v3", f"SELECT revision, kind, published FROM events WHERE crop={a1}") == [
        {"revision": 1, "kind": 8, "published": publish.SET_BITS["state"]}]


def test_a_publication_speaks_against_the_revision_the_store_counts_not_the_marks(layout, monkeypatch, tmp_path):
    root, publish, remote = layout
    a1 = ids(root)["ex:a1"]
    # On the site: a seen mark (revision 1), then a review (revision 2). The store imported the review at 2.
    for db in (remote.dbs["glyph-atlas-v3"], sqlite3.connect(root / "out" / "atlas.sqlite")):
        db.executemany("INSERT INTO events(crop, revision, cut, gen, actor, submission, at, kind) VALUES(?, ?, 0, 0, 1, 1, 1, ?)",
                       [(a1, 1, 3), (a1, 2, 1)])
        db.execute("UPDATE crops SET revision=2 WHERE id=?", (a1,))
        db.commit()
    run(publish, monkeypatch, catalogue(tmp_path / "c.sqlite", [unit("ex:a1", "hl:p1", {"x": 0, "y": 0, "w": 10, "h": 10}, "あ",
                                                                     revision=2)]))
    logical = f"SELECT {publish.LOGICAL} AS n FROM crops c WHERE c.id={a1}"
    assert remote.query("glyph-atlas-v3", logical) == [{"n": 2}]
    assert sqlite3.connect(root / "out" / "atlas.sqlite").execute(logical).fetchone() == (2,)


def events(root, remote, crop, *rows, state=None):
    """Events on the site and in the local copy, (revision, kind), the crop at the last revision."""
    for db in (remote.dbs["glyph-atlas-v3"], sqlite3.connect(root / "out" / "atlas.sqlite")):
        db.executemany("INSERT INTO events(crop, revision, cut, gen, actor, submission, at, kind) VALUES(?, ?, 0, 0, 1, 1, 1, ?)",
                       [(crop, revision, kind) for revision, kind in rows])
        db.execute("UPDATE crops SET revision=?, state=coalesce(?, state) WHERE id=?", (rows[-1][0], state, crop))
        db.commit()


def logical(publish, db_query, crop):
    return db_query(f"SELECT {publish.LOGICAL} AS n FROM crops c WHERE c.id={crop}")


def test_a_publication_that_changes_nothing_a_reviewer_sees_anchors_at_the_crops_own_revision(layout, monkeypatch, tmp_path):
    root, publish, remote = layout
    a1 = ids(root)["ex:a1"]
    # Two marks, then a review: revision 3 on the site, 1 as the store counts it.
    events(root, remote, a1, (1, 3), (2, 3), (3, 1))
    state = remote.query("glyph-atlas-v3", f"SELECT k.key FROM crops c JOIN codes k ON k.field='state' AND k.code=c.state WHERE c.id={a1}")
    char = remote.query("glyph-atlas-v3", f"SELECT g.text FROM crops c JOIN graphemes g ON g.id=c.char WHERE c.id={a1}")[0]["text"]
    # The store, which imported the review as two of its own, publishes the crop unchanged at its revision 2.
    run(publish, monkeypatch, catalogue(tmp_path / "c.sqlite", [unit("ex:a1", "hl:p1", {"x": 0, "y": 0, "w": 10, "h": 10}, char,
                                                                     state=state[0]["key"], revision=2)]))
    assert crops(remote)[a1]["revision"] == 3
    assert logical(publish, lambda q: remote.query("glyph-atlas-v3", q), a1) == [{"n": 2}]
    # Nothing a reviewer sees changed, so there is no boundary: open views and undos stand.
    assert remote.query("glyph-atlas-v3", f"SELECT count(*) AS n FROM events WHERE crop={a1} AND kind=8") == [{"n": 0}]
    events(root, remote, a1, (4, 1))
    assert logical(publish, lambda q: remote.query("glyph-atlas-v3", q), a1) == [{"n": 3}]


def test_a_re_cut_that_clears_a_mark_leaves_the_revision_the_store_counts(layout, monkeypatch, tmp_path):
    root, publish, remote = layout
    a1 = ids(root)["ex:a1"]
    seen = remote.dbs["glyph-atlas-v3"].execute("SELECT code FROM codes WHERE field='state' AND key='seen'").fetchone()[0]
    events(root, remote, a1, (1, 3), state=seen)
    run(publish, monkeypatch, catalogue(tmp_path / "c.sqlite", [unit("ex:a1", "hl:p1", {"x": 1, "y": 1, "w": 10, "h": 10}, revision=1)]))
    assert remote.query("glyph-atlas-v3", f"SELECT kind FROM events WHERE crop={a1} ORDER BY revision DESC LIMIT 1") == [{"kind": 7}]
    assert logical(publish, lambda q: remote.query("glyph-atlas-v3", q), a1) == [{"n": 1}]


def test_the_review_migrations_reach_a_copy_that_took_an_earlier_one(layout, monkeypatch, tmp_path):
    root, publish, remote = layout
    # Both copies took migration 0015 as it first stood, with no table for the store's revisions.
    for db in (remote.dbs["glyph-atlas-v3"], sqlite3.connect(root / "out" / "atlas.sqlite")):
        db.executescript("DROP INDEX event_reviews; DROP TABLE review_revisions;")
        db.commit()
    run(publish, monkeypatch, catalogue(tmp_path / "c.sqlite", [unit("ex:a1", "hl:p1", {"x": 0, "y": 0, "w": 10, "h": 10}, "お")]))
    for db in (remote.dbs["glyph-atlas-v3"], sqlite3.connect(root / "out" / "atlas.sqlite")):
        assert {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE name IN ('review_revisions','event_reviews')")} == {
            "review_revisions", "event_reviews"}
        assert "evidence" in {r[1] for r in db.execute("PRAGMA table_info(submissions)")}


def test_migrate_applies_the_review_migrations_alone_and_stamps_them(layout, monkeypatch):
    root, publish, remote = layout
    for db in (remote.dbs["glyph-atlas-v3"], sqlite3.connect(root / "out" / "atlas.sqlite")):
        db.executescript("DROP INDEX event_reviews; DROP TABLE review_revisions;")
        db.commit()
    pages = remote.query("glyph-atlas-v3", "SELECT id, rev FROM pages")
    monkeypatch.setattr(sys, "argv", ["publish.py", "--migrate"])
    publish.main()
    assert remote.query("glyph-atlas-v3", "SELECT count(*) AS n FROM sqlite_master WHERE name IN ('review_revisions','event_reviews')") == [{"n": 2}]
    assert remote.query("glyph-atlas-v3", "SELECT stamp FROM imports WHERE stamp LIKE 'migration:001_'") == [{"stamp": "migration:0016"}]
    assert remote.query("glyph-atlas-v3", "SELECT id, rev FROM pages") == pages
    # Run again, it finds both in place and sends nothing.
    publish.main()
    assert remote.query("glyph-atlas-v3", "SELECT count(*) AS n FROM imports WHERE stamp LIKE 'migration:001_'") == [{"n": 1}]


def test_a_database_that_took_an_earlier_0016_gains_its_new_columns_under_a_stamp_of_their_own(layout, monkeypatch):
    root, publish, remote = layout
    for db in (remote.dbs["glyph-atlas-v3"], sqlite3.connect(root / "out" / "atlas.sqlite")):
        db.executescript("ALTER TABLE crops DROP COLUMN bumps; ALTER TABLE events DROP COLUMN published;")
        db.commit()
    # As publish.py stamps the whole migration when it applies one.
    remote.dbs["glyph-atlas-v3"].execute("INSERT INTO imports(stamp, at) VALUES('migration:0016', 0)")
    remote.dbs["glyph-atlas-v3"].commit()
    # Before them, the count reads the anchors and leaves re-cut events out.
    local = sqlite3.connect(root / "out" / "atlas.sqlite")
    assert "review_revisions" in publish.logical_sql(local) and "kind IN (7," in publish.logical_sql(local)
    monkeypatch.setattr(sys, "argv", ["publish.py", "--migrate"])
    publish.main()
    for db in (remote.dbs["glyph-atlas-v3"], sqlite3.connect(root / "out" / "atlas.sqlite")):
        assert "bumps" in {r[1] for r in db.execute("PRAGMA table_info(crops)")}
        assert "published" in {r[1] for r in db.execute("PRAGMA table_info(events)")}
    assert remote.query("glyph-atlas-v3", "SELECT count(*) AS n FROM imports WHERE stamp LIKE 'migration:0016+%'") == [{"n": 1}]


def test_a_local_copy_without_the_review_history_is_refused(layout, monkeypatch, tmp_path):
    root, publish, _remote = layout
    db = sqlite3.connect(root / "out" / "atlas.sqlite")
    db.execute("DROP TABLE submissions")
    db.commit()
    with pytest.raises(SystemExit, match="run load_history.py first"):
        run(publish, monkeypatch, catalogue(tmp_path / "c.sqlite", [unit("ex:a1", "hl:p1", {"x": 0, "y": 0, "w": 10, "h": 10})]),
            "--dry-run")


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
                     [("ex:a1", 2, "ex:a3", None, "あえ", "book:a", 1, 1, 0, "あえ")])
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


def test_a_crop_put_back_goes_past_the_events_of_its_earlier_life(layout, monkeypatch, tmp_path):
    root, publish, remote = layout
    a2 = ids(root)["ex:a2"]
    # Marked once (revision 1, 0 as the store counts it), retired at 0, then put back at 0.
    for db in (remote.dbs["glyph-atlas-v3"], sqlite3.connect(root / "out" / "atlas.sqlite")):
        db.execute("INSERT INTO events(crop, revision, cut, gen, actor, submission, at, kind) VALUES(?, 1, 0, 0, 1, 1, 1, 3)", (a2,))
        db.execute("UPDATE crops SET revision=1 WHERE id=?", (a2,))
        db.commit()
    gone = unit("ex:a2", "hl:p1", {"x": 0, "y": 20, "w": 10, "h": 10}, "い", revision=0)
    run(publish, monkeypatch, catalogue(tmp_path / "gone.sqlite", [(gone[0], "retired", *gone[2:])]))
    assert a2 not in crops(remote)
    run(publish, monkeypatch, catalogue(tmp_path / "back.sqlite", [gone]))
    assert crops(remote)[a2]["revision"] == 2
    count = f"SELECT {publish.LOGICAL} AS n FROM crops c WHERE c.id={a2}"
    assert remote.query("glyph-atlas-v3", count) == [{"n": 0}]
    assert sqlite3.connect(root / "out" / "atlas.sqlite").execute(count).fetchone() == (0,)


def test_a_crop_put_back_takes_a_revision_no_view_from_before_holds(layout, monkeypatch, tmp_path):
    root, publish, remote = layout
    a2 = ids(root)["ex:a2"]
    # Two marks, then a review showing あ: revision 3 on the site, 1 as the store counts it.
    for db in (remote.dbs["glyph-atlas-v3"], sqlite3.connect(root / "out" / "atlas.sqlite")):
        db.executemany("INSERT INTO events(crop, revision, cut, gen, actor, submission, at, kind) VALUES(?, ?, 0, 0, 1, 1, 1, ?)",
                       [(a2, 1, 3), (a2, 2, 3), (a2, 3, 1)])
        db.execute("UPDATE crops SET revision=3 WHERE id=?", (a2,))
        db.commit()
    gone = unit("ex:a2", "hl:p1", {"x": 0, "y": 20, "w": 10, "h": 10}, "あ", revision=1)
    run(publish, monkeypatch, catalogue(tmp_path / "gone.sqlite", [(gone[0], "retired", *gone[2:])]))
    back = unit("ex:a2", "hl:p1", {"x": 0, "y": 20, "w": 10, "h": 10}, "い", revision=2)
    run(publish, monkeypatch, catalogue(tmp_path / "back.sqlite", [back]))
    # A new revision, above every event and the one it was retired at; the store's 2 is recorded there.
    assert crops(remote)[a2]["revision"] == 4
    assert remote.query("glyph-atlas-v3", f"SELECT {publish.LOGICAL} AS n FROM crops c WHERE c.id={a2}") == [{"n": 2}]
    assert remote.query("glyph-atlas-v3", f"SELECT revision, kind, published FROM events WHERE crop={a2} AND kind=8") == [
        {"revision": 4, "kind": 8, "published": publish.EVERYTHING}]


def test_a_crop_retired_without_its_revision_is_not_put_back(layout, monkeypatch, tmp_path):
    root, publish, remote = layout
    a2 = ids(root)["ex:a2"]
    # Seen, undone, seen (1–3), a review (4), then a publication moved it to 5 with no event.
    for db in (remote.dbs["glyph-atlas-v3"], sqlite3.connect(root / "out" / "atlas.sqlite")):
        db.executemany("INSERT INTO events(crop, revision, cut, gen, actor, submission, at, kind) VALUES(?, ?, 0, 0, 1, 1, 1, ?)",
                       [(a2, 1, 3), (a2, 2, 5), (a2, 3, 3), (a2, 4, 1)])
        db.execute("UPDATE crops SET revision=5 WHERE id=?", (a2,))
        db.commit()
    gone = unit("ex:a2", "hl:p1", {"x": 0, "y": 20, "w": 10, "h": 10}, "い", revision=4)
    run(publish, monkeypatch, catalogue(tmp_path / "gone.sqlite", [(gone[0], "retired", *gone[2:])]))
    # A bundle written before retired entries kept their revision.
    rev = sqlite3.connect(root / "out" / "atlas.sqlite").execute("SELECT rev FROM pages WHERE id=?", (a2 // PAGE,)).fetchone()[0]
    path = root / "out" / "pages" / f"{a2 // PAGE}-{rev}.json.gz"
    body = json.loads(gzip.decompress(path.read_bytes()))
    for e in body["crops"]:
        if e["id"] == "ex:a2":
            del e["revision"]
    path.write_bytes(gzip.compress(json.dumps(body).encode()))
    with pytest.raises(SystemExit):
        run(publish, monkeypatch, catalogue(tmp_path / "back.sqlite", [gone]))
    assert a2 not in crops(remote)


def test_a_book_style_moves_no_revision_and_sets_no_boundary(layout, monkeypatch, tmp_path):
    root, publish, remote = layout
    a1 = ids(root)["ex:a1"]
    before = crops(remote)[a1]
    path = tmp_path / "styles.sqlite"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE book_styles(document, style)")
    db.execute("INSERT INTO book_styles VALUES('book:a', 'cursive')")
    db.commit()
    with pytest.raises(SystemExit, match=r"--approve-styles (\d+)") as refused:
        run(publish, monkeypatch, path)
    approved = re.search(r"--approve-styles (\d+)", str(refused.value)).group(1)
    # The refused run's frames hold its lock on out/mirror.lock until they are released.
    del refused
    gc.collect()
    run(publish, monkeypatch, path, "--approve-styles", approved)
    after = crops(remote)[a1]
    # A book's style is a fact about the document: open views and undos stand, and the store's count is untouched.
    assert after["style"] != before["style"] and after["revision"] == before["revision"]
    assert remote.query("glyph-atlas-v3", f"SELECT count(*) AS n FROM review_revisions WHERE crop={a1}") == [{"n": 0}]
    assert remote.query("glyph-atlas-v3", f"SELECT count(*) AS n FROM events WHERE crop={a1}") == [{"n": 0}]


def test_a_corpus_catalogue_keeps_the_review_of_a_corpus_glyph(layout, monkeypatch, tmp_path):
    root, publish, remote = layout
    g1 = dict(sqlite3.connect(root / "out" / "mirror.sqlite").execute("SELECT text, id FROM ids"))["codh:g1"]
    before = crops(remote)[g1]
    path = tmp_path / "corpus.sqlite"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE corpus_units(id,character,family,visual_group,shuffle,object,offset,size,production,named,style,document,label)")
    db.execute(f"INSERT INTO corpus_units VALUES('codh:g1','が',NULL,NULL,7,'{PACK[2]}',0,120,'printed',0,'unassessed','codh:b',NULL)")
    db.commit()
    run(publish, monkeypatch, path)
    after = crops(remote)[g1]
    assert (after["char"], after["state"], after["quiz"], after["revision"]) == (before["char"], before["state"], before["quiz"], 2)
    body = json.loads(gzip.decompress((root / "out" / "pages" / f"{g1 // PAGE}-2.json.gz").read_bytes()))
    assert body["crops"][0]["record_place"] == [PACK[2], 0, 120]


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


def test_a_box_a_reviewer_redrew_onto_another_crops_glyph_is_published_as_the_site_holds_it(layout, monkeypatch, tmp_path):
    root, publish, _remote = layout
    a1 = unit("ex:a1", "hl:p1", {"x": 0, "y": 20, "w": 10, "h": 10})
    data = {**json.loads(a1[-1]), "box_pending": True}
    run(publish, monkeypatch, catalogue(tmp_path / "c.sqlite", [(*a1[:-1], json.dumps(data))]))
    page = ids(root)["ex:a1"] // PAGE
    rev = sqlite3.connect(root / "out" / "atlas.sqlite").execute("SELECT rev FROM pages WHERE id=?", (page,)).fetchone()[0]
    entry = next(e for e in json.loads(gzip.decompress((root / "out" / "pages" / f"{page}-{rev}.json.gz").read_bytes()))["crops"]
                 if e["id"] == "ex:a1")
    assert entry["box"] == {"x": 0, "y": 20, "w": 10, "h": 10}


def test_a_known_crop_with_another_box_is_re_cut_under_its_own_id(layout, monkeypatch, tmp_path):
    root, publish, remote = layout
    a2 = ids(root)["ex:a2"]
    before = crops(remote)[a2]
    run(publish, monkeypatch, catalogue(tmp_path / "c.sqlite", [unit("ex:a2", "hl:p1", {"x": 2, "y": 22, "w": 12, "h": 9}, "い",
                                                                     state="checked", revision=3)]))
    after = crops(remote)[a2]
    assert ids(root)["ex:a2"] == a2 and after["cut"] == before["cut"] + 1
    assert {k: after[k] for k in ("char", "state")} == {k: before[k] for k in ("char", "state")}
    # A new cut is a change a reviewer sees: one revision on, with a publication event holding the new cut;
    # the store's count and its anchor are untouched, as the publication did not speak for the crop.
    assert after["revision"] == before["revision"] + 1
    assert remote.query("glyph-atlas-v3", f"SELECT revision, cut, kind, published FROM events WHERE crop={a2}") == [
        {"revision": after["revision"], "cut": after["cut"], "kind": 8, "published": publish.SET_BITS["cut"]}]
    assert remote.query("glyph-atlas-v3", f"SELECT count(*) AS n FROM review_revisions WHERE crop={a2}") == [{"n": 0}]
    assert remote.query("glyph-atlas-v3", f"SELECT {publish.LOGICAL} AS n FROM crops c WHERE c.id={a2}") == [{"n": before["revision"]}]
    entry = next(e for e in json.loads(gzip.decompress((root / "out" / "pages" / f"{a2 // PAGE}-2.json.gz").read_bytes()))["crops"]
                 if e["id"] == "ex:a2")
    assert entry["box"] == {"x": 2, "y": 22, "w": 12, "h": 9}


def test_a_publication_that_does_not_speak_for_a_crop_leaves_the_stores_revision_alone(layout, monkeypatch, tmp_path):
    root, publish, remote = layout
    a2 = ids(root)["ex:a2"]
    # The store last spoke for the crop at its revision 2, the crop's 3; a review since is not yet imported.
    for db in (remote.dbs["glyph-atlas-v3"], sqlite3.connect(root / "out" / "atlas.sqlite")):
        db.execute("INSERT INTO events(crop, revision, cut, gen, actor, submission, at, kind) VALUES(?, 4, 0, 0, 1, 1, 1, 1)", (a2,))
        db.execute("UPDATE crops SET revision=4 WHERE id=?", (a2,))
        db.execute("INSERT INTO review_revisions(crop, revision, at) VALUES(?, 2, 3)", (a2,))
        db.commit()
    # A catalogue behind the site: its revision speaks for nothing, but its new cut applies.
    run(publish, monkeypatch, catalogue(tmp_path / "c.sqlite", [unit("ex:a2", "hl:p1", {"x": 2, "y": 22, "w": 12, "h": 9}, "い", revision=2)]))
    assert crops(remote)[a2]["revision"] == 5
    assert remote.query("glyph-atlas-v3", f"SELECT revision, at FROM review_revisions WHERE crop={a2}") == [{"revision": 2, "at": 3}]
    assert remote.query("glyph-atlas-v3", f"SELECT {publish.LOGICAL} AS n FROM crops c WHERE c.id={a2}") == [{"n": 3}]


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
    # The new cut moves it on to 2 with a boundary; clearing the mark is event 3.
    assert (after["cut"], after["state"], after["revision"]) == (1, 0, 3)
    db = remote.dbs["glyph-atlas-v3"]
    assert [tuple(r) for r in db.execute("SELECT crop, revision, cut, actor, submission, kind, state FROM events ORDER BY revision")] == [
        (a1, 2, 1, 0, 0, 8, None), (a1, 3, 1, 0, 0, 7, 0)]
    assert [tuple(r) for r in db.execute("SELECT state FROM reviews")] == [(0,)]
    local = sqlite3.connect(root / "out" / "atlas.sqlite")
    assert local.execute("SELECT state, revision FROM crops WHERE id=?", (a1,)).fetchone() == (0, 3)
    # A crop that is not marked keeps its state through a re-cut: only the new cut's publication event.
    run(publish, monkeypatch, catalogue(tmp_path / "d.sqlite", [unit("ex:a1", "hl:p1", {"x": 2, "y": 2, "w": 8, "h": 8})]))
    assert [tuple(r) for r in db.execute("SELECT kind, count(*) FROM events GROUP BY kind ORDER BY kind")] == [(7, 1), (8, 2)]


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
    db.execute(f"INSERT INTO corpus_units VALUES('codh:g1','が',NULL,NULL,7,'{PACK[1]}',0,100,'printed',0,'unassessed','codh:b',NULL)")
    db.execute(f"CREATE TABLE units({','.join(UNITS)})")
    db.execute(f"INSERT INTO units VALUES({','.join('?' * len(UNITS))})",
               ("codh:g1", "corpus", "か", None, None, "printed", "kana", "checked", 5, 0, 1, 1, "codh:b", "cursive", None, "{}"))
    db.commit()
    run(publish, monkeypatch, path)
    styles = dict(sqlite3.connect(root / "out" / "mirror.sqlite").execute("SELECT key, code FROM codes WHERE field='style'"))
    assert crops(remote)[g1]["style"] == styles["cursive"]
    assert remote.query("glyph-atlas-v3", f"SELECT {publish.LOGICAL} AS n FROM crops c WHERE c.id={g1}") == [{"n": 5}]


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
    db.execute(f"INSERT INTO corpus_units VALUES('codh:x1','き',NULL,NULL,7,'{PACK[3]}',0,100,'printed',0,'unassessed','codh:b:x',NULL)")
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
    db.executemany(f"INSERT INTO corpus_units VALUES(?,?,?,NULL,7,'{PACK[3]}',0,100,'printed',0,'unassessed','codh:b',?)", rows)
    db.commit()
    return path


def test_a_corpus_republish_keeps_a_followed_character_and_moves_its_base(layout, monkeypatch, tmp_path):
    root, publish, remote = layout
    run(publish, monkeypatch, corpus_catalogue(tmp_path / "c1.sqlite", [("codh:x1", "き", None, None), ("codh:x2", "き", None, None)]))
    x1, x2 = ids(root)["codh:x1"], ids(root)["codh:x2"]
    db = remote.dbs["glyph-atlas-v3"]
    grapheme = lambda text: db.execute("SELECT id FROM graphemes WHERE text=?", (text,)).fetchone()[0]
    # A forms decision moved x1 to か in the Worker; its base is き.
    db.execute("INSERT INTO form_bases(crop, char) VALUES(?, ?)", (x1, grapheme("き")))
    db.execute("INSERT INTO form_glyphs(crop,family,form,decision) VALUES(?, 'U+304B', 'か', 'd')", (x1,))
    db.execute("UPDATE crops SET char=? WHERE id=?", (grapheme("か"), x1))
    db.commit()
    run(publish, monkeypatch, corpus_catalogue(tmp_path / "c2.sqlite", [("codh:x1", "く", None, None), ("codh:x2", "く", None, None)]))
    after = crops(remote)
    assert (after[x1]["char"], after[x2]["char"]) == (grapheme("か"), grapheme("く"))
    # A withdrawn form restores the base, now the new upstream character.
    assert db.execute("SELECT char FROM form_bases WHERE crop=?", (x1,)).fetchone()[0] == grapheme("く")
    local = sqlite3.connect(root / "out" / "atlas.sqlite")
    assert local.execute("SELECT char FROM crops WHERE id=?", (x2,)).fetchone()[0] == grapheme("く")
    run(publish, monkeypatch, corpus_catalogue(tmp_path / "c3.sqlite", [("codh:x1", "く", None, None), ("codh:x2", "く", None, None)]))
    assert crops(remote)[x1]["char"] == grapheme("か")


def test_a_followed_run_keeps_its_written_variant_when_upstream_changes_to_the_head(layout, monkeypatch, tmp_path):
    root, publish, remote = layout
    run(publish, monkeypatch, corpus_catalogue(tmp_path / "c1.sqlite", [("codh:x1", "き", None, None)]))
    crop = ids(root)["codh:x1"]
    atlas, runs = remote.dbs["glyph-atlas-v3"], remote.dbs["glyph-atlas-v3-runs"]
    chars = dict(atlas.execute("SELECT text,id FROM graphemes"))
    atlas.execute("UPDATE graphemes SET head=? WHERE id=?", (chars["あ"], chars["か"]))
    atlas.execute("INSERT INTO form_bases VALUES(?,?)", (crop, chars["き"]))
    atlas.execute("INSERT INTO form_glyphs(crop,family,form,decision) VALUES(?, 'U+3042', 'か', 'd')", (crop,))
    atlas.execute("UPDATE crops SET char=? WHERE id=?", (chars["か"], crop))
    atlas.commit()
    runs.execute("INSERT INTO run_written(crop,char) VALUES(?, 'か')", (crop,))
    runs.execute("INSERT INTO runs(size,graphemes,hand,shuffle,first,second,third,book) VALUES(2,'ああ',0,0,?,?,NULL,1)",
                 (crop, ids(root)["ex:a1"]))
    runs.commit()
    # The live decisions have reached the local baseline before publication.
    for name, db in (("atlas.sqlite", atlas), ("atlas-runs.sqlite", runs)):
        with sqlite3.connect(root / "out" / name) as local:
            db.backup(local)
    run(publish, monkeypatch, corpus_catalogue(tmp_path / "c2.sqlite", [("codh:x1", "あ", None, None)]))
    assert atlas.execute("SELECT char FROM crops WHERE id=?", (crop,)).fetchone()[0] == chars["か"]
    assert runs.execute("SELECT char FROM run_written WHERE crop=?", (crop,)).fetchone()[0] == "か"
    with sqlite3.connect(root / "out" / "atlas.sqlite") as local:
        assert local.execute("SELECT char FROM crops WHERE id=?", (crop,)).fetchone()[0] == chars["か"]
    # A base equal to the followed character still advances, so withdrawing the decision restores it.
    run(publish, monkeypatch, corpus_catalogue(tmp_path / "c3.sqlite", [("codh:x1", "か", None, None)]))
    assert atlas.execute("SELECT char FROM form_bases WHERE crop=?", (crop,)).fetchone()[0] == chars["か"]


def test_a_republish_releases_an_inactive_legacy_base_before_upstream_correction(layout, monkeypatch, tmp_path):
    root, publish, remote = layout
    run(publish, monkeypatch, corpus_catalogue(tmp_path / "c1.sqlite", [("codh:x1", "き", None, None)]))
    crop = ids(root)["codh:x1"]
    atlas = remote.dbs["glyph-atlas-v3"]
    base = atlas.execute("SELECT char FROM crops WHERE id=?", (crop,)).fetchone()[0]
    # The old drain restored this character but kept its base; no decision or queued follow remains.
    atlas.execute("INSERT INTO form_bases VALUES(?,?)", (crop, base))
    atlas.commit()
    with sqlite3.connect(root / "out" / "atlas.sqlite") as local:
        atlas.backup(local)
    run(publish, monkeypatch, corpus_catalogue(tmp_path / "c2.sqlite", [("codh:x1", "く", None, None)]))
    assert atlas.execute("SELECT g.text FROM crops c JOIN graphemes g ON g.id=c.char WHERE c.id=?", (crop,)).fetchone()[0] == "く"
    assert atlas.execute("SELECT * FROM form_bases WHERE crop=?", (crop,)).fetchall() == []


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
                 [("codh:m1", None, "U+3082", None, 7, PACK[1], 0, 100, "printed", 0, "unassessed", "codh:b", "も")])
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


def test_a_corpus_glyph_takes_its_review_overlays_category(layout, monkeypatch, tmp_path):
    root, publish, remote = layout
    g1 = ids(root)["codh:g1"]
    kana = remote.dbs["glyph-atlas-v3"].execute("SELECT code FROM codes WHERE field='category' AND key='kana'").fetchone()[0]
    # The build files it under its overlay's category; a publish keeps it there.
    assert crops(remote)[g1]["category"] == kana
    path = tmp_path / "corpus.sqlite"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE corpus_units(id,character,family,visual_group,shuffle,object,offset,size,production,named,style,document,label)")
    db.execute(f"INSERT INTO corpus_units VALUES('codh:g1','が',NULL,NULL,7,'{PACK[1]}',0,100,'printed',0,'unassessed','codh:b',NULL)")
    db.execute(f"CREATE TABLE units({','.join(UNITS)})")
    db.execute(f"INSERT INTO units VALUES({','.join('?' * len(UNITS))})",
               ("codh:g1", "corpus", "か", None, None, "printed", "kanji", "checked", 2, 0, 1, 1, "codh:b", "unassessed", None, "{}"))
    db.commit()
    run(publish, monkeypatch, path)
    kanji = remote.dbs["glyph-atlas-v3"].execute("SELECT code FROM codes WHERE field='category' AND key='kanji'").fetchone()[0]
    assert crops(remote)[g1]["category"] == kanji


def test_a_float_literal_reads_back_as_the_same_double(layout):
    _, publish, _ = layout
    pull = importlib.import_module("pull")
    db = sqlite3.connect(":memory:")
    # SQLite 3.45 reads the shortest form of the first three one bit off; D1 reads them exactly.
    rounded = [round(i / 99991, 5) for i in range(99991)]
    for v in (0.93256, 0.87006, 0.96381, 0.1, 1e-30, 123456.789, *rounded):
        assert db.execute(f"SELECT {publish.lit(v)}").fetchone()[0] == v
        assert db.execute(f"SELECT {pull.sql_lit(v)}").fetchone()[0] == v
    assert publish.lit(3) == "3" and pull.sql_lit(3) == "3"


def test_a_run_with_a_claimed_crop_is_published_under_the_grapheme_the_claim_reads(layout, monkeypatch, tmp_path):
    root, publish, remote = layout
    a1, a2 = (ids(root)[t] for t in ("ex:a1", "ex:a2"))
    # い read as う; the Worker filed the run あい as あう when the claim was made.
    atlas, runs = remote.dbs["glyph-atlas-v3"], remote.dbs["glyph-atlas-v3-runs"]
    atlas.execute("INSERT INTO current_claims(subject,predicate,scope,slot,crop,cut,status,object,members,supporting,claims,resolver,at) "
                  "VALUES('ex:a2','has_grapheme','','',?,0,'asserted','U+3046','[]','[]','[]','r','2026-10-23')", (a2,))
    atlas.commit()
    runs.executescript(f"""UPDATE runs SET graphemes='あう' WHERE first={a1} AND size=2; DELETE FROM run_stats WHERE graphemes='あい';
      INSERT INTO run_stats VALUES(2,'あう',1,1); INSERT INTO run_written VALUES({a2},'い'); INSERT INTO run_moves VALUES({a1},2,{a2});""")
    units = [unit("ex:a1", "hl:p1", {"x": 0, "y": 0, "w": 10, "h": 10}, "あ"),
             unit("ex:a2", "hl:p1", {"x": 0, "y": 20, "w": 10, "h": 10}, "い", state="checked", revision=3)]
    run(publish, monkeypatch, catalogue(tmp_path / "c.sqlite", units, [("ex:a1", 2, "ex:a2", None, "あい", "book:a", 1, 1, 0, "あい")]))
    local = sqlite3.connect(root / "out" / "atlas-runs.sqlite")
    for db in (runs, local):
        assert db.execute("SELECT graphemes FROM runs").fetchall() == [("あう",)]
        assert db.execute("SELECT graphemes, n FROM run_stats").fetchall() == [("あう", 1)]
        assert db.execute("SELECT crop, char FROM run_written").fetchall() == [(a2, "い")]


def test_a_claim_made_before_a_publish_files_its_runs_there(layout, monkeypatch, tmp_path):
    root, publish, remote = layout
    a2 = ids(root)["ex:a2"]
    atlas = remote.dbs["glyph-atlas-v3"]
    atlas.execute("INSERT INTO current_claims(subject,predicate,scope,slot,crop,cut,status,object,members,supporting,claims,resolver,at) "
                  "VALUES('ex:a2','has_grapheme','','',?,0,'asserted','U+3046','[]','[]','[]','r','2026-10-23')", (a2,))
    atlas.commit()
    units = [unit("ex:a1", "hl:p1", {"x": 0, "y": 0, "w": 10, "h": 10}, "あ"),
             unit("ex:a2", "hl:p1", {"x": 0, "y": 20, "w": 10, "h": 10}, "い", state="checked", revision=3)]
    run(publish, monkeypatch, catalogue(tmp_path / "c.sqlite", units, [("ex:a1", 2, "ex:a2", None, "あい", "book:a", 1, 1, 0, "あい")]))
    assert [r["graphemes"] for r in remote.query("glyph-atlas-v3-runs", "SELECT graphemes FROM runs")] == ["あう"]
    assert remote.query("glyph-atlas-v3-runs", "SELECT graphemes, n FROM run_stats") == [{"graphemes": "あう", "n": 1}]
    assert remote.query("glyph-atlas-v3-runs", "SELECT crop, char FROM run_written") == [{"crop": a2, "char": "い"}]


def claim_and_move(remote, a1, a2):
    """い read as う, and the run あい filed as あう, as the Worker does when the claim is saved."""
    atlas, runs = remote.dbs["glyph-atlas-v3"], remote.dbs["glyph-atlas-v3-runs"]
    atlas.execute("INSERT INTO current_claims(subject,predicate,scope,slot,crop,cut,status,object,members,supporting,claims,resolver,at) "
                  "VALUES('ex:a2','has_grapheme','','',?,0,'asserted','U+3046','[]','[]','[]','r','2026-10-23')", (a2,))
    atlas.commit()
    runs.executescript(f"""UPDATE runs SET graphemes='あう' WHERE first={a1} AND size=2; DELETE FROM run_stats WHERE graphemes='あい';
      INSERT INTO run_stats VALUES(2,'あう',1,1); INSERT INTO run_written VALUES({a2},'い'); INSERT INTO run_moves VALUES({a1},2,{a2});""")


def claimed_catalogue(tmp_path):
    units = [unit("ex:a1", "hl:p1", {"x": 0, "y": 0, "w": 10, "h": 10}, "あ"),
             unit("ex:a2", "hl:p1", {"x": 0, "y": 20, "w": 10, "h": 10}, "い", state="checked", revision=3)]
    return catalogue(tmp_path / "c.sqlite", units, [("ex:a1", 2, "ex:a2", None, "あい", "book:a", 1, 1, 0, "あい")])


def held_runs(db):
    return (db.execute("SELECT graphemes FROM runs").fetchall(), db.execute("SELECT graphemes, n FROM run_stats").fetchall())


def test_a_dry_run_takes_the_runs_a_claim_moved_and_owes_their_answers_to_the_next_publish(layout, monkeypatch, tmp_path):
    root, publish, remote = layout
    claim_and_move(remote, *(ids(root)[t] for t in ("ex:a1", "ex:a2")))
    path = claimed_catalogue(tmp_path)
    run(publish, monkeypatch, path, "--dry-run")
    local = sqlite3.connect(root / "out" / "atlas-runs.sqlite")
    assert held_runs(local) == ([("あう",)], [("あう", 1)])
    assert local.execute("SELECT kind FROM answers_owed ORDER BY kind").fetchall() == [("book",), ("shard",)]
    run(publish, monkeypatch, path)
    local = sqlite3.connect(root / "out" / "atlas-runs.sqlite")
    assert held_runs(local) == ([("あう",)], [("あう", 1)])
    assert local.execute("SELECT count(*) FROM answers_owed").fetchone() == (0,)


def test_a_claim_disputed_while_a_publish_is_under_way_is_not_undone_by_it(layout, monkeypatch, tmp_path):
    root, publish, remote = layout
    a1, a2 = (ids(root)[t] for t in ("ex:a1", "ex:a2"))
    claim_and_move(remote, a1, a2)
    deliver = publish.deliver

    def disputed_meanwhile(m, run_dir):
        # Between planning and landing, another reader disputes the claim and the Worker files the run back.
        remote.dbs["glyph-atlas-v3"].execute("UPDATE current_claims SET status='disputed'")
        remote.dbs["glyph-atlas-v3"].commit()
        remote.dbs["glyph-atlas-v3-runs"].executescript(f"""UPDATE runs SET graphemes='あい' WHERE first={a1} AND size=2;
          DELETE FROM run_stats WHERE graphemes='あう'; INSERT INTO run_stats VALUES(2,'あい',1,1); DELETE FROM run_written WHERE crop={a2};""")
        return deliver(m, run_dir)
    monkeypatch.setattr(publish, "deliver", disputed_meanwhile)
    units = [unit("ex:a1", "hl:p1", {"x": 0, "y": 0, "w": 10, "h": 10}, "あ"),
             unit("ex:a2", "hl:p1", {"x": 0, "y": 20, "w": 10, "h": 10}, "い", state="checked", revision=3),
             unit("ex:a4", "hl:p1", {"x": 0, "y": 60, "w": 10, "h": 10}, "お")]
    run(publish, monkeypatch, catalogue(tmp_path / "c.sqlite", units, [("ex:a1", 2, "ex:a2", None, "あい", "book:a", 1, 1, 0, "あい")]))
    runs = remote.dbs["glyph-atlas-v3-runs"]
    assert held_runs(runs) == ([("あい",)], [("あい", 1)])
    assert runs.execute("SELECT count(*) FROM run_moves").fetchone() == (0,)
    assert held_runs(sqlite3.connect(root / "out" / "atlas-runs.sqlite")) == ([("あい",)], [("あい", 1)])


def test_a_claim_a_redrawn_box_stops_is_filed_back_by_the_next_publish(layout, monkeypatch, tmp_path):
    root, publish, remote = layout
    a1, a2 = (ids(root)[t] for t in ("ex:a1", "ex:a2"))
    claim_and_move(remote, a1, a2)
    # The crop is cut again on the site; its claim was made on the old cut and no longer holds.
    remote.dbs["glyph-atlas-v3"].execute("UPDATE crops SET cut=1 WHERE id=?", (a2,))
    remote.dbs["glyph-atlas-v3"].commit()
    run(publish, monkeypatch, catalogue(tmp_path / "c.sqlite", [unit("ex:b2", "hl:p2", {"x": 0, "y": 40, "w": 10, "h": 10}, "え")]))
    runs = remote.dbs["glyph-atlas-v3-runs"]
    assert held_runs(runs) == ([("あい",)], [("あい", 1)])
    assert runs.execute("SELECT count(*) FROM run_moves").fetchone() == (0,)


def test_a_claim_saved_while_a_publish_reads_the_claims_is_published_once(layout, monkeypatch, tmp_path):
    root, publish, remote = layout
    a1, a2 = (ids(root)[t] for t in ("ex:a1", "ex:a2"))
    read = publish.claimed_heads

    saved = []

    def saved_meanwhile(atlas, recut, crops=None):
        if not saved:
            claim_and_move(remote, a1, a2)
            saved.append(True)
        return read(atlas, recut, crops)
    monkeypatch.setattr(publish, "claimed_heads", saved_meanwhile)
    run(publish, monkeypatch, claimed_catalogue(tmp_path))
    for db in (remote.dbs["glyph-atlas-v3-runs"], sqlite3.connect(root / "out" / "atlas-runs.sqlite")):
        assert held_runs(db) == ([("あう",)], [("あう", 1)])


def test_a_crop_cut_again_leaves_its_claims_on_the_old_cut(layout):
    root, publish, remote = layout
    a1, a2 = (ids(root)[t] for t in ("ex:a1", "ex:a2"))
    claim_and_move(remote, a1, a2)
    atlas = sqlite3.connect(root / "out" / "atlas.sqlite")
    assert publish.claimed_heads(atlas, set()) == {a2: "う"}
    assert publish.claimed_heads(atlas, {a2}) == {}


def test_a_run_is_split_into_graphemes_as_the_worker_splits_it(layout):
    _root, publish, _remote = layout
    assert publish.graphemes_of("가나") == ["가", "나"]
    assert publish.graphemes_of("가나") == ["가", "나"]
    assert publish.graphemes_of("葛\U000E0100城") == ["葛\U000E0100", "城"]
    assert publish.graphemes_of("がな") == ["が", "な"]


def counts_match(db):
    """The runs by key, and the counts the database keeps of them."""
    db.row_factory = None
    return (db.execute("SELECT size, graphemes, count(*) FROM runs GROUP BY 1, 2 ORDER BY 1, 2").fetchall(),
            db.execute("SELECT size, graphemes, n FROM run_stats ORDER BY 1, 2").fetchall())


def saved_before_landing(publish, monkeypatch, remote, a1, a2):
    """The claim and its Worker filing land after the publish has planned, before its parts do."""
    deliver, done = publish.deliver, []

    def claimed_meanwhile(m, run_dir):
        if not done:
            claim_and_move(remote, a1, a2)
            done.append(True)
        return deliver(m, run_dir)
    monkeypatch.setattr(publish, "deliver", claimed_meanwhile)


def test_a_run_rewritten_by_a_publish_after_a_claim_moved_it_is_one_run(layout, monkeypatch, tmp_path):
    root, publish, remote = layout
    a1, a2 = (ids(root)[t] for t in ("ex:a1", "ex:a2"))
    saved_before_landing(publish, monkeypatch, remote, a1, a2)
    units = [unit("ex:a1", "hl:p1", {"x": 0, "y": 0, "w": 10, "h": 10}, "あ"),
             unit("ex:a2", "hl:p1", {"x": 0, "y": 20, "w": 10, "h": 10}, "い", state="checked", revision=3),
             unit("ex:a3", "hl:p1", {"x": 0, "y": 40, "w": 10, "h": 10}, "え")]
    path = catalogue(tmp_path / "c.sqlite", units, [("ex:a1", 2, "ex:a3", None, "あえ", "book:a", 1, 1, 0, "あえ")])
    run(publish, monkeypatch, path)
    runs = remote.dbs["glyph-atlas-v3-runs"]
    runs.row_factory = None
    assert runs.execute(f"SELECT graphemes FROM runs WHERE first={a1} AND size=2").fetchall() == [("あえ",)]
    rows, stats = counts_match(runs)
    assert rows == stats
    # The next publish of the same book keeps it so.
    units[2] = unit("ex:a3", "hl:p1", {"x": 0, "y": 40, "w": 10, "h": 10}, "え", state="checked", revision=1)
    run(publish, monkeypatch, catalogue(tmp_path / "c2.sqlite", units, [("ex:a1", 2, "ex:a3", None, "あえ", "book:a", 1, 1, 0, "あえ")]))
    assert runs.execute(f"SELECT graphemes FROM runs WHERE first={a1} AND size=2").fetchall() == [("あえ",)]
    rows, stats = counts_match(runs)
    assert rows == stats


def test_a_crop_retired_after_a_claim_moved_its_run_leaves_no_run(layout, monkeypatch, tmp_path):
    root, publish, remote = layout
    a1, a2 = (ids(root)[t] for t in ("ex:a1", "ex:a2"))
    saved_before_landing(publish, monkeypatch, remote, a1, a2)
    gone = unit("ex:a2", "hl:p1", {"x": 0, "y": 20, "w": 10, "h": 10}, revision=3)
    run(publish, monkeypatch, catalogue(tmp_path / "c.sqlite", [(gone[0], "retired", *gone[2:])]))
    runs = remote.dbs["glyph-atlas-v3-runs"]
    assert counts_match(runs) == ([], [])
    assert runs.execute("SELECT count(*) FROM run_moves").fetchone() == (0,)


def test_a_claim_disputed_while_the_reconcile_reads_is_not_filed_back(layout, monkeypatch, tmp_path):
    root, publish, remote = layout
    a1, a2 = (ids(root)[t] for t in ("ex:a1", "ex:a2"))
    claim_and_move(remote, a1, a2)
    read, calls = publish.claimed_heads, []

    def heads_then_dispute(atlas, recut, crops=None):
        out = read(atlas, recut, crops)
        calls.append(1)
        if len(calls) == 2:
            # Once the reconcile has read the claims, a dispute lands and the Worker files the run back.
            remote.dbs["glyph-atlas-v3"].execute("UPDATE current_claims SET status='disputed', at='2026-10-24'")
            remote.dbs["glyph-atlas-v3"].commit()
            remote.dbs["glyph-atlas-v3-runs"].executescript(f"""UPDATE runs SET graphemes='あい' WHERE first={a1} AND size=2;
              DELETE FROM run_stats WHERE graphemes='あう'; INSERT INTO run_stats VALUES(2,'あい',1,1); DELETE FROM run_written WHERE crop={a2};""")
        return out
    monkeypatch.setattr(publish, "claimed_heads", heads_then_dispute)
    units = [unit("ex:a1", "hl:p1", {"x": 0, "y": 0, "w": 10, "h": 10}, "あ"),
             unit("ex:a2", "hl:p1", {"x": 0, "y": 20, "w": 10, "h": 10}, "い", state="checked", revision=3),
             unit("ex:a4", "hl:p1", {"x": 0, "y": 60, "w": 10, "h": 10}, "お")]
    run(publish, monkeypatch, catalogue(tmp_path / "c.sqlite", units, [("ex:a1", 2, "ex:a2", None, "あい", "book:a", 1, 1, 0, "あい")]))
    runs = remote.dbs["glyph-atlas-v3-runs"]
    assert held_runs(runs) == ([("あい",)], [("あい", 1)])
    assert runs.execute("SELECT count(*) FROM run_written").fetchone() == (0,)


def test_a_publish_after_the_claims_are_filed_reconciles_nothing(layout, monkeypatch, tmp_path):
    root, publish, remote = layout
    claim_and_move(remote, *(ids(root)[t] for t in ("ex:a1", "ex:a2")))
    run(publish, monkeypatch, claimed_catalogue(tmp_path))
    sent, send = [], remote.send
    monkeypatch.setattr(publish, "send", lambda database, file, stamp: (sent.append(stamp), send(database, file, stamp))[1])
    run(publish, monkeypatch, catalogue(tmp_path / "c2.sqlite", [unit("ex:b2", "hl:p2", {"x": 0, "y": 40, "w": 10, "h": 10}, "え")]))
    assert sent and not [s for s in sent if ".reconcile" in s]


def test_a_claim_committed_after_a_newer_one_was_read_is_still_reconciled(layout, monkeypatch, tmp_path):
    root, publish, remote = layout
    a2 = ids(root)["ex:a2"]
    run(publish, monkeypatch, claimed_catalogue(tmp_path))
    atlas = remote.dbs["glyph-atlas-v3"]
    atlas.execute("INSERT INTO current_claims(subject,predicate,scope,slot,crop,cut,status,object,members,supporting,claims,resolver,at) "
                  "VALUES('ex:zz','has_grapheme','','',999,0,'asserted','U+3042','[]','[]','[]','r','2026-10-23T00:00:00.200Z')")
    atlas.commit()
    query = publish.query

    def late_commit(database, sql):
        out = query(database, sql)
        if "max(at)" in sql:
            # A claim timed before the one just read commits after the read, and its filing failed.
            atlas.execute("INSERT INTO current_claims(subject,predicate,scope,slot,crop,cut,status,object,members,supporting,claims,resolver,at) "
                          "VALUES('ex:a2','has_grapheme','','',?,0,'asserted','U+3046','[]','[]','[]','r','2026-10-23T00:00:00.100Z')", (a2,))
            atlas.commit()
        return out
    monkeypatch.setattr(publish, "query", late_commit)
    run(publish, monkeypatch, catalogue(tmp_path / "c2.sqlite", [unit("ex:b2", "hl:p2", {"x": 0, "y": 40, "w": 10, "h": 10}, "え")]))
    monkeypatch.setattr(publish, "query", query)
    run(publish, monkeypatch, catalogue(tmp_path / "c3.sqlite", [unit("ex:b2", "hl:p2", {"x": 0, "y": 40, "w": 12, "h": 10}, "え")]))
    assert held_runs(remote.dbs["glyph-atlas-v3-runs"]) == ([("あう",)], [("あう", 1)])


def test_a_part_boundary_never_falls_inside_a_guarded_run_change(layout, monkeypatch, tmp_path):
    root, publish, remote = layout
    claim_and_move(remote, *(ids(root)[t] for t in ("ex:a1", "ex:a2")))
    monkeypatch.setattr(publish, "PART_BYTES", 300)
    units = [unit("ex:a1", "hl:p1", {"x": 0, "y": 0, "w": 10, "h": 10}, "あ"),
             unit("ex:a2", "hl:p1", {"x": 0, "y": 20, "w": 10, "h": 10}, "い", state="checked", revision=3),
             unit("ex:a3", "hl:p1", {"x": 0, "y": 40, "w": 10, "h": 10}, "え")]
    run(publish, monkeypatch, catalogue(tmp_path / "c.sqlite", units, [("ex:a1", 2, "ex:a2", None, "あい", "book:a", 1, 1, 0, "あい"),
                                                                       ("ex:a2", 2, "ex:a3", None, "いえ", "book:a", 1, 1, 0, "いえ")]))
    rows, stats = counts_match(remote.dbs["glyph-atlas-v3-runs"])
    assert rows == stats == [(2, "あう", 1), (2, "うえ", 1)]
    for part in (root / "out" / "publish").rglob("runs-*.sql"):
        assert not part.read_text().splitlines()[0].startswith("INSERT INTO run_stats(size,graphemes,n,down) SELECT")
