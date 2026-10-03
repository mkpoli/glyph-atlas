"""Migrate datasets written with schema version 5 or earlier to version 6.

Version 6 removed `Unit.reading`: a crop is its character, and the kana a character stands for is
character data (`Character.readings`). For every units table under the given directories, this script:

- writes the readings that said something the unit's transcription and code point do not to an
  archive, one JSON object a line, before anything is dropped;
- drops the `reading` column;
- sets `schema_version` to 6 in the dataset's `MANIFEST.json`.

It does the same for every review store (`review.sqlite`) under the directories: the units the store
holds, the units it imported, and the units and split entries its events carry and answered with,
because a store rebuilds units from its events when it replays them. An event that set a reading
stays as it was written: it is history, it moved its unit's revision, and replaying it now changes nothing.

Every table is read and validated before anything is written. A table that does not validate as
version 6 once its readings are dropped is reported and left alone: one still holding `jibo` goes
through `scripts/migrate_schema_v2.py` first, and one whose written forms or shape ids hold values
through `scripts/migrate_written_forms.py`. Directories named `backups` are snapshots and
are skipped. A table or store that needs none of this is left untouched, so the script can run again.

    uv run python scripts/migrate_schema_v6.py work            # report what would change
    uv run python scripts/migrate_schema_v6.py work --apply    # archive, then rewrite

Stop every process that opens a review store first. A store holding events that are not yet in
`reviews.jsonl` refuses to open after its tables change, so run `atlas review apply` on it before
migrating.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import unicodedata
from contextlib import closing
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from glyph_atlas import tables
from glyph_atlas.schema import Unit

RETIRED = "reading"
COMMAND = "scripts/migrate_schema_v6.py"
SNAPSHOTS = "backups"
BATCH = 5000


def _live(path: Path, root: Path) -> bool:
    return SNAPSHOTS not in path.relative_to(root).parts


def units_tables(root: Path) -> list[Path]:
    """Every units table under `root` outside a snapshot directory: a `units.parquet` file or a
    `units/` directory of shards."""
    found = [path for path in root.rglob("units.parquet") if path.is_file()]
    found += [path for path in root.rglob("units") if path.is_dir() and any(path.glob("*.parquet"))]
    return sorted(path for path in found if _live(path, root))


def needs_migration(path: Path) -> bool:
    """Whether a units table still has the `reading` column."""
    return any(RETIRED in pq.read_schema(file).names for file in tables._table_files(path))


def _character(unicode: str | None) -> str | None:
    try:
        return "".join(chr(int(point.upper().removeprefix("U+"), 16)) for point in unicode.split()) if unicode else None
    except ValueError:
        return None


def telling(row: dict) -> bool:
    """Whether a row's reading says something its transcription and code point do not."""
    reading = row.get(RETIRED)
    if not reading:
        return False
    text = unicodedata.normalize("NFC", reading)
    return text not in (row.get("text_source"), _character(row.get("unicode")))


def archive_table(path: Path, archive: Path) -> int:
    """Write the telling readings of one table to `archive`; return how many there were."""
    rows = []
    for file in tables._table_files(path):
        columns = [name for name in ("id", RETIRED, "text_source", "unicode") if name in pq.read_schema(file).names]
        rows += [row for row in pq.read_table(file, columns=columns).to_pylist() if telling(row)]
    if rows:
        archive.parent.mkdir(parents=True, exist_ok=True)
        with archive.open("a", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps({"table": str(path), **row}, ensure_ascii=False) + "\n")
    return len(rows)


def read_v6(path: Path) -> list[Unit]:
    """Every row of one table as a v6 unit; raises when a row does not validate as one."""
    return [tables._row_to_model({key: value for key, value in row.items() if key != RETIRED}, Unit)
            for file in tables._table_files(path) for row in pq.read_table(file).to_pylist()]


def migrate_table(path: Path, archive: Path) -> tuple[int, int]:
    """Archive one table's telling readings and rewrite it as v6 under its lock.

    Returns its number of rows and of archived readings. The rows are validated before the archive
    is written, so a table that cannot be migrated leaves neither.
    """
    with tables.locked(path):
        units = read_v6(path)
        kept = archive_table(path, archive)
        return tables._write_unlocked(path, units, Unit, shard=path.is_dir(), command=COMMAND), kept


def without_reading(value: Any) -> Any:
    """`value` with `reading` taken out of every unit and split entry it holds."""
    if isinstance(value, dict):
        record = isinstance(value.get("id"), str) or "box" in value
        return {key: without_reading(item) for key, item in value.items() if not (record and key == RETIRED)}
    if isinstance(value, list):
        return [without_reading(item) for item in value]
    return value


