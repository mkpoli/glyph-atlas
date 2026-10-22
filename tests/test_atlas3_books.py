import gzip
import importlib
import json
import sqlite3
import sys
from pathlib import Path

import pytest
from test_atlas3_publish import ids, layout, write_source  # noqa: F401 -- `layout` is the fixture

from glyph_atlas import book_facts_input

SCHEMA = Path(__file__).resolve().parents[1] / "apps" / "cloudflare" / "migrations-atlas"
DATING = ("document", "axis", "kind", "start", "end", "precision", "qualifier", "uncertain", "text", "label", "status",
          "calendar", "conversion", "source")


def dated(document, axis="witness", start=1700):
    return (document, axis, "printed", start, start, "year", None, 0, str(start), str(start), "single", "gregorian",
            json.dumps({"query": "https://ap.hutime.org/cal/"}), "source:test")


def sources(root, dating=None, titles=None, characters=None, metadata=None, claims=(), stamp="2026-10-20T00:00:00Z"):
    for table in ("corpus_documents", "metadata"):
        (root / f"source-{table}.sqlite").unlink(missing_ok=True)
    (root / "book-facts-input.sqlite").unlink(missing_ok=True)
    with book_facts_input.writing(root / "book-facts-input.sqlite") as db:
        db.executemany(f"INSERT INTO document_dating({','.join(DATING)}, claims, resolver) "
                       f"VALUES({','.join('?' * len(DATING))}, '[]', 'dates-2')",
                       dating if dating is not None else [dated("book:a"), dated("nowhere")])
        db.executemany("INSERT INTO document_characters VALUES(?,?,?,?)", characters if characters is not None else [
            ("book:a", 0, "ex:a1", json.dumps({"label": "あ", "page": 1})),
            ("codh:b", 0, "codh:g1", json.dumps({"label": "が", "page": 1}))])
        db.executemany("INSERT INTO date_claims VALUES(?,?,?,?,?,?,?,?,?)", claims)
        for table in ("date_claims", "document_dating", "document_characters"):
            book_facts_input.noted(db, table, stamp)
    write_source(root, "corpus_documents", ("document", "title"), titles if titles is not None else [("codh:b", "鼎左秘録")])
    write_source(root, "metadata", ("key", "value"), metadata if metadata is not None else [
        ("review_epoch", '"2026-10-01"'), ("collection", '{"books": 2}'), ("corpus_index", '{"glyphs": 1}'),
        ("variant_sources", "{}"), ("word_sources", "{}"), ("published_at", '"2026-10-19"')])


def history(root, seen=()):
    """A seen mark on each crop of `seen` in the local copy."""
    local = sqlite3.connect(root / "out" / "atlas.sqlite")
    local.executemany("INSERT INTO seen(submission, crop, cut, at) VALUES(1, ?, 0, 0)", [(ids(root)[t],) for t in seen])
    local.commit()


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
            "characters": db.execute("SELECT book, ord, crop FROM book_characters ORDER BY book, ord").fetchall(),
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
    # New rows write once each: the DELETE ahead of an INSERT finds nothing.
    assert "book_characters\t2\t80\t0\t2\t0\t2\n" in report
    assert "rows written: 7 " in report

    books(remote, monkeypatch, "--send")
    book = dict(db.execute("SELECT key, id FROM books"))
    got = facts(db)
    assert got["titles"] == {"book:a": None, "codh:b": "鼎左秘録"}
    assert got["dates"] == [("book:a", "witness", 1700, "gregorian")]
    assert sorted(got["characters"]) == sorted([(book["book:a"], 0, ids(root)["ex:a1"]), (book["codh:b"], 0, ids(root)["codh:g1"])])
    assert set(got["site"]) == {"review_epoch", "collection", "corpus_index"}
    # The local copy replays the same parts.
    local = sqlite3.connect(root / "out" / "atlas.sqlite")
    assert facts(local) == got
    # A second run finds nothing to send, and leaves the site values reference.py writes alone.
    for target in (db, local):
        target.execute("""INSERT INTO site VALUES('variant_sources', '{"cjkvi-variants": "x"}')""")
        target.commit()
    stamps = db.execute("SELECT count(*) FROM imports").fetchone()[0]
    books(remote, monkeypatch, "--send")
    assert db.execute("SELECT count(*) FROM imports").fetchone()[0] == stamps
    assert "variant_sources" in facts(db)["site"]


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
    report = (root / "out" / "books-report.txt").read_text()
    # removed, added, changed, written: a changed row writes its DELETE and its INSERT.
    assert "books.title\t0\t0\t0\t0\t1\t1\n" in report
    assert "book_dates\t2\t203\t0\t1\t1\t3\n" in report
    assert "book_characters\t1\t40\t1\t0\t0\t1\n" in report
    assert "rows written: 5 " in report


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


