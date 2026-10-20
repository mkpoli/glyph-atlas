import gzip
import importlib
import json
import sqlite3
import sys

import pytest
from test_atlas3_publish import ids, layout, write_source  # noqa: F401 -- `layout` is the fixture

DATING = ("document", "axis", "kind", "start", "end", "precision", "qualifier", "uncertain", "text", "label", "status",
          "calendar", "conversion", "source")


def dated(document, axis="witness", start=1700):
    return (document, axis, "printed", start, start, "year", None, 0, str(start), str(start), "single", "gregorian",
            json.dumps({"query": "https://ap.hutime.org/cal/"}), "source:test")


def sources(root, dating=None, titles=None, characters=None, metadata=None):
    for table in ("document_dating", "corpus_documents", "document_characters", "metadata"):
        (root / f"source-{table}.sqlite").unlink(missing_ok=True)
    write_source(root, "document_dating", DATING, dating if dating is not None else [dated("book:a"), dated("nowhere")])
    write_source(root, "corpus_documents", ("document", "title"), titles if titles is not None else [("codh:b", "鼎左秘録")])
    write_source(root, "document_characters", ("document", "ord", "unit", "data"), characters if characters is not None else [
        ("book:a", 0, "ex:a1", json.dumps({"label": "あ", "page": 1})),
        ("book:a", 1, "ar:gone", json.dumps({"label": "い", "page": 1}))])
    write_source(root, "metadata", ("key", "value"), metadata if metadata is not None else [
        ("review_epoch", '"2026-10-01"'), ("collection", '{"books": 2}'), ("corpus_index", '{"glyphs": 1}'),
        ("variant_sources", "{}"), ("word_sources", "{}"), ("published_at", '"2026-10-19"')])


def books(remote, monkeypatch, *flags, send=None):
    for name in ("books", "load_history"):
        sys.modules.pop(name, None)
    module = importlib.import_module("books")
    load_history = importlib.import_module("load_history")
    for target in (module, load_history):
        monkeypatch.setattr(target, "send", send or remote.send)
    monkeypatch.setattr(module, "query", remote.query)
    monkeypatch.setattr(module, "applied", lambda database, stamp: bool(
        remote.query(database, f"SELECT 1 AS n FROM imports WHERE stamp = '{stamp}'")))
    monkeypatch.setattr(sys, "argv", ["books.py", *flags])
    module.main()


def facts(db):
    db.row_factory = None
    return {"titles": dict(db.execute("SELECT key, title FROM books")),
            "dates": db.execute("SELECT b.key, d.axis, d.start, d.calendar FROM book_dates d JOIN books b ON b.id = d.book").fetchall(),
            "characters": db.execute("SELECT book, ord, crop, unit FROM book_characters ORDER BY book, ord").fetchall(),
            "site": dict(db.execute("SELECT key, value FROM site"))}


def test_book_facts_load_once_by_book_and_crop(layout, monkeypatch):  # noqa: F811
    root, _publish, remote = layout
    sources(root)
    books(remote, monkeypatch)
    db = remote.dbs["glyph-atlas-v3"]
    # A dry run sends nothing and reports what it would.
    assert db.execute("SELECT count(*) FROM book_dates").fetchone() == (0,)
    report = (root / "out" / "books-report.txt").read_text()
    assert "1 dates of documents that are not books, left out" in report
    assert "1 characters with no published crop, kept by their source id" in report

    books(remote, monkeypatch, "--send")
    book = dict(db.execute("SELECT key, id FROM books"))
    got = facts(db)
    assert got["titles"] == {"book:a": None, "codh:b": "鼎左秘録"}
    assert got["dates"] == [("book:a", "witness", 1700, "gregorian")]
    assert got["characters"] == [(book["book:a"], 0, ids(root)["ex:a1"], None), (book["book:a"], 1, None, "ar:gone")]
    assert set(got["site"]) == {"review_epoch", "collection", "corpus_index", "variant_sources", "word_sources"}
    # The local copy replays the same parts.
    assert facts(sqlite3.connect(root / "out" / "atlas.sqlite")) == got
    # A second run finds nothing to send.
    stamps = db.execute("SELECT count(*) FROM imports").fetchone()[0]
    books(remote, monkeypatch, "--send")
    assert db.execute("SELECT count(*) FROM imports").fetchone()[0] == stamps


def test_a_fresh_copy_sends_only_the_difference(layout, monkeypatch):  # noqa: F811
    root, _publish, remote = layout
    sources(root)
    books(remote, monkeypatch, "--send")
    sources(root, dating=[dated("book:a", start=1701), dated("codh:b", "composed", 1600)], titles=[],
            characters=[("book:a", 0, "ex:a1", json.dumps({"label": "あ", "page": 1}))])
    books(remote, monkeypatch, "--send")
    got = facts(remote.dbs["glyph-atlas-v3"])
    assert got["titles"]["codh:b"] is None
    assert sorted(got["dates"]) == [("book:a", "witness", 1701, "gregorian"), ("codh:b", "composed", 1600, "gregorian")]
    assert len(got["characters"]) == 1
    assert "books.title\t0\t0\t1" in (root / "out" / "books-report.txt").read_text()


