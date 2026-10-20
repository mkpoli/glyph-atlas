import importlib
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
    """The local copy and D1 as the first build loaded them: runs and counts with no forms."""
    for db in (sqlite3.connect(root / "out" / "atlas-runs.sqlite"), remote.dbs["glyph-atlas-v3-runs"]):
        db.executescript("DROP TABLE run_forms; DROP TABLE run_books; ALTER TABLE runs DROP COLUMN form;"
                         "ALTER TABLE runs DROP COLUMN vertical;")
        db.commit()


def runs(remote, monkeypatch, *flags, send=None):
    for name in ("runs", "load_history"):
        sys.modules.pop(name, None)
    module = importlib.import_module("runs")
    monkeypatch.setattr(module, "send", send or remote.send)
    monkeypatch.setattr(module, "query", remote.query)
    monkeypatch.setattr(module, "applied", lambda database, stamp: bool(
        remote.query(database, f"SELECT 1 AS n FROM imports WHERE stamp = '{stamp}'")))
    monkeypatch.setattr(sys, "argv", ["runs.py", *flags])
    module.main()


def forms(db):
    db.row_factory = None
    return {"runs": sorted(db.execute("SELECT first, size, form, vertical FROM runs")),
            "forms": sorted(db.execute("SELECT size, graphemes, form, n, down FROM run_forms")),
            "books": sorted(db.execute("SELECT book, size, graphemes, n, down FROM run_books"))}


# The layout's one run, ex:a1 ex:a2 (あい), written as ん𛁅 down the page, with a second line across it.
def rows(text="ん𛁅", vertical=1):
    return [("ex:a1", 2, "ex:a2", None, text, "book:a", vertical, 1, 0, "あい")]


def test_forms_load_into_a_database_without_them(layout, monkeypatch):  # noqa: F811
    root, _publish, remote = layout
    before_0002(root, remote)
    live(root, rows())
    runs(remote, monkeypatch)
    report = (root / "out" / "runs-report.txt").read_text()
    db = remote.dbs["glyph-atlas-v3-runs"]
    assert "form" not in {r[1] for r in db.execute("PRAGMA table_info(runs)")}
    # removed, added, changed, written: one run's UPDATE, one form and one book row's INSERT.
    assert "runs\t1\t0\t0\t1\t1\n" in report and "run_forms\t1\t0\t1\t0\t1\n" in report and "run_books\t1\t0\t1\t0\t1\n" in report
    assert "rows written: 3 " in report

    runs(remote, monkeypatch, "--send")
    a1, book = ids(root)["ex:a1"], db.execute("SELECT book FROM runs").fetchone()[0]
    got = forms(db)
    assert got == {"runs": [(a1, 2, "ん𛁅", 1)], "forms": [(2, "あい", "ん𛁅", 1, 1)], "books": [(book, 2, "あい", 1, 1)]}
    assert db.execute("SELECT 1 FROM imports WHERE stamp='migration:0002'").fetchone()
    # The local copy replays the same parts; a second run finds nothing to send.
    assert forms(sqlite3.connect(root / "out" / "atlas-runs.sqlite")) == got
    stamps = db.execute("SELECT count(*) FROM imports").fetchone()[0]
    runs(remote, monkeypatch, "--send")
    assert db.execute("SELECT count(*) FROM imports").fetchone()[0] == stamps


def test_a_run_written_as_its_graphemes_has_no_form_row(layout, monkeypatch):  # noqa: F811
    root, _publish, remote = layout
    before_0002(root, remote)
    live(root, rows(text="あい", vertical=0))
    runs(remote, monkeypatch, "--send")
    got = forms(remote.dbs["glyph-atlas-v3-runs"])
    assert got["runs"] == [(ids(root)["ex:a1"], 2, None, 0)]
    assert got["forms"] == []
    assert [b[2:] for b in got["books"]] == [("あい", 1, 0)]


def test_a_fresh_copy_sends_only_the_difference(layout, monkeypatch):  # noqa: F811
    root, _publish, remote = layout
    before_0002(root, remote)
    live(root, rows())
    runs(remote, monkeypatch, "--send")
    live(root, rows(text="あい"))
    runs(remote, monkeypatch, "--send")
    report = (root / "out" / "runs-report.txt").read_text()
    # The run's form goes, and with it its form row; the book's count is unchanged.
    assert "runs\t1\t0\t0\t1\t1\n" in report and "run_forms\t0\t1\t0\t0\t1\n" in report and "run_books\t1\t0\t0\t0\t0\n" in report
    assert forms(remote.dbs["glyph-atlas-v3-runs"])["forms"] == []


def test_a_run_stopped_after_d1_applied_a_part_is_followed_on_rerun(layout, monkeypatch):  # noqa: F811
    root, _publish, remote = layout
    before_0002(root, remote)
    live(root, rows())

    def stopped(database, file, stamp):
        applied = remote.send(database, file, stamp)
        if ".rows-" in stamp:
            raise KeyboardInterrupt  # D1 holds the part; the local copy never replays it
        return applied

    with pytest.raises(KeyboardInterrupt):
        runs(remote, monkeypatch, "--send", send=stopped)
    db, local = remote.dbs["glyph-atlas-v3-runs"], sqlite3.connect(root / "out" / "atlas-runs.sqlite")
    assert forms(local)["forms"] == [] and len(forms(db)["forms"]) == 1
    runs(remote, monkeypatch, "--send")
    assert forms(local) == forms(db)


