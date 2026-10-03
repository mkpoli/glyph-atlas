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
