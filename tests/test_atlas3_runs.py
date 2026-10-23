import gzip
import importlib
import json
import sqlite3
import sys

import pytest
from test_atlas3_publish import (  # noqa: F401 -- `layout` is the fixture
    catalogue,
    ids,
    layout,
    run,
    unit,
    write_source,
)

NGRAMS = ("first", "size", "second", "third", "text", "document", "vertical", "hand_order", "shuffle", "graphemes")


def live(root, rows):
    (root / "source-unit_ngrams.sqlite").unlink(missing_ok=True)
    write_source(root, "unit_ngrams", NGRAMS, rows)


def before_0002(root, remote):
    """The local copy and D1 as the first build loaded them: runs and counts, no written crops."""
    for db in (sqlite3.connect(root / "out" / "atlas-runs.sqlite"), remote.dbs["glyph-atlas-v3-runs"]):
        db.executescript("DROP TABLE run_written;")
        db.commit()


def hentaigana(root, written="𛀆"):
    """ex:a2 written as 𛀆, a hentaigana い, in the live copies: a grapheme of い's family in the layout,
    as reference.py files it, and the live run of ex:a1 ex:a2 written あ𛀆."""
    local = sqlite3.connect(root / "out" / "atlas.sqlite")
    head = local.execute("SELECT id FROM graphemes WHERE text='い'").fetchone()[0]
    local.execute("INSERT OR IGNORE INTO graphemes(id,text,head) VALUES(900,'𛀆',?)", (head,))
    local.commit()
    units = sqlite3.connect(root / "source-units.sqlite")
    units.execute("UPDATE units SET character=? WHERE id='ex:a2'", (written,))
    units.commit()
    live(root, [("ex:a1", 2, "ex:a2", None, "あ" + written, "book:a", 1, 1, 0, "あい")])


def runs(remote, monkeypatch, *flags, send=None):
    for name in ("runs", "load_history", "run_answers"):
        sys.modules.pop(name, None)
    module = importlib.import_module("runs")
    monkeypatch.setattr(module, "send", send or remote.send)
    monkeypatch.setattr(module, "query", remote.query)
    monkeypatch.setattr(module, "upload", remote.upload)
    monkeypatch.setattr(module, "applied", lambda database, stamp: bool(
        remote.query(database, f"SELECT 1 AS n FROM imports WHERE stamp = '{stamp}'")))
    monkeypatch.setattr(sys, "argv", ["runs.py", *flags])
    module.main()


def written(db):
    return sorted(tuple(r) for r in db.execute("SELECT crop, char FROM run_written"))


def answer(root, name):
    return json.loads(gzip.decompress((root / "out" / "runs-answers" / f"{name}.json.gz").read_bytes()))


def test_written_crops_and_answers_load_into_a_database_without_them(layout, monkeypatch):  # noqa: F811
    root, _publish, remote = layout
    before_0002(root, remote)
    hentaigana(root)
    runs(remote, monkeypatch)
    report = (root / "out" / "runs-report.txt").read_text()
    db = remote.dbs["glyph-atlas-v3-runs"]
    assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='run_written'").fetchone()
    assert "run_written\t1 rows\t1 statements\t1 added\t0 removed\t0 changed" in report
    assert "rows written: 1 in glyph-atlas-v3-runs, and 1 stamp in glyph-atlas-v3" in report
    assert remote.uploaded == []

    runs(remote, monkeypatch, "--send")
    a2 = ids(root)["ex:a2"]
    assert written(db) == [(a2, "𛀆")] == written(sqlite3.connect(root / "out" / "atlas-runs.sqlite"))
    assert db.execute("SELECT 1 FROM imports WHERE stamp='migration:0002'").fetchone()
    # The run あい is written あ𛀆; its book and the site count it once, down the page.
    book = db.execute("SELECT book FROM runs").fetchone()[0]
    assert answer(root, "grid-2") == [["あい", 1, 1, 1]]
    assert answer(root, f"books/{book}-2") == [["あい", 1, 1]]
    assert answer(root, f"forms/2-{ord('あ'):x}") == {"あい": [1, [["あ𛀆", 1]]]}
    assert sorted(set(remote.prefixes)) == ["v3/runs/", "v3/runs/books/", "v3/runs/forms/"]
    # One stamp in glyph-atlas-v3 moves the publication key, once.
    stamps = remote.dbs["glyph-atlas-v3"].execute("SELECT count(*) FROM imports WHERE stamp LIKE 'runs-%.answers'").fetchone()[0]
    assert stamps == 1
    # A second run sends and uploads nothing.
    before = (db.execute("SELECT count(*) FROM imports").fetchone()[0], len(remote.uploaded))
    runs(remote, monkeypatch, "--send")
    assert (db.execute("SELECT count(*) FROM imports").fetchone()[0], len(remote.uploaded)) == before