def test_a_part_applies_twice_without_a_conflict(layout, monkeypatch):  # noqa: F811
    root, _publish, remote = layout
    before_0002(root, remote)
    live(root, rows())
    runs(remote, monkeypatch, "--send")
    db = remote.dbs["glyph-atlas-v3-runs"]
    got = forms(db)
    for part in (root / "out" / "runs-load").glob("runs-*/rows-*.sql"):
        db.executescript("BEGIN;\n" + part.read_text().rsplit("INSERT INTO imports", 1)[0] + "COMMIT;\n")
    assert forms(db) == got


def test_a_book_keeps_its_most_frequent_runs(layout, monkeypatch):  # noqa: F811
    root, _publish, remote = layout
    build = importlib.import_module("build")
    monkeypatch.setattr(build, "BOOK_RUNS", 1)
    local = sqlite3.connect(root / "out" / "atlas-runs.sqlite")
    a1, a2, b1 = (ids(root)[t] for t in ("ex:a1", "ex:a2", "ex:b1"))
    book = local.execute("SELECT book FROM runs").fetchone()[0]
    # Two runs of いう and one of あい: the book's grid of one keeps いう.
    local.executemany("INSERT INTO runs(size,graphemes,hand,shuffle,first,second,third,book) VALUES(2,'いう',1,?,?,?,NULL,?)",
                      [(1, a2, b1, book), (2, b1, a1, book)])
    local.commit()
    runs(remote, monkeypatch)
    out = sqlite3.connect(root / "out" / "runs-forms.sqlite")
    assert out.execute("SELECT graphemes, n FROM run_books").fetchall() == [("いう", 2)]


def test_check_compares_the_forms_with_the_live_runs(layout, monkeypatch):  # noqa: F811
    root, _publish, _remote = layout
    live(root, rows())
    sys.modules.pop("check", None)
    check = importlib.import_module("check")

    class Report:
        def __init__(self):
            self.failed, self.skipped = {}, []

        def check(self, name, problems):
            self.failed[name] = list(problems)

        def skip(self, name, why):
            self.skipped.append(name)

    def checked():
        report = Report()
        db = sqlite3.connect(root / "out" / "atlas-runs.sqlite")
        check.check_run_forms(report, db, ids(root), dict(sqlite3.connect(root / "out" / "atlas.sqlite").execute("SELECT key, id FROM books")))
        return {name: problems for name, problems in report.failed.items() if problems}

    # The build wrote the live text as the graphemes; the live row now says ん𛁅.
    failed = checked()
    assert len(failed) == 2 and all("ん𛁅" in problems[0] or "あい" in problems[0] for problems in failed.values())
    local = sqlite3.connect(root / "out" / "atlas-runs.sqlite")
    local.executescript("UPDATE runs SET form='ん𛁅'; INSERT INTO run_forms VALUES(2,'あい','ん𛁅',1,1);")
    local.commit()
    assert checked() == {}
    local.executescript("UPDATE run_books SET n=2")
    local.commit()
    assert list(checked()) == [f"each book's grid is its first {check.BOOK_RUNS} live runs of each length (ngram_counts)"]


def test_a_publish_keeps_forms_and_book_grids(layout, monkeypatch, tmp_path):  # noqa: F811
    root, publish, remote = layout
    path = catalogue(tmp_path / "c.sqlite", [unit("ex:a1", "hl:p1", {"x": 0, "y": 0, "w": 10, "h": 10}, "あ"),
                                             unit("ex:a3", "hl:p1", {"x": 0, "y": 40, "w": 10, "h": 10}, "え")],
                     [("ex:a1", 2, "ex:a3", None, "あ𛀁", "book:a", 0, 1, 0, "あえ")])
    run(publish, monkeypatch, path)
    db = remote.dbs["glyph-atlas-v3-runs"]
    got = forms(db)
    assert [r[2:] for r in got["runs"]] == [("あ𛀁", 0)]
    assert got["forms"] == [(2, "あえ", "あ𛀁", 1, 0)]
    assert [b[2:] for b in got["books"]] == [("あえ", 1, 0)]
    assert forms(sqlite3.connect(root / "out" / "atlas-runs.sqlite")) == got


def test_a_publish_needs_the_forms_loaded(layout, monkeypatch, tmp_path):  # noqa: F811
    root, publish, remote = layout
    before_0002(root, remote)
    path = catalogue(tmp_path / "c.sqlite", [unit("ex:a1", "hl:p1", {"x": 0, "y": 0, "w": 10, "h": 10}, "あ")],
                     [("ex:a1", 2, "ex:a2", None, "あい", "book:a", 1, 1, 0, "あい")])
    with pytest.raises(SystemExit, match="runs.py --send"):
        run(publish, monkeypatch, path)
