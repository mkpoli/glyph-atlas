"""Publish the component index the hosted search reads (`han_components`, `han_component_names`) as D1 SQL.

Writes a publication `scripts/publish_cloudflare.sh` uploads: ordered SQL parts under `sql/` and a
`publication.json` naming them, with no R2 objects. The first part empties both tables, so the search
answers no component query until the last part is in; the rest are inserts only.

    .venv/bin/python scripts/export_components_cloudflare.py work/components-<date>
    scripts/publish_cloudflare.sh work/components-<date>

Apply migration 0033 first (`wrangler d1 migrations apply glyph-atlas --remote`).
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from pathlib import Path

from glyph_atlas import han_components, refs

PART_BYTES = 90 * 1024**2
STATEMENT_BYTES = 90_000


def quoted(value) -> str:
    return str(value) if isinstance(value, int) else "'" + str(value).replace("'", "''") + "'"


def statements(table: str, columns: str, rows):
    """Multi-row INSERTs, each under D1's 100 KB statement limit."""
    head = f"INSERT INTO {table}({columns}) VALUES"
    batch: list[str] = []
    size = len(head)
    for row in rows:
        values = "(" + ",".join(quoted(value) for value in row) + ")"
        if batch and size + len(values) + 2 > STATEMENT_BYTES:
            yield head + ",".join(batch) + ";"
            batch, size = [], len(head)
        batch.append(values)
        size += len(values) + 1
    if batch:
        yield head + ",".join(batch) + ";"


def component_rows():
    """(component, tier, size, code point, count, direct) for every character the character table holds."""
    for part, char, n, direct, tier, size in han_components.rows():
        code_point = refs.to_code_point(char)
        if refs.character(code_point) is not None:
            yield part, tier, size, code_point, n, direct


def name_rows(totals: Counter):
    """What a person may type for each component: the component itself, and each radical character
    Unicode unifies with it (⺡ for 氵), with how many characters hold it."""
    for component, total in sorted(totals.items()):
        if component not in han_components.unified():
            yield component, component, total
    for form, unified in sorted(han_components.unified().items()):
        if unified in totals:
            yield form, unified, totals[unified]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("out", type=Path)
    args = parser.parse_args(argv)
    (args.out / "sql").mkdir(parents=True, exist_ok=False)

    rows = sorted(component_rows())
    totals = Counter(row[0] for row in rows)
    names = list(name_rows(totals))
    # The statements are checked against the schema the Worker's migration makes before any is written.
    check = sqlite3.connect(":memory:")
    check.executescript(
        (
            Path(__file__).resolve().parents[1] / "apps/cloudflare/migrations/0033_han_components.sql"
        ).read_text()
    )

    parts: list[str] = []
    handle = None

    def write(statement: str) -> None:
        nonlocal handle
        if handle is None or handle.tell() + len(statement.encode()) > PART_BYTES:
            if handle:
                handle.close()
            parts.append(f"sql/{len(parts) + 1:04}.sql")
            handle = (args.out / parts[-1]).open("w", encoding="utf-8")
        handle.write(statement + "\n")

    write("DELETE FROM han_components;")
    write("DELETE FROM han_component_names;")
    for statement in statements("han_components", "component,tier,size,code_point,n,direct", rows):
        write(statement)
    for statement in statements("han_component_names", "query,component,total", names):
        check.execute(statement)
        write(statement)
    if handle:
        handle.close()
    check.execute(next(statements("han_components", "component,tier,size,code_point,n,direct", rows[:10])))

    counts = {
        "han_components": len(rows),
        "han_component_names": len(names),
        "characters": len({r[3] for r in rows}),
    }
    (args.out / "publication.json").write_text(
        json.dumps({"counts": counts, "objects": [], "sql": parts}, indent=1) + "\n"
    )
    print(json.dumps({"counts": counts, "parts": len(parts)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
