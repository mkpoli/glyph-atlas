"""A schema-1 units table is refused, and `scripts/migrate_schema_v2.py` makes it current."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from glyph_atlas import tables
from glyph_atlas.schema import Box, Script, Unit

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("migrate_schema_v2", ROOT / "scripts" / "migrate_schema_v2.py")
assert _spec is not None and _spec.loader is not None
migrate = importlib.util.module_from_spec(_spec)
sys.modules["migrate_schema_v2"] = migrate
_spec.loader.exec_module(migrate)


def v1_dataset(root: Path) -> Path:
    """A dataset as schema 1 wrote it: a `jibo` column, and `kanji` for the script of a kanji."""
    root.mkdir()
    units = [
        Unit(id="ne", page_id="p", box=Box(x=0, y=0, w=1, h=1), unicode="U+1B098", script=Script.HENTAIGANA),
        Unit(id="ni", page_id="p", box=Box(x=0, y=0, w=1, h=1), unicode="U+306B", script=Script.HIRAGANA),
        Unit(id="koku", page_id="p", box=Box(x=0, y=0, w=1, h=1), unicode="U+56FD", script=Script.HAN),
    ]
    path = root / "units.parquet"
    tables.write(path, units, Unit)
    table = pq.read_table(path)
    ids = table["id"].to_pylist()
    scripts = {"ne": "hentaigana", "ni": "hiragana", "koku": "kanji"}
    # 子 is what the character layer states for U+1B098; 尓 for に is a source's own reading of the form.
    jibo = {"ne": "子", "ni": "尓", "koku": None}
    table = table.set_column(table.schema.get_field_index("script"), "script", pa.array([scripts[i] for i in ids]))
    pq.write_table(table.append_column("jibo", pa.array([jibo[i] for i in ids])), path)
    (root / tables.MANIFEST_NAME).write_text(json.dumps({"schema_version": 1, "tables": ["units"]}))
    return path


def test_a_schema_1_table_is_refused_and_names_the_migration(tmp_path):
    path = v1_dataset(tmp_path / "old")
    with pytest.raises(tables.SchemaMismatch, match="migrate_schema_v2"):
        tables.read(path, Unit)


def test_the_migration_keeps_a_jibo_the_layer_does_not_state_and_relabels_kanji(tmp_path, capsys):
    path = v1_dataset(tmp_path / "old")
    migrate.main([str(tmp_path), "--apply"])
    units = {unit.id: unit for unit in tables.read(path, Unit)}
    assert "jibo" not in units["ne"].upstream
    assert units["ni"].upstream["jibo"] == "尓"
    assert units["koku"].script is Script.HAN
    manifest = json.loads((path.parent / tables.MANIFEST_NAME).read_text())
    assert manifest["schema_version"] == tables.SCHEMA_VERSION
    capsys.readouterr()
    migrate.main([str(tmp_path), "--apply"])
    assert "already version 2" in capsys.readouterr().out


def test_a_review_store_holding_schema_1_units_is_migrated_and_replays(tmp_path):
    import sqlite3

    from glyph_atlas.review.store import ReviewRequest, Store, replay
    from glyph_atlas.schema import Document, Page

    root = tmp_path / "dataset"
    root.mkdir()
    tables.write(root / "documents.parquet", [Document(id="d", title="t")], Document)
    tables.write(root / "pages.parquet", [Page(id="p", document_id="d", seq=0, image="x",
                 width=10, height=10)], Page)
    tables.write(root / "units.parquet", [
        Unit(id="ni", document_id="d", page_id="p", box=Box(x=0, y=0, w=1, h=1), unicode="U+306B"),
        Unit(id="koku", document_id="d", page_id="p", box=Box(x=0, y=0, w=1, h=1), unicode="U+56FD",
             script=Script.HAN),
    ], Unit)
    Store(root).record(ReviewRequest(target_id="ni", field="reading", new="に", client_id="reviewer"))
    # The store as schema 1 left it: a jibo on the cached unit, and the kanji label.
    with sqlite3.connect(root / "review.sqlite") as conn:
        for unit_id, patch in (("ni", {"jibo": "尓"}), ("koku", {"script": "kanji", "jibo": None})):
            data = json.loads(conn.execute("SELECT data FROM units WHERE id = ?", (unit_id,)).fetchone()[0])
            conn.execute("UPDATE units SET data = ? WHERE id = ?", (json.dumps({**data, **patch}), unit_id))
    with pytest.raises(Exception, match="jibo|kanji"):
        replay(root)

    migrate.main([str(tmp_path), "--apply"])
    replay(root)
    units = {unit.id: unit for unit, _ in Store(root).unit_snapshot()}
    assert units["ni"].reading == "に", "the recorded event replays over the migrated store"
    assert units["koku"].script is Script.HAN


def test_a_split_entry_is_migrated_and_a_candidate_s_jibo_is_left_alone():
    value = {"split": [{"box": {"x": 0, "y": 0, "w": 1, "h": 1}, "unicode": "U+56FD", "script": "kanji",
                        "jibo": "国"}],
             "candidates": [{"unicode": "U+1B002", "p": 1.0, "jibo": "安"}]}
    migrated = migrate.migrate_event_value("segmentation", value)
    assert migrated["split"] == [{"box": {"x": 0, "y": 0, "w": 1, "h": 1}, "unicode": "U+56FD", "script": "han"}]
    assert migrated["candidates"] == value["candidates"]

