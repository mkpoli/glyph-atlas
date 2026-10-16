import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import export_corpus_documents as export


def test_runs_of_one_document_become_bounded_id_ranges():
    found = [("a:1", "A"), ("a:2", "A"), ("a:3", "A"), ("b:1", "B"), ("c:1", "A")]
    assert export.ranges(found, size=2) == [("a:1", "a:2", "A"), ("a:3", "a:3", "A"), ("b:1", "b:1", "B"), ("c:1", "c:1", "A")]


def test_the_statements_fill_each_glyph_once_and_repeat_safely():
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE corpus_units(id TEXT PRIMARY KEY, document TEXT)")
    db.executemany("INSERT INTO corpus_units(id) VALUES(?)", [("a:1",), ("a:2",), ("b:1",), ("z:9",)])
    sql = "".join(export.statements(export.ranges([("a:1", "A"), ("a:2", "A"), ("b:1", "B")])))
    db.executescript(sql)
    db.executescript(sql)
    assert db.execute("SELECT id,document FROM corpus_units ORDER BY id").fetchall() == [
        ("a:1", "A"), ("a:2", "A"), ("b:1", "B"), ("z:9", None)]


DOCUMENTS = "CREATE TABLE corpus_documents(document TEXT PRIMARY KEY, n INTEGER NOT NULL, title TEXT)"


def test_each_documents_glyphs_are_counted_and_named():
    db = sqlite3.connect(":memory:")
    db.execute(DOCUMENTS)
    db.execute("INSERT INTO corpus_documents VALUES('gone', 9, 'Gone')")
    db.executescript("".join(export.counts([("a:1", "A"), ("a:2", "A"), ("b:1", "B")], {"A": "Book A's", "Z": "Unpublished"})))
    assert db.execute("SELECT document,n,title FROM corpus_documents ORDER BY 1").fetchall() == [("A", 2, "Book A's"), ("B", 1, None)]


def test_a_corpus_publication_counts_and_names_the_documents_it_publishes():
    from cloudflare_schema import document_statements

    export_db = sqlite3.connect(":memory:")
    export_db.execute("CREATE TABLE corpus_units(id TEXT PRIMARY KEY, document TEXT)")
    export_db.execute(DOCUMENTS)
    export_db.executemany("INSERT INTO corpus_units VALUES(?,?)", [("a:1", "A"), ("a:2", "A"), ("x:1", None), ("b:1", "B's"), ("c:1", "C")])
    # The export knows the titles of what it read, including documents it holds no glyph of.
    export_db.executemany("INSERT INTO corpus_documents VALUES(?,0,?)", [("A", "New A"), ("B's", "B's book"), ("unheld", "Unheld")])
    site = sqlite3.connect(":memory:")
    site.execute(DOCUMENTS)
    site.execute("INSERT INTO corpus_documents VALUES('A', 9, 'Old A'), ('C', 1, 'Kept C'), ('other', 3, 'Other')")
    site.executescript("\n".join(document_statements(export_db)))
    # A title the export does not know keeps the site's; a document the export holds no glyph of is not written.
    assert site.execute("SELECT document,n,title FROM corpus_documents ORDER BY 1").fetchall() == [
        ("A", 2, "New A"), ("B's", 1, "B's book"), ("C", 1, "Kept C"), ("other", 3, "Other")]
