"""Migrate datasets written with schema version 5 or earlier to version 6.

Version 6 removed `Unit.reading`: a crop is its character, and the kana a character stands for is
character data (`Character.readings`). For every units table under the given directories, this script:

- writes the readings that said something the unit's transcription and code point do not to an
  archive, one JSON object a line, before anything is dropped;
- drops the `reading` column;
- sets `schema_version` to 6 in the dataset's `MANIFEST.json`.

It does the same for every review store (`review.sqlite`) under the directories: the units the store
holds, the units it imported, and the units and split entries its events carry, because a store
rebuilds units from its events when it replays them. An event that set a reading stays as it was
written: it is history, it moved its unit's revision, and replaying it now changes nothing.

A table or store that needs none of this is left untouched, so the script can run again.

    uv run python scripts/migrate_schema_v6.py work            # report what would change
    uv run python scripts/migrate_schema_v6.py work --apply    # archive, then rewrite

A units table whose written forms or shape ids still hold values goes through
`scripts/migrate_written_forms.py` first. Stop every process that opens a review store first. A store holding events that are not yet in
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


def units_tables(root: Path) -> list[Path]:
    """Every units table under `root`: a `units.parquet` file or a `units/` directory of shards."""
    found = [path for path in root.rglob("units.parquet") if path.is_file()]
    found += [path for path in root.rglob("units") if path.is_dir() and any(path.glob("*.parquet"))]
    return sorted(found)


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


def migrate_table(path: Path) -> int:
    """Rewrite one units table as v6 under its lock; return its number of rows."""
    with tables.locked(path):
        units = [tables._row_to_model({key: value for key, value in row.items() if key != RETIRED}, Unit)
                 for file in tables._table_files(path) for row in pq.read_table(file).to_pylist()]
        return tables._write_unlocked(path, units, Unit, shard=path.is_dir(), command=COMMAND)


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
    return sorted(path for path in root.rglob("review.sqlite") if path.is_file())


def migrate_store(path: Path, *, apply: bool) -> dict[str, int]:
    """Take readings out of the units a review store holds; return how many rows of each kind change.

    The reads and the writes are one transaction, taken before the first read, so a store written in
    between cannot have its new rows replaced by ones read before them. An event whose field is
    `reading` keeps its value: that is what it recorded.
    """
    counts = {"units": 0, "imported": 0, "events": 0}
    uri = f"file:{path}" + ("" if apply else "?mode=ro")
    with closing(sqlite3.connect(uri, uri=True, isolation_level=None)) as conn:
        conn.execute("BEGIN IMMEDIATE" if apply else "BEGIN")
        present = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        updates: list[tuple[str, tuple]] = []
        if "units" in present:
            for unit_id, data in conn.execute("SELECT id, data FROM units"):
                new = _json_rewrite(data)
                if new is not None:
                    counts["units"] += 1
                    updates.append(("UPDATE units SET data = ? WHERE id = ?", (new, unit_id)))
        if "imported_records" in present:
            for record_id, data in conn.execute("SELECT id, data FROM imported_records WHERE table_name = 'units'"):
                new = _json_rewrite(data)
                if new is not None:
                    counts["imported"] += 1
                    updates.append(("UPDATE imported_records SET data = ? WHERE table_name = 'units' AND id = ?",
                                    (new, record_id)))
        if "events" in present:
            for seq, field, old, new in conn.execute("SELECT seq, field, old, new FROM events"):
                if field == RETIRED:
                    continue
                old_after, new_after = _json_rewrite(old), _json_rewrite(new)
                if old_after is not None or new_after is not None:
                    counts["events"] += 1
                    updates.append(("UPDATE events SET old = COALESCE(?, old), new = COALESCE(?, new) WHERE seq = ?",
                                    (old_after, new_after, seq)))
        for statement, parameters in updates if apply else ():
            conn.execute(statement, parameters)
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
    parser.add_argument("--archive", type=Path, default=Path("work/reading-purge/unit-readings.jsonl"),
                        help="where the readings a transcription and a code point do not repeat are kept")
    arguments = parser.parse_args(argv)
    pending = [path for root in arguments.roots for path in units_tables(root) if needs_migration(path)]
    for path in pending:
        if arguments.apply:
            kept = archive_table(path, arguments.archive)
            rows = migrate_table(path)
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