def test_a_table_no_generator_wrote_stops_the_run(layout, monkeypatch):  # noqa: F811
    root, _publish, remote = layout
    sources(root)
    db = sqlite3.connect(root / "book-facts-input.sqlite")
    db.execute("DELETE FROM written WHERE name='document_characters'")
    db.commit()
    with pytest.raises(SystemExit, match="export_document_characters.py writes it"):
        books(remote, monkeypatch)


def claim(identity, document="book:a", start=1700, by="kokusho"):
    value = json.dumps({"of": "witness", "text": str(start), "start": start, "end": start}, separators=(",", ":"))
    return (identity, document, "date_printed", value, "attested", f"source:{by}", "source", by, f"{by}#printed")


def ledger(db):
    db.row_factory = None
    return {"assertions": db.execute("SELECT id, subject, slot, asserted_at, method, run FROM assertions ORDER BY id").fetchall(),
            "evidence": db.execute("SELECT assertion, kind, ref, locator FROM assertion_evidence ORDER BY 1").fetchall(),
            "actions": db.execute("SELECT id, assertion, action, actor, at FROM assertion_actions ORDER BY 1").fetchall(),
            "current": db.execute("SELECT subject, status, json_extract(value, '$.start'), supporting, crop, cut FROM current_claims "
                                  "ORDER BY 1").fetchall()}


def test_date_claims_go_to_the_ledger_with_their_source_and_resolved_slots(layout, monkeypatch):  # noqa: F811
    root, _publish, remote = layout
    sources(root, claims=[claim("dt:1"), claim("dt:2", "nowhere", 1500)])
    books(remote, monkeypatch, "--send")
    db = remote.dbs["glyph-atlas-v3"]
    got = ledger(db)
    stamp = "2026-10-20T00:00:00Z"
    assert got["assertions"] == [("dt:1", "book:a", claim("dt:1")[3], stamp, "atlas dates", "dates-2"),
                                 ("dt:2", "nowhere", claim("dt:2", "nowhere", 1500)[3], stamp, "atlas dates", "dates-2")]
    assert got["evidence"] == [("dt:1", "source", "kokusho", "kokusho#printed"), ("dt:2", "source", "kokusho", "kokusho#printed")]
    # A document that is no book keeps its claims and their resolution, for when it becomes one.
    assert [(c[0], c[1], c[2], json.loads(c[3]), c[4], c[5]) for c in got["current"]] == [
        ("book:a", "asserted", 1700, ["dt:1"], None, None), ("nowhere", "asserted", 1500, ["dt:2"], None, None)]
    assert ledger(sqlite3.connect(root / "out" / "atlas.sqlite")) == got
    stamps = db.execute("SELECT count(*) FROM imports").fetchone()[0]
    books(remote, monkeypatch, "--send")
    assert db.execute("SELECT count(*) FROM imports").fetchone()[0] == stamps, "a rerun finds nothing to send"


