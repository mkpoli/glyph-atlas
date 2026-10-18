"""Irregular marks: a box far narrower or wider than its character's is marked, and the SQL loads into the schema."""
import json
import sqlite3
from pathlib import Path

from glyph_atlas.review import irregular

MIGRATION = Path("apps/cloudflare/migrations/0080_unit_irregular.sql").read_text()


def crop(name, label="あ", w=40, h=40, document="d"):
    return {"id": name, "label": label, "document": document, "box": {"x": 0, "y": 0, "w": w, "h": h}}


def written(label, n, document="d", w=40):
    return [crop(f"{label}{document}{i}", label, w=w + i % 3, h=40 + i % 2, document=document) for i in range(n)]


def test_a_half_character_is_marked_narrow():
    found = irregular.marks(written("あ", 30) + [crop("half", w=12)])
    assert list(found) == ["half"] and found["half"]["shape"] < -irregular.THRESHOLD
    assert found["half"]["label"] == "あ" and found["half"]["box"]["w"] == 12


def test_two_characters_side_by_side_are_marked_wide():
    assert irregular.marks(written("あ", 30) + [crop("double", w=82)])["double"]["shape"] > irregular.THRESHOLD


def test_a_hand_that_writes_narrow_is_not_marked():
    # Every character of document `n` is written at half the width; its crops are no outliers.
    crops = written("あ", 30) + written("い", 30) + written("あ", 30, "n", 20) + written("い", 30, "n", 20)
    assert irregular.marks(crops) == {}


def test_a_character_with_few_crops_is_not_measured():
    assert irregular.marks(written("あ", irregular.MIN_CROPS - 2) + [crop("half", w=5)]) == {}


def test_the_sql_replaces_every_mark(tmp_path):
    marks = tmp_path / "irregular.json"
    marks.write_text(json.dumps({"marks": {"it's": {"shape": -6.5, "label": "あ", "box": {"x": 1, "y": 2, "w": 3, "h": 4}}}}))
    assert irregular.sql(marks, tmp_path / "irregular.sql") == 1
    db = sqlite3.connect(":memory:")
    db.executescript(MIGRATION + "CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
                     "INSERT INTO unit_irregular VALUES('gone','い',0,0,1,1,6);")
    db.executescript((tmp_path / "irregular.sql").read_text())
    assert list(db.execute("SELECT * FROM unit_irregular")) == [("it's", "あ", 1.0, 2.0, 3.0, 4.0, -6.5)]
    assert db.execute("SELECT count(*) FROM metadata WHERE key='units_refreshed_at'").fetchone() == (1,)
