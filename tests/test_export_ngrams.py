import importlib.util
import sqlite3
import sys
from pathlib import Path

from glyph_atlas import tables
from glyph_atlas.schema import Box, Unit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("export_ngrams", ROOT / "scripts" / "export_ngrams.py")
export = importlib.util.module_from_spec(spec)
spec.loader.exec_module(export)
SCHEMA = "\n".join(p.read_text() for p in sorted((ROOT / "apps/cloudflare/migrations").glob("*.sql")))


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


def test_a_line_another_dataset_cuts_differently_keeps_its_runs(tmp_path):
    store = review_dataset(tmp_path / "store", [unit(0), unit(1), unit(2)])
    cells = parquet_dataset(tmp_path / "cells", [unit(1, cut="b"), unit(2, cut="b")])
    found, sizes = export.statements([store, cells])
    assert sizes == {2: 3, 3: 1}
    assert any("'L1:0','L1:1'" in s for s in found)
    assert any("'L1:0','L1:1','L1:2'" in s for s in found)


def test_a_later_dataset_replaces_every_run_from_a_crop_it_holds(tmp_path):
    # The earlier cut runs 0-1-2; the later one holds crop 0 with nothing after it within reach.
    earlier = parquet_dataset(tmp_path / "earlier", [unit(0), unit(1), unit(2)])
    later = parquet_dataset(tmp_path / "later", [unit(0), Unit(id="L1:far", line_id="L1", seq=1, box=Box(x=0, y=400, w=36, h=36))])
    found, sizes = export.statements([earlier, later])
    assert sizes == {2: 1}
    inserts = [s for s in found if s.startswith("INSERT")]
    assert not any("'L1:0'," in s for s in inserts) and any("('L1:1','L1:2')" in s for s in inserts)


def test_the_parts_record_the_runs_and_can_be_applied_again(tmp_path, monkeypatch):
    dataset = parquet_dataset(tmp_path / "cells", [unit(0), unit(1), unit(2)])
    out = tmp_path / "out"
    monkeypatch.setattr(sys, "argv", ["export_ngrams.py", str(dataset), str(out)])
    export.main()
    parts = sorted((out / "sql").glob("part-*.sql"))
    db = sqlite3.connect(":memory:")
    db.executescript(SCHEMA)
    db.executemany("INSERT INTO units(id,origin,character,production,category,state,revision,quiz,priority,shuffle,"
                   "data,snapshot,context,visual,document) VALUES(?,'local',?,'handwritten','kana','pending',0,1,1,0,"
                   "'{}','{}','{}','{}','book')", [(f"L1:{i}", c) for i, c in enumerate("申候也")])
    for _ in range(2):
        for part in parts:
            db.executescript(part.read_text())
    assert db.execute("SELECT first,size,text FROM unit_ngrams ORDER BY first,size").fetchall() == [
        ("L1:0", 2, "申候"), ("L1:0", 3, "申候也"), ("L1:1", 2, "候也")]
    assert db.execute("SELECT count(*) FROM metadata WHERE key='units_refreshed_at'").fetchone() == (1,)