def test_a_date_no_source_states_is_retracted_and_a_claim_that_returns_is_asserted_anew(layout, monkeypatch):  # noqa: F811
    root, _publish, remote = layout
    sources(root, claims=[claim("dt:1"), claim("dt:2", start=1710, by="iiif-manifests")])
    books(remote, monkeypatch, "--send")
    sources(root, claims=[claim("dt:2", start=1710, by="iiif-manifests")], stamp="2026-10-21T00:00:00Z")
    books(remote, monkeypatch, "--send")
    db = remote.dbs["glyph-atlas-v3"]
    got = ledger(db)
    assert got["actions"] == [("dt-retract:dt:1:2026-10-21T00:00:00Z", "dt:1", "retract", "source:kokusho",
                               "2026-10-21T00:00:00Z")]
    assert [(c[1], c[2], json.loads(c[3])) for c in got["current"]] == [("asserted", 1710, ["dt:2"])]
    sources(root, claims=[claim("dt:1"), claim("dt:2", start=1710, by="iiif-manifests")], stamp="2026-10-22T00:00:00Z")
    books(remote, monkeypatch, "--send")
    got = ledger(db)
    assert [a[0] for a in got["assertions"]] == ["dt:1", "dt:1@2026-10-22T00:00:00Z", "dt:2"]
    assert ("dt:1@2026-10-22T00:00:00Z", "source", "kokusho", "kokusho#printed") in got["evidence"]
    assert sorted(json.loads(c[3]) for c in got["current"]) == [["dt:1@2026-10-22T00:00:00Z"], ["dt:2"]]
    assert ledger(sqlite3.connect(root / "out" / "atlas.sqlite")) == got


def test_a_slot_is_resolved_from_the_verdicts_glyph_atlas_v3_holds(layout, monkeypatch):  # noqa: F811
    root, _publish, remote = layout
    sources(root, claims=[claim("dt:1")])
    books(remote, monkeypatch, "--send")
    db = remote.dbs["glyph-atlas-v3"]
    # Two readers reject dt:1 on the site; the Worker resolves the slot, the local copy never hears of it.
    db.executemany("INSERT INTO assertion_actions(id,submission,assertion,action,actor,at,reason) VALUES(?,NULL,'dt:1','reject',?,?,'')",
                   [("cf:1", "user:a", "2026-10-20T01:00:00Z"), ("cf:2", "user:b", "2026-10-20T02:00:00Z")])
    db.execute("UPDATE current_claims SET status='rejected', supporting='[]'")
    db.commit()
    # Another source states the same date: its slot is resolved again with the rejections counted.
    sources(root, claims=[claim("dt:1"), claim("dt:3", by="iiif-manifests")], stamp="2026-10-21T00:00:00Z")
    books(remote, monkeypatch, "--send")
    current = ledger(db)["current"]
    assert [(c[1], json.loads(c[3])) for c in current] == [("asserted", ["dt:3"])]


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


def shown_query():
    """The read in 0005's comment that derives whether the site shows an occurrence."""
    sql = (SCHEMA / "0005_books.sql").read_text().splitlines()
    return " ".join(line[4:].strip() for line in sql if line.startswith("--   "))


def test_a_corpus_glyph_counts_as_shown_once_a_round_showed_it(layout, monkeypatch):  # noqa: F811
    root, _publish, remote = layout
    sources(root)
    books(remote, monkeypatch, "--send")
    local = sqlite3.connect(root / "out" / "atlas.sqlite")
    book = dict(local.execute("SELECT key, id FROM books"))

    def shown(key):
        return [r[3] for r in local.execute(shown_query(), (book[key], 0, 50))]

    assert (shown("book:a"), shown("codh:b")) == ([1], [0])
    history(root, seen=["codh:g1"])
    assert shown("codh:b") == [1]
    # Every table is reached by its key or an index.
    plan = " | ".join(r[3] for r in local.execute("EXPLAIN QUERY PLAN " + shown_query(), (book["codh:b"], 0, 50)))
    assert "SCAN" not in plan, plan


def test_a_character_of_a_unit_the_site_never_had_keeps_its_text_id(layout, monkeypatch):  # noqa: F811
    root, _publish, remote = layout
    sources(root, characters=[("book:a", 0, "ar:gone", json.dumps({"label": "い"}))])
    books(remote, monkeypatch, "--send")
    db = remote.dbs["glyph-atlas-v3"]
    assert [tuple(r) for r in db.execute("SELECT ord, crop, unit FROM book_characters")] == [(0, None, "ar:gone")]
    assert "characters naming a unit the site never had, kept by their text id" in (root / "out" / "books-report.txt").read_text()
