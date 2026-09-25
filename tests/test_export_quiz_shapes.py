"""The shape-order SQL replaces the table and loads into the migrated schema."""
import importlib
import json
import sqlite3
from pathlib import Path

import pytest

MIGRATION = Path("apps/cloudflare/migrations/0005_unit_shapes.sql").read_text()


@pytest.fixture
def module(monkeypatch):
    monkeypatch.syspath_prepend(str(Path("scripts").resolve()))
    return importlib.import_module("export_quiz_shapes")


def test_the_sql_replaces_every_shape_order(module, tmp_path):
    shapes = tmp_path / "quiz-shapes.json"
    shapes.write_text(json.dumps({"orders": {"it's": 2, "b": 0}}))
    assert module.export(shapes, tmp_path / "shapes.sql") == 2
    db = sqlite3.connect(":memory:")
    db.executescript(MIGRATION + "INSERT INTO unit_shapes VALUES('gone',5);")
    db.executescript((tmp_path / "shapes.sql").read_text())
    assert dict(db.execute("SELECT id, shape_order FROM unit_shapes")) == {"b": 0, "it's": 2}


def test_a_malformed_order_is_refused(module, tmp_path):
    shapes = tmp_path / "quiz-shapes.json"
    shapes.write_text(json.dumps({"orders": {"a": "1"}}))
    with pytest.raises(ValueError, match="non-negative integer"):
        module.export(shapes, tmp_path / "shapes.sql")
