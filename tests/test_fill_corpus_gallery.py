import importlib.util
import sqlite3
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load():
    # The script runs with boto3 in its own environment; these tests read only its SQL.
    for name in ("boto3", "botocore", "botocore.config", "botocore.exceptions"):
        sys.modules.setdefault(name, types.ModuleType(name))
    sys.modules["botocore.config"].Config = object
    sys.modules["botocore.exceptions"].BotoCoreError = sys.modules["botocore.exceptions"].ClientError = Exception
    spec = importlib.util.spec_from_file_location("fill_corpus_gallery", ROOT / "scripts/fill_corpus_gallery.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def corpus(rows):
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE corpus_units(id TEXT PRIMARY KEY, character TEXT, shuffle INTEGER, object TEXT, offset INTEGER, size INTEGER)")
    db.execute("CREATE INDEX corpus_character ON corpus_units(character,id)")
    db.execute("CREATE TABLE corpus_gallery(id TEXT PRIMARY KEY, shuffle INTEGER, object TEXT, offset INTEGER, data TEXT, category TEXT)")
    db.executemany("INSERT INTO corpus_units VALUES(?,?,?,'pack',0,1)", rows)
    return db


def test_missing_copies_the_sample_and_every_hangul_and_gugyeol_glyph():
    fill = load()
    high = fill.SAMPLE_RANGE + 1
    db = corpus([("low", "仮", 5), ("kanji", "仮", high), ("syllable", "그", high), ("old", "ᄲᅢ", high),
                 ("gugyeol", "", high), ("past", "퟼", high)])
    found = {row[0] for row in db.execute(fill.MISSING)}
    assert found == {"low", "syllable", "old", "gugyeol"}
    db.execute("INSERT INTO corpus_gallery VALUES('syllable',?,'pack',0,'{}','hangul')", (high,))
    assert "syllable" not in {row[0] for row in db.execute(fill.MISSING)}


def test_missing_reads_ranges_of_the_character_index():
    fill = load()
    plan = " ".join(row[3] for row in corpus([]).execute("EXPLAIN QUERY PLAN " + fill.MISSING))
    assert "corpus_character (character>? AND character<?)" in plan


def test_copies_take_their_glyphs_current_scripts():
    fill = load()
    high = fill.SAMPLE_RANGE + 1
    rows = [{"id": "a", "shuffle": 1, "category": None, "character": "仮"},
            {"id": "b", "shuffle": high, "category": None, "character": "그"},
            {"id": "c", "shuffle": 1, "category": "kana", "character": "あ"},
            {"id": "d", "shuffle": 2, "category": "kana", "character": "仮"},
            {"id": "e", "shuffle": high, "category": "kana", "character": "그"},
            {"id": "f", "shuffle": high, "category": "hangul", "character": "あ"}]
    assert fill.recategorised(rows, sqlite3.connect(":memory:")) == [
        "UPDATE corpus_gallery SET category='kanji' WHERE id IN ('a','d');",
        "UPDATE corpus_gallery SET category='hangul' WHERE id IN ('b','e');",
        "DELETE FROM corpus_gallery WHERE id IN ('f');"]


def test_long_runs_of_ids_split_under_the_statement_limit():
    fill = load()
    rows = [{"id": f"codh:{i:08}", "shuffle": 1, "category": None, "character": "仮"} for i in range(20_000)]
    statements = fill.recategorised(rows, sqlite3.connect(":memory:"))
    assert len(statements) > 1
    assert all(len(statement.encode()) <= fill.STATEMENT_BYTES for statement in statements)
    assert sum(statement.count("'codh:") for statement in statements) == len(rows)
