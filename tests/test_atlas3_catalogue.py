import importlib
import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "atlas3"))


def copy(root, table, cols, rows, rowid=True, done=True):
    db = sqlite3.connect(root / f"source-{table}.sqlite")
    db.execute(f'CREATE TABLE "{table}"({",".join((["_rowid"] if rowid else []) + cols)})')
    db.executemany(f'INSERT INTO "{table}" VALUES({",".join("?" * (len(cols) + rowid))})', rows)
    db.execute("CREATE TABLE _progress(tbl PRIMARY KEY, key TEXT)")
    db.execute("INSERT INTO _progress VALUES(?, ?)", (table, json.dumps("done" if done else [1])))
    db.commit()


def sources(root, done=True):
    copy(root, "units", ["id", "origin", "data"], [(1, "ex:a", "local", "{}"), (2, "ex:b", "local", "{}"),
                                                    (3, "ex:a", "retired", '{"v": 2}')], done=done)
    copy(root, "corpus_units", ["id", "character"], [(1, "c:1", "あ")])
    copy(root, "media", ["key", "object"], [(1, "k1", "p1")])
    copy(root, "unit_ngrams", ["first", "size"], [("ex:a", 2), ("ex:b", 2)], rowid=False)
    copy(root, "pages", ["id", "document", "data"], [("pg:1", "book:a", "{}")], rowid=False)
    copy(root, "document_dating", ["document", "axis"], [("book:a", "witness")], rowid=False)
    copy(root, "unit_redirects", ["id", "target"], [("ex:old", "ex:a")], rowid=False)


def run(root, monkeypatch):
    monkeypatch.setenv("ATLAS3_DIR", str(root))
    for name in ("build", "catalogue"):
        sys.modules.pop(name, None)
    importlib.import_module("catalogue").main()
    return sqlite3.connect(root / "catalogue.sqlite")


def test_the_catalogue_holds_each_live_row_once_without_the_copys_rowid(tmp_path, monkeypatch):
    sources(tmp_path)
    db = run(tmp_path, monkeypatch)
    assert [r[1] for r in db.execute("PRAGMA table_info(units)")] == ["id", "origin", "data"]
    # ex:a was rewritten during the copy: the later copy stands.
    assert sorted(db.execute("SELECT id, origin FROM units")) == [("ex:a", "retired"), ("ex:b", "local")]
    assert db.execute("SELECT count(*) FROM unit_ngrams").fetchone() == (2,)
    assert db.execute("SELECT * FROM unit_redirects").fetchall() == [("ex:old", "ex:a")]
    assert not (tmp_path / "catalogue.sqlite.building").exists()


def test_an_unfinished_copy_stops_it(tmp_path, monkeypatch):
    sources(tmp_path, done=False)
    with pytest.raises(SystemExit, match="unfinished"):
        run(tmp_path, monkeypatch)
    assert not (tmp_path / "catalogue.sqlite").exists()