def _json_rewrite(text: str | None) -> str | None:
    """`text` without readings, or None when nothing changes."""
    if text is None:
        return None
    try:
        value = json.loads(text)
    except ValueError:
        return None
    changed = without_reading(value)
    return None if changed == value else json.dumps(changed, ensure_ascii=False, sort_keys=True)


def review_stores(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("review.sqlite") if path.is_file() and _live(path, root))


def _rows(conn: sqlite3.Connection, sql: str):
    """`sql`, which selects the rowid first and ends in `rowid > ?`, in batches of `BATCH` rows."""
    last = -1
    while True:
        rows = conn.execute(sql + " ORDER BY rowid LIMIT ?", (last, BATCH)).fetchall()
        if not rows:
            return
        yield from rows
        last = rows[-1][0]


def migrate_store(path: Path, *, apply: bool) -> dict[str, int]:
    """Take readings out of the units a review store holds; return how many rows of each kind change.

    The reads and the writes are one transaction, taken before the first read, so a store written in
    between cannot have its new rows replaced by ones read before them. Rows are read and rewritten
    in batches, so a store of a million imported rows is not held in memory. An event whose field is
    `reading` keeps its value: that is what it recorded.
    """
    counts = {"units": 0, "imported": 0, "events": 0}
    uri = f"file:{path}" + ("" if apply else "?mode=ro")
    with closing(sqlite3.connect(uri, uri=True, isolation_level=None)) as conn:
        conn.execute("BEGIN IMMEDIATE" if apply else "BEGIN")
        present = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}

        def write(statement: str, parameters: tuple) -> None:
            if apply:
                conn.execute(statement, parameters)

        if "units" in present:
            for rowid, data in _rows(conn, "SELECT rowid, data FROM units WHERE rowid > ?"):
                new = _json_rewrite(data)
                if new is not None:
                    counts["units"] += 1
                    write("UPDATE units SET data = ? WHERE rowid = ?", (new, rowid))
        if "imported_records" in present:
            for rowid, name, data in _rows(conn, "SELECT rowid, table_name, data FROM imported_records WHERE rowid > ?"):
                new = _json_rewrite(data) if name == "units" else None
                if new is not None:
                    counts["imported"] += 1
                    write("UPDATE imported_records SET data = ? WHERE rowid = ?", (new, rowid))
        if "events" in present:
            for seq, field, old, new, result in _rows(conn, "SELECT seq, field, old, new, result FROM events WHERE seq > ?"):
                if field == RETIRED:
                    continue
                after = (_json_rewrite(old), _json_rewrite(new), _json_rewrite(result))
                if any(value is not None for value in after):
                    counts["events"] += 1
                    write("UPDATE events SET old = COALESCE(?, old), new = COALESCE(?, new), "
                          "result = COALESCE(?, result) WHERE seq = ?", (*after, seq))
        conn.execute("COMMIT")
    return counts


def migrate_manifest(dataset: Path) -> bool:
    """Set the dataset manifest's schema version; return whether it changed."""
    target = dataset / tables.MANIFEST_NAME
    if not target.is_file():
        return False
    manifest = json.loads(target.read_text(encoding="utf-8"))
    if manifest.get("schema_version") == tables.SCHEMA_VERSION:
        return False
    manifest["schema_version"] = tables.SCHEMA_VERSION
    target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("roots", nargs="+", type=Path, help="directories to search for datasets")
    parser.add_argument("--apply", action="store_true", help="archive and rewrite; without it, only report")
    parser.add_argument("--archive", type=Path, default=ROOT / "work/reading-purge/unit-readings.jsonl",
                        help="where the readings a transcription and a code point do not repeat are kept")
    arguments = parser.parse_args(argv)
    pending = [path for root in arguments.roots for path in units_tables(root) if needs_migration(path)]
    refused: dict[Path, str] = {}
    for path in pending:
        try:
            read_v6(path)
        except ValueError as error:
            refused[path] = str(error).splitlines()[0]
    for path, reason in refused.items():
        print(f"left unchanged: {path} does not validate as version 6 ({reason})", file=sys.stderr)
    for path in (path for path in pending if path not in refused):
        if arguments.apply:
            rows, kept = migrate_table(path, arguments.archive)
            manifest = " and its manifest" if migrate_manifest(path.parent) else ""
            print(f"migrated {path}{manifest} ({rows} units, {kept} readings archived)")
        else:
            print(f"would migrate {path}")
    if not pending:
        print("every units table is already version 6")
    stores = 0
    for root in arguments.roots:
        for store in review_stores(root):
            counts = migrate_store(store, apply=arguments.apply)
            if any(counts.values()):
                stores += 1
                verb = "migrated" if arguments.apply else "would migrate"
                print(f"{verb} {store} ({counts['units']} units, {counts['imported']} imported, "
                      f"{counts['events']} events)")
    if not stores:
        print("every review store is already version 6")
    return 1 if refused else 0


if __name__ == "__main__":
    raise SystemExit(main())