def test_a_crop_written_as_its_head_has_no_row(layout, monkeypatch):  # noqa: F811
    root, _publish, remote = layout
    before_0002(root, remote)
    runs(remote, monkeypatch, "--send")
    assert written(remote.dbs["glyph-atlas-v3-runs"]) == []
    assert answer(root, f"forms/2-{ord('あ'):x}") == {"あい": [1, [["あい", 1]]]}


def test_a_fresh_copy_sends_only_the_difference(layout, monkeypatch):  # noqa: F811
    root, _publish, remote = layout
    before_0002(root, remote)
    hentaigana(root)
    runs(remote, monkeypatch, "--send")
    hentaigana(root, "い")
    sent = len(remote.uploaded)
    runs(remote, monkeypatch, "--send")
    assert "run_written\t0 rows\t1 statements\t0 added\t1 removed" in (root / "out" / "runs-report.txt").read_text()
    assert written(remote.dbs["glyph-atlas-v3-runs"]) == []
    # The shard changes; the grids and the book's grid do not.
    assert [p.split("/")[-1] for p in remote.uploaded[sent:]] == [f"2-{ord('あ'):x}.json.gz"]


def test_a_run_stopped_after_d1_applied_a_part_is_followed_on_rerun(layout, monkeypatch):  # noqa: F811
    root, _publish, remote = layout
    before_0002(root, remote)
    hentaigana(root)

    def stopped(database, file, stamp):
        applied = remote.send(database, file, stamp)
        if ".rows-" in stamp:
            raise KeyboardInterrupt  # D1 holds the part; the local copy never replays it
        return applied

    with pytest.raises(KeyboardInterrupt):
        runs(remote, monkeypatch, "--send", send=stopped)
    db, local = remote.dbs["glyph-atlas-v3-runs"], sqlite3.connect(root / "out" / "atlas-runs.sqlite")
    assert written(local) == [] and len(written(db)) == 1
    runs(remote, monkeypatch, "--send")
    assert written(local) == written(db)


def test_a_part_applies_twice_without_a_conflict(layout, monkeypatch):  # noqa: F811
    root, _publish, remote = layout
    before_0002(root, remote)
    hentaigana(root)
    runs(remote, monkeypatch, "--send")
    db = remote.dbs["glyph-atlas-v3-runs"]
    got = written(db)
    for part in (root / "out" / "runs-load").glob("runs-*/rows-*.sql"):
        db.executescript("BEGIN;\n" + part.read_text().rsplit("INSERT INTO imports", 1)[0] + "COMMIT;\n")
    assert written(db) == got


