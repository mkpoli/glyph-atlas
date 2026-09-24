"""Migrate datasets written with schema version 1 to version 2.

Version 2 removed `Unit.jibo`: the 字母 is character metadata now, and `data/vocab/characters.tsv`
states it. It also names the kanji script `han` only. For every units table under the given
directories, this script:

- drops the `jibo` column, and keeps a 字母 the character layer does not state as `upstream["jibo"]`,
  so a source's own reading of the form is not lost;
- relabels the script `kanji` as `han`;
- sets `schema_version` to 2 in the dataset's `MANIFEST.json`.

It does the same for every review store (`review.sqlite`) under the directories: the units the store
holds, the units it imported, and the units and script values its events carry, because a store
rebuilds units from its events when it replays them. What an event recorded as evidence, and the
answer it returned, are history and stay as they were written.

A table or store that needs none of this is left untouched, so the script can run again.

    uv run python scripts/migrate_schema_v2.py work            # report what would change
    uv run python scripts/migrate_schema_v2.py work --apply    # rewrite

Stop every process that opens a review store first. A store holding events that are not yet in
`reviews.jsonl` refuses to open after its tables change, so run `atlas review apply` on it before
migrating.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from typing import Any

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


def migrate_unit_data(data: dict[str, Any]) -> dict[str, Any]:
    """One stored unit, as JSON, in version 2: the same rule `migrate_row` applies to a table row."""
    data = dict(data)
    jibo = data.pop("jibo", None)
    if data.get("script") == OLD_SCRIPT:
        data["script"] = "han"
    if jibo and jibo != refs.jibo_of_unit(data.get("unicode")):
        upstream = dict(data.get("upstream") or {})
        upstream.setdefault("jibo", jibo)
        data["upstream"] = upstream
    return data


def _is_unit(value: Any) -> bool:
    return isinstance(value, dict) and "id" in value and ("jibo" in value or value.get("script") == OLD_SCRIPT)


def _is_split_entry(value: Any) -> bool:
    """One child of a split, as a segmentation event records it: a box and what it holds, no id."""
    return isinstance(value, dict) and "box" in value and "id" not in value


def migrate_event_value(field: str, value: Any) -> Any:
    """An event's `old` or `new` in version 2: its units migrated, and `kanji` as a script value renamed.

    A split entry is migrated too, since replaying the split validates it, but it keeps no 字母 in
    `upstream`: an entry cannot set that field. A candidate's or a confidence's `jibo` is a field of
    version 2 and is left alone.
    """
    if field == "script" and value == OLD_SCRIPT:
        return "han"
    if _is_unit(value):
        return migrate_unit_data(value)
    if _is_split_entry(value):
        entry = {key: item for key, item in value.items() if key != "jibo"}
        if entry.get("script") == OLD_SCRIPT:
            entry["script"] = "han"
        return entry
    if isinstance(value, dict):
        return {key: migrate_event_value("", item) for key, item in value.items()}
    if isinstance(value, list):
        return [migrate_event_value("", item) for item in value]
    return value


def _json_rewrite(text: str | None, rewrite) -> str | None:
    """`text` with `rewrite` applied to its decoded value, or None when nothing changes."""
    if text is None:
        return None
    try:
        value = json.loads(text)
    except ValueError:
        return None
    changed = rewrite(value)
    return None if changed == value else json.dumps(changed, ensure_ascii=False, sort_keys=True)


def review_stores(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("review.sqlite") if path.is_file())


class JiboEvents(RuntimeError):
    """A store records 字母 decisions of its own, which version 2 has no field for."""


def migrate_store(path: Path, *, apply: bool) -> dict[str, int]:
    """Rewrite the v1 units a review store holds; return how many rows of each kind change.

    The reads and the writes are one transaction, taken before the first read, so a store written in
    between cannot have its new rows replaced by ones read before them.
    """
    counts = {"units": 0, "imported": 0, "events": 0}
    uri = f"file:{path}" + ("" if apply else "?mode=ro")
    with closing(sqlite3.connect(uri, uri=True, isolation_level=None)) as conn:
        conn.execute("BEGIN IMMEDIATE" if apply else "BEGIN")
        tables_present = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        updates: list[tuple[str, tuple]] = []
        if "units" in tables_present:
            for unit_id, data in conn.execute("SELECT id, data FROM units"):
                new = _json_rewrite(data, lambda value: migrate_unit_data(value) if _is_unit(value) else value)
                if new is not None:
                    counts["units"] += 1
                    updates.append(("UPDATE units SET data = ? WHERE id = ?", (new, unit_id)))
        if "imported_records" in tables_present:
            rows = conn.execute("SELECT id, data FROM imported_records WHERE table_name = 'units'")
            for record_id, data in rows:
                new = _json_rewrite(data, lambda value: migrate_unit_data(value) if _is_unit(value) else value)
                if new is not None:
                    counts["imported"] += 1
                    updates.append(("UPDATE imported_records SET data = ? WHERE table_name = 'units' AND id = ?",
                                    (new, record_id)))
        if "events" in tables_present:
            decided = conn.execute("SELECT COUNT(*) FROM events WHERE field = 'jibo'").fetchone()[0]
            if decided:
                # A reviewer chose a 字母 for these units. The character layer states the 字母 of a
                # code point now, so there is no field to replay them onto; a person decides.
                conn.execute("ROLLBACK")
                raise JiboEvents(f"{path} records {decided} jibo decisions; migrate them by hand first")
            for seq, field, old, new in conn.execute("SELECT seq, field, old, new FROM events"):
                old_after = _json_rewrite(old, lambda value, field=field: migrate_event_value(field, value))
                new_after = _json_rewrite(new, lambda value, field=field: migrate_event_value(field, value))
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
    stores = 0
    refused = 0
    for root in arguments.roots:
        for store in review_stores(root):
            try:
                counts = migrate_store(store, apply=arguments.apply)
            except JiboEvents as error:
                refused += 1
                print(f"left unchanged: {error}", file=sys.stderr)
                continue
            if any(counts.values()):
                stores += 1
                verb = "migrated" if arguments.apply else "would migrate"
                print(f"{verb} {store} ({counts['units']} units, {counts['imported']} imported, "
                      f"{counts['events']} events)")
    if not stores and not refused:
        print("every review store is already version 2")
    return 1 if refused else 0


if __name__ == "__main__":
    raise SystemExit(main())
