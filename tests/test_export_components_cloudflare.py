"""Tests of `scripts/export_components_cloudflare.py`."""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

from glyph_atlas import reference_input

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location(
    "export_components_cloudflare", ROOT / "scripts" / "export_components_cloudflare.py"
)
export = importlib.util.module_from_spec(spec)
spec.loader.exec_module(export)


def test_the_index_replaces_both_tables_of_the_reference_input(tmp_path, monkeypatch):
    rows = [("女", "好", 1, 1, 0, 2), ("女", "如", 1, 0, 0, 2), ("子", "好", 1, 1, 0, 2), ("氵", "江", 1, 1, 0, 2)]
    monkeypatch.setattr(export.han_components, "rows", lambda: rows)
    monkeypatch.setattr(export.han_components, "unified", lambda: {"⼥": "女", "⺡": "氵"})
    path = tmp_path / "reference-input.sqlite"
    with reference_input.writing(path) as db:
        db.execute("INSERT INTO han_components VALUES('乙', 0, 1, 'U+4E59', 1, 1)")
    assert export.main(["--input", str(path)]) == 0
    db = sqlite3.connect(path)
    assert db.execute("SELECT component, tier, size, code_point, n, direct FROM han_components ORDER BY 1, 4").fetchall() == [
        ("女", 0, 2, "U+597D", 1, 1), ("女", 0, 2, "U+5982", 1, 0), ("子", 0, 2, "U+597D", 1, 1), ("氵", 0, 2, "U+6C5F", 1, 1)]
    assert db.execute("SELECT query, component, total FROM han_component_names ORDER BY 1").fetchall() == [
        ("⺡", "氵", 1), ("⼥", "女", 2), ("女", "女", 2), ("子", "子", 1), ("氵", "氵", 1)]
    assert {n for (n,) in db.execute("SELECT name FROM written")} == {"han_components", "han_component_names"}
