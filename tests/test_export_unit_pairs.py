import importlib.util
import sqlite3
import sys
from pathlib import Path

from glyph_atlas import tables
from glyph_atlas.schema import Box, Unit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("export_unit_pairs", ROOT / "scripts" / "export_unit_pairs.py")
export = importlib.util.module_from_spec(spec)
spec.loader.exec_module(export)
MIGRATION = ROOT / "apps/cloudflare/migrations/0028_unit_pairs.sql"


def unit(seq, line="L1", cut=""):
    return Unit(id=f"{line}:{cut}{seq}", line_id=line, seq=seq, box=Box(x=0, y=seq * 40, w=36, h=36))


def review_dataset(path, units):
    path.mkdir()
    with sqlite3.connect(path / "review.sqlite") as db:
        db.execute("CREATE TABLE units (id TEXT PRIMARY KEY, data TEXT)")
        db.executemany("INSERT INTO units VALUES(?,?)", [(u.id, u.model_dump_json()) for u in units])
    return path


def parquet_dataset(path, units):
    path.mkdir()
    tables.write(path / "units.parquet", units, Unit)
    return path


def test_a_line_another_dataset_cuts_differently_keeps_its_pairs(tmp_path):
    store = review_dataset(tmp_path / "store", [unit(0), unit(1), unit(2)])
    cells = parquet_dataset(tmp_path / "cells", [unit(1, cut="b"), unit(2, cut="b")])
    found, pairs = export.statements([store, cells])
    assert pairs == 3
    assert any("'L1:0','L1:1'" in s for s in found)


def test_the_parts_record_the_pairs_and_can_be_applied_again(tmp_path, monkeypatch):
    dataset = parquet_dataset(tmp_path / "cells", [unit(0), unit(1), unit(2)])
    out = tmp_path / "out"
    monkeypatch.setattr(sys, "argv", ["export_unit_pairs.py", str(dataset), str(out)])
    export.main()
    parts = sorted((out / "sql").glob("part-*.sql"))
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE units (id TEXT PRIMARY KEY, origin TEXT, character TEXT, document TEXT)")
    db.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT)")
    db.executescript(MIGRATION.read_text())
    db.executemany("INSERT INTO units VALUES(?,?,?,?)", [(f"L1:{i}", "local", c, "book") for i, c in enumerate("申候也")])
    for _ in range(2):
        for part in parts:
            db.executescript(part.read_text())
    assert db.execute("SELECT first,second,text FROM unit_pairs ORDER BY first").fetchall() == [
        ("L1:0", "L1:1", "申候"), ("L1:1", "L1:2", "候也")]
    assert db.execute("SELECT count(*) FROM metadata WHERE key='units_refreshed_at'").fetchone() == (1,)