def test_a_run_stopped_after_d1_applied_a_part_is_followed_on_rerun(layout, monkeypatch):  # noqa: F811
    root, _publish, remote = layout
    sources(root, dating=[dated("book:a"), dated("codh:b", "composed", 1600)])

    def stopped(database, file, stamp):
        remote.send(database, file, stamp)
        if ".rows-" in stamp:
            raise KeyboardInterrupt  # D1 holds the part; the local copy never replays it

    with pytest.raises(KeyboardInterrupt):
        books(remote, monkeypatch, "--send", send=stopped)
    db, local = remote.dbs["glyph-atlas-v3"], sqlite3.connect(root / "out" / "atlas.sqlite")
    assert len(facts(db)["dates"]) == 2 and facts(local)["dates"] == []
    # A fresh copy changes one date and drops the other: the rerun follows D1 first, then sends the difference.
    sources(root, dating=[dated("book:a", start=1701)])
    books(remote, monkeypatch, "--send")
    assert facts(db)["dates"] == [("book:a", "witness", 1701, "gregorian")]
    assert facts(local) == facts(db)


def test_a_part_applies_twice_without_a_conflict(layout, monkeypatch):  # noqa: F811
    root, _publish, remote = layout
    sources(root)
    books(remote, monkeypatch, "--send")
    db = remote.dbs["glyph-atlas-v3"]
    got = facts(db)
    for part in (root / "out" / "books-load").glob("books-*/rows-*.sql"):
        db.executescript("BEGIN;\n" + part.read_text().rsplit("INSERT INTO imports", 1)[0] + "COMMIT;\n")
    assert facts(db) == got


def test_a_long_value_arrives_whole(layout, monkeypatch):  # noqa: F811
    root, _publish, remote = layout
    big = json.dumps({"books": "あ" * 60_000})
    sources(root, metadata=[("collection", big)])
    books(remote, monkeypatch, "--send")
    assert facts(remote.dbs["glyph-atlas-v3"])["site"] == {"collection": big}
    parts = list((root / "out" / "books-load").glob("books-*/rows-*.sql"))
    assert max(len(line.encode()) for p in parts for line in p.read_text().splitlines()) < 100_000


def test_a_database_without_migration_0005_gets_it_first(layout, monkeypatch):  # noqa: F811
    root, _publish, remote = layout
    for db in (remote.dbs["glyph-atlas-v3"], sqlite3.connect(root / "out" / "atlas.sqlite")):
        db.executescript("DROP TABLE book_dates; DROP TABLE book_characters; DROP TABLE site; ALTER TABLE books DROP COLUMN title;")
    sources(root)
    books(remote, monkeypatch)
    assert "title" not in {r[1] for r in remote.dbs["glyph-atlas-v3"].execute("PRAGMA table_info(books)")}
    books(remote, monkeypatch, "--send")
    db = remote.dbs["glyph-atlas-v3"]
    assert db.execute("SELECT 1 FROM imports WHERE stamp='migration:0005'").fetchone()
    assert facts(db)["dates"] == [("book:a", "witness", 1700, "gregorian")]


def test_a_partial_dating_copy_is_refused(layout, monkeypatch):  # noqa: F811
    root, _publish, remote = layout
    sources(root)
    (root / "source-document_dating.sqlite").unlink()
    write_source(root, "document_dating", ("document", "axis", "kind", "start", "end", "precision", "label", "status"), [])
    with pytest.raises(SystemExit, match="pull.py --fresh document_dating"):
        books(remote, monkeypatch)


def test_a_book_takes_the_title_of_its_first_page_before_its_corpus_title(layout, monkeypatch):  # noqa: F811
    root, _publish, remote = layout
    local = sqlite3.connect(root / "out" / "atlas.sqlite")
    book = dict(local.execute("SELECT key, id FROM books"))
    for key in ("book:a", "codh:b"):
        pid, rev = local.execute("SELECT id, rev FROM pages WHERE book=? AND rev > 0 ORDER BY id LIMIT 1", (book[key],)).fetchone()
        path = root / "out" / "pages" / f"{pid}-{rev}.json.gz"
        bundle = json.loads(gzip.decompress(path.read_bytes()))
        path.write_bytes(gzip.compress(json.dumps({**bundle, "page": {"title": f"{key} pages"}}).encode()))
    sources(root, titles=[("codh:b", "鼎左秘録")])
    books(remote, monkeypatch, "--send")
    assert facts(remote.dbs["glyph-atlas-v3"])["titles"] == {"book:a": "book:a pages", "codh:b": "codh:b pages"}


def test_a_book_without_a_page_title_takes_its_crops_source(layout, monkeypatch):  # noqa: F811
    root, _publish, remote = layout
    local = sqlite3.connect(root / "out" / "atlas.sqlite")
    book = dict(local.execute("SELECT key, id FROM books"))
    for key, source in (("book:a", "凌煙閣功臣圖"), ("codh:b", json.dumps({"title": "羣書治要"}))):
        pid, rev = local.execute("SELECT id, rev FROM pages WHERE book=? AND rev > 0 ORDER BY id LIMIT 1", (book[key],)).fetchone()
        path = root / "out" / "pages" / f"{pid}-{rev}.json.gz"
        bundle = json.loads(gzip.decompress(path.read_bytes()))
        crops = [{**c, "source": source if i else None} for i, c in enumerate(bundle["crops"] * 2)]
        path.write_bytes(gzip.compress(json.dumps({**bundle, "page": None, "crops": crops}).encode()))
    sources(root, titles=[("codh:b", "鼎左秘録")])
    books(remote, monkeypatch, "--send")
    assert facts(remote.dbs["glyph-atlas-v3"])["titles"] == {"book:a": "凌煙閣功臣圖", "codh:b": "羣書治要"}