def test_check_compares_the_written_forms_and_answers_with_the_live_runs(layout, monkeypatch):  # noqa: F811
    root, _publish, remote = layout
    hentaigana(root)
    sys.modules.pop("check", None)
    check = importlib.import_module("check")

    class Report:
        def __init__(self):
            self.failed = {}

        def check(self, name, problems):
            self.failed[name] = list(problems)

        def skip(self, name, why):
            self.failed[name] = [why]

    def checked():
        report = Report()
        check.check_run_forms(report, dict(sqlite3.connect(root / "out" / "atlas.sqlite").execute("SELECT key, id FROM books")))
        return sorted(name for name, problems in report.failed.items() if problems)

    # A schema alone has no loaded answer snapshot; all five checks must report it as unavailable.
    assert len(checked()) == 5
    runs(remote, monkeypatch, "--send")
    assert checked() == []
    # A crop written otherwise than its live runs say, and a shard that counts otherwise.
    local = sqlite3.connect(root / "out" / "atlas-runs.sqlite")
    local.execute("UPDATE run_written SET char='𛀇'")
    local.commit()
    assert checked() == ["each run is written as its live row"]
    shard = root / "out" / "runs-answers" / "forms" / f"2-{ord('あ'):x}.json.gz"
    shard.write_bytes(gzip.compress(b"{}"))
    assert checked() == ["each run is written as its live row", "the forms shards count the live runs' written forms"]


def test_a_publish_keeps_the_written_crops_and_answers(layout, monkeypatch, tmp_path):  # noqa: F811
    root, publish, remote = layout
    path = catalogue(tmp_path / "c.sqlite", [unit("ex:a1", "hl:p1", {"x": 0, "y": 0, "w": 10, "h": 10}, "あ"),
                                             unit("ex:a3", "hl:p1", {"x": 0, "y": 40, "w": 10, "h": 10}, "え")],
                     [("ex:a1", 2, "ex:a3", None, "あえ", "book:a", 1, 1, 0, "あえ")])
    run(publish, monkeypatch, path)
    db = remote.dbs["glyph-atlas-v3-runs"]
    book = db.execute("SELECT book FROM runs").fetchone()[0]
    assert answer(root, f"books/{book}-2") == [["あえ", 1, 1]]
    assert answer(root, f"forms/2-{ord('あ'):x}") == {"あえ": [1, [["あえ", 1]]]}
    assert answer(root, "grid-2") == [["あえ", 1, 1, 1]]
    assert {"v3/runs/", "v3/runs/books/", "v3/runs/forms/"} <= set(remote.prefixes)


def test_a_run_with_other_crops_is_updated_in_place(layout, monkeypatch, tmp_path):  # noqa: F811
    root, publish, remote = layout
    path = catalogue(tmp_path / "c.sqlite", [unit("ex:a1", "hl:p1", {"x": 0, "y": 0, "w": 10, "h": 10}, "あ")],
                     [("ex:a1", 2, "ex:b1", None, "あい", "book:a", 1, 1, 0, "あい")])
    run(publish, monkeypatch, path)
    parts = "".join(p.read_text() for p in (root / "out" / "publish").glob("*/runs-*.sql"))
    assert f"UPDATE runs SET second={ids(root)['ex:b1']} WHERE" in parts and "DELETE FROM runs" not in parts
    assert remote.dbs["glyph-atlas-v3-runs"].execute("SELECT second FROM runs").fetchall() == [(ids(root)["ex:b1"],)]


def test_a_run_across_the_page_is_left_out_and_the_rest_published(layout, monkeypatch, tmp_path, capsys):  # noqa: F811
    root, publish, remote = layout
    before = remote.dbs["glyph-atlas-v3-runs"].execute("SELECT * FROM runs ORDER BY first").fetchall()
    path = catalogue(tmp_path / "c.sqlite", [unit("ex:a1", "hl:p1", {"x": 0, "y": 0, "w": 10, "h": 10}, "え")],
                     [("ex:a1", 2, "ex:a2", None, "えい", "book:a", 0, 1, 0, "えい")])
    run(publish, monkeypatch, path)
    assert "1 runs are written across the page and left out" in capsys.readouterr().out
    assert remote.dbs["glyph-atlas-v3"].execute("SELECT g.text FROM crops c JOIN graphemes g ON g.id=c.char WHERE c.id=?",
                                                (ids(root)["ex:a1"],)).fetchone() == ("え",)
    # The crop's runs down the page stay as they were.
    assert remote.dbs["glyph-atlas-v3-runs"].execute("SELECT * FROM runs ORDER BY first").fetchall() == before


