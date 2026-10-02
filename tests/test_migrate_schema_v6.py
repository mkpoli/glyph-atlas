"""A schema-5 units table is refused, and `scripts/migrate_schema_v6.py` takes `Unit.reading` out."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from glyph_atlas import tables
from glyph_atlas.review.store import BadRequest, ReviewRequest, Store, replay
from glyph_atlas.schema import Box, Document, Line, Page, Unit

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("migrate_schema_v6", ROOT / "scripts" / "migrate_schema_v6.py")
assert _spec is not None and _spec.loader is not None
migrate = importlib.util.module_from_spec(_spec)
sys.modules["migrate_schema_v6"] = migrate
_spec.loader.exec_module(migrate)

LINE = "p:l0"
#: The readings schema 5 stored. か repeats the transcription and く the code point; only キ says
#: something neither does.
READINGS = {"ka": "か", "ki": "キ", "ku": "く"}
SPLIT = {"split": [{"box": {"x": 0, "y": 0, "w": 10, "h": 10}, "unicode": "U+304B"},
                   {"box": {"x": 0, "y": 10, "w": 10, "h": 10}, "unicode": "U+304B"}]}


def unit(ident: str, seq: int, text: str, code: str) -> Unit:
    return Unit(id=ident, document_id="d", page_id="p", line_id=LINE, seq=seq,
                box=Box(x=0, y=seq * 20, w=10, h=20), text_source=text, unicode=code)


def v4_dataset(root: Path) -> Path:
    """A dataset and review store as schema 5 left them: `reading` on units, entries and dumps."""
    root.mkdir()
    tables.write(root / "documents.parquet", [Document(id="d", title="t")], Document)
    tables.write(root / "pages.parquet", [Page(id="p", document_id="d", seq=0, image="x",
                 width=100, height=100)], Page)
    tables.write(root / "lines.parquet", [Line(id=LINE, page_id="p", seq=0, text_raw="かき久",
                 text="かき久", box=Box(x=0, y=0, w=50, h=80))], Line)
    path = root / "units.parquet"
    tables.write(path, [unit("ka", 0, "か", "U+304B"), unit("ki", 1, "き", "U+304D"),
                        unit("ku", 2, "久", "U+304F")], Unit)
    store = Store(root)
    store.record(ReviewRequest(target_id="ki", field="text_source", new="き", client_id="reviewer"))
    store.record(ReviewRequest(target_id="ka", field="segmentation", new=SPLIT, client_id="reviewer"))

    table = pq.read_table(path)
    readings = pa.array([READINGS.get(ident) for ident in table["id"].to_pylist()])
    pq.write_table(table.append_column("reading", readings), path)
    (root / tables.MANIFEST_NAME).write_text(json.dumps({"schema_version": 5, "tables": ["units"]}))
    with sqlite3.connect(root / "review.sqlite") as conn:
        for ident, data in conn.execute("SELECT id, data FROM units").fetchall():
            record = {**json.loads(data), "reading": READINGS.get(ident)}
            conn.execute("UPDATE units SET data = ? WHERE id = ?", (json.dumps(record), ident))
        imported = {**unit("ke", 3, "け", "U+3051").model_dump(mode="json"), "reading": "ケ"}
        conn.execute("INSERT INTO imported_records VALUES ('units', 'ke', ?)", (json.dumps(imported),))
        # The first event set a reading; the split carried one on its parent and on an entry.
        conn.execute("UPDATE events SET field = 'reading', new = ? WHERE seq = 1", (json.dumps("キ"),))
        old, new = conn.execute("SELECT old, new FROM events WHERE seq = 2").fetchone()
        old, new = {**json.loads(old), "reading": "か"}, json.loads(new)
        new["split"][0]["reading"] = "か"
        conn.execute("UPDATE events SET old = ?, new = ? WHERE seq = 2", (json.dumps(old), json.dumps(new)))
    return path


def store_rows(root: Path) -> dict[str, list]:
    with sqlite3.connect(root / "review.sqlite") as conn:
        return {
            "units": conn.execute("SELECT id, data FROM units ORDER BY id").fetchall(),
            "imported": conn.execute("SELECT id, data FROM imported_records ORDER BY id").fetchall(),
            "events": conn.execute("SELECT seq, field, old, new FROM events ORDER BY seq").fetchall(),
        }


def test_a_table_with_a_reading_column_is_refused_and_names_the_migration(tmp_path):
    path = v4_dataset(tmp_path / "old")
    with pytest.raises(tables.SchemaMismatch, match="reading.*scripts/migrate_schema_v6.py"):
        tables.read(path, Unit)


def test_a_dry_run_changes_nothing(tmp_path, capsys):
    root = tmp_path / "old"
    path = v4_dataset(root)
    before = (path.read_bytes(), (root / tables.MANIFEST_NAME).read_bytes(), store_rows(root))
    archive = tmp_path / "archive.jsonl"
    assert migrate.main([str(tmp_path), "--archive", str(archive)]) == 0
    out = capsys.readouterr().out
    assert f"would migrate {path}\n" in out and f"would migrate {root / 'review.sqlite'} " in out
    assert (path.read_bytes(), (root / tables.MANIFEST_NAME).read_bytes(), store_rows(root)) == before
    assert not archive.exists()


def test_the_migration_archives_telling_readings_and_drops_the_column(tmp_path, capsys):
    root = tmp_path / "old"
    path = v4_dataset(root)
    archive = tmp_path / "purge" / "readings.jsonl"
    migrate.main([str(tmp_path), "--apply", "--archive", str(archive)])

    kept = [json.loads(line) for line in archive.read_text(encoding="utf-8").splitlines()]
    assert kept == [{"table": str(path), "id": "ki", "reading": "キ", "text_source": "き", "unicode": "U+304D"}]
    assert "reading" not in pq.read_schema(path).names
    assert {unit.id: unit.text_source for unit in tables.read(path, Unit)}["ku"] == "久"
    manifest = json.loads((root / tables.MANIFEST_NAME).read_text())
    assert manifest["schema_version"] == tables.SCHEMA_VERSION == 6

    rows = store_rows(root)
    assert all("reading" not in json.loads(data) for _, data in rows["units"] + rows["imported"])
    (_, field, _, value), (_, _, old, new) = rows["events"]
    assert (field, json.loads(value)) == ("reading", "キ"), "a recorded reading event keeps its value"
    assert "reading" not in json.loads(old)
    assert json.loads(new) == SPLIT

    capsys.readouterr()
    migrate.main([str(tmp_path), "--apply", "--archive", str(archive)])
    out = capsys.readouterr().out
    assert "every units table is already version 6" in out and "every review store is already version 6" in out
    assert len(archive.read_text(encoding="utf-8").splitlines()) == 1


def test_a_migrated_store_opens_and_replays_its_reading_event_as_a_no_op(tmp_path):
    root = tmp_path / "old"
    v4_dataset(root)
    with pytest.raises(Exception, match="reading"):
        replay(root)

    migrate.main([str(tmp_path), "--apply", "--archive", str(tmp_path / "archive.jsonl")])
    assert replay(root)["events"] == 2
    units = {unit.id: (unit, revision) for unit, revision in Store(root).unit_snapshot()}
    ki, revision = units["ki"]
    assert ki.text_source == "き" and ki.unicode == "U+304D"
    assert revision == 1, "the recorded reading event still moves the unit's revision"
    assert not units["ka"][0].active and units["ka"][0].split_into == [f"{LINE}:m1", f"{LINE}:m2"]
    assert units["ke"][0].text_source == "け"
    with pytest.raises(BadRequest, match="reading"):
        Store(root).record(ReviewRequest(target_id="ki", field="reading", new="キ", client_id="reviewer"))


def test_a_table_an_older_schema_wrote_is_left_alone_and_snapshots_are_skipped(tmp_path):
    """A table that still holds `jibo` does not validate as version 6: nothing of it is written or
    archived, the run says so and fails, and a `backups` snapshot is not touched at all."""
    root = tmp_path / "work"
    root.mkdir()
    older = v4_dataset(root / "older")
    table = pq.read_table(older)
    pq.write_table(table.append_column("jibo", pa.array(["加", None, None])), older)
    snapshot = v4_dataset(root / "backups")
    before = {path: path.read_bytes() for path in (older, snapshot)}
    archive = tmp_path / "archive.jsonl"
    assert migrate.main([str(root), "--apply", "--archive", str(archive)]) == 1
    assert {path: path.read_bytes() for path in before} == before
    assert not archive.exists()
