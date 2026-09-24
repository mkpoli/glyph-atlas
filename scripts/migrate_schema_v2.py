"""Migrate datasets written with schema version 1 to version 2.

Version 2 removed `Unit.jibo`: the 字母 is character metadata now, and `data/vocab/characters.tsv`
states it. It also names the kanji script `han` only. For every units table under the given
directories, this script:

- drops the `jibo` column, and keeps a 字母 the character layer does not state as `upstream["jibo"]`,
  so a source's own reading of the form is not lost;
- relabels the script `kanji` as `han`;
- sets `schema_version` to 2 in the dataset's `MANIFEST.json`.

A table that needs none of this is left untouched, so the script can run again.

    uv run python scripts/migrate_schema_v2.py work            # report what would change
    uv run python scripts/migrate_schema_v2.py work --apply    # rewrite

A review store beside a migrated table reloads the table the next time it opens and replays its
events. A store holding events that are not yet in `reviews.jsonl` refuses to open instead, so run
`atlas review apply` on it before migrating.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from glyph_atlas import refs, tables
from glyph_atlas.schema import Unit

OLD_SCRIPT = "kanji"


def units_tables(root: Path) -> list[Path]:
    """Every units table under `root`: a `units.parquet` file or a `units/` directory of shards."""
    found = [path for path in root.rglob("units.parquet") if path.is_file()]
    found += [path for path in root.rglob("units") if path.is_dir() and any(path.glob("*.parquet"))]
    return sorted(found)


def needs_migration(path: Path) -> bool:
    """Whether a units table still has the `jibo` column or the `kanji` script label."""
    for file in tables._table_files(path):
        schema = pq.read_schema(file)
        if "jibo" in schema.names:
            return True
        if "script" in schema.names and OLD_SCRIPT in pq.read_table(file, columns=["script"])["script"].to_pylist():
            return True
    return False


def migrate_row(row: dict) -> Unit:
    """One v1 row as a v2 unit."""
    jibo = row.pop("jibo", None)
    if row.get("script") == OLD_SCRIPT:
        row["script"] = "han"
    unit = tables._row_to_model(row, Unit)
    if jibo and jibo != refs.jibo_of_unit(unit.unicode) and "jibo" not in unit.upstream:
        unit.upstream["jibo"] = jibo
    return unit


def migrate_table(path: Path) -> int:
    """Rewrite one units table as v2 under its lock; return its number of rows."""
    with tables.locked(path):
        units = [migrate_row(row) for file in tables._table_files(path) for row in pq.read_table(file).to_pylist()]
        return tables._write_unlocked(path, units, Unit, shard=path.is_dir(), command="scripts/migrate_schema_v2.py")


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
    parser.add_argument("--apply", action="store_true", help="rewrite the tables; without it, only report")
    arguments = parser.parse_args(argv)
    pending = [path for root in arguments.roots for path in units_tables(root) if needs_migration(path)]
    for path in pending:
        if arguments.apply:
            rows = migrate_table(path)
            manifest = " and its manifest" if migrate_manifest(path.parent) else ""
            print(f"migrated {path}{manifest} ({rows} units)")
        else:
            print(f"would migrate {path}")
    if not pending:
        print("every units table is already version 2")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