def test_a_publish_needs_the_written_crops_loaded(layout, monkeypatch, tmp_path):  # noqa: F811
    root, publish, remote = layout
    before_0002(root, remote)
    path = catalogue(tmp_path / "c.sqlite", [unit("ex:a1", "hl:p1", {"x": 0, "y": 0, "w": 10, "h": 10}, "あ")])
    with pytest.raises(SystemExit, match="runs.py --send"):
        run(publish, monkeypatch, path)


def test_a_row_the_publisher_wrote_for_a_crop_in_no_run_stays(layout, monkeypatch):  # noqa: F811
    root, _publish, remote = layout
    hentaigana(root)
    runs(remote, monkeypatch, "--send")
    # A crop in no run, written apart from its head: the publisher's row.
    loose = max(r[0] for r in sqlite3.connect(root / "out" / "atlas.sqlite").execute("SELECT id FROM crops")) + 1
    for db in (remote.dbs["glyph-atlas-v3-runs"], sqlite3.connect(root / "out" / "atlas-runs.sqlite")):
        db.execute("INSERT INTO run_written(crop, char) VALUES(?, '𛀆')", (loose,))
        db.commit()
    stamps = remote.dbs["glyph-atlas-v3-runs"].execute("SELECT count(*) FROM imports").fetchone()[0]
    runs(remote, monkeypatch, "--send")
    assert tuple(remote.dbs["glyph-atlas-v3-runs"].execute("SELECT char FROM run_written WHERE crop=?", (loose,)).fetchone()) == ("𛀆",)
    assert remote.dbs["glyph-atlas-v3-runs"].execute("SELECT count(*) FROM imports").fetchone()[0] == stamps


def test_the_migrations_apply_alone_once_each_with_their_stamps(layout, monkeypatch):  # noqa: F811
    root, _publish, remote = layout
    db, local = remote.dbs["glyph-atlas-v3-runs"], sqlite3.connect(root / "out" / "atlas-runs.sqlite")
    for copy in (db, local):
        copy.executescript("DROP TABLE run_moves;")
        copy.commit()
    runs(remote, monkeypatch, "--migrate")
    for copy in (db, sqlite3.connect(root / "out" / "atlas-runs.sqlite")):
        assert copy.execute("SELECT 1 FROM sqlite_master WHERE name='run_moves'").fetchone()
    assert db.execute("SELECT count(*) FROM imports WHERE stamp='migration:0003'").fetchone() == (1,)
    assert db.execute("SELECT count(*) FROM imports WHERE stamp='migration:0002'").fetchone() == (0,)
    before = db.execute("SELECT count(*) FROM imports").fetchone()
    runs(remote, monkeypatch, "--migrate")
    assert db.execute("SELECT count(*) FROM imports").fetchone() == before


def test_the_row_the_worker_wrote_for_a_claimed_crop_stays(layout, monkeypatch):  # noqa: F811
    root, _publish, remote = layout
    a2 = ids(root)["ex:a2"]
    atlas = remote.dbs["glyph-atlas-v3"]
    atlas.execute("INSERT INTO current_claims(subject,predicate,scope,slot,crop,cut,status,object,members,supporting,claims,resolver,at) "
                  "VALUES('ex:a2','has_grapheme','','',?,0,'asserted','U+3046','[]','[]','[]','r','2026-10-23')", (a2,))
    atlas.commit()
    before_0002(root, remote)
    runs(remote, monkeypatch, "--migrate")
    for copy in (remote.dbs["glyph-atlas-v3-runs"], sqlite3.connect(root / "out" / "atlas-runs.sqlite")):
        copy.execute("INSERT INTO run_written VALUES(?, 'い')", (a2,))
        copy.commit()
    runs(remote, monkeypatch, "--send")
    assert written(remote.dbs["glyph-atlas-v3-runs"]) == [(a2, "い")]
