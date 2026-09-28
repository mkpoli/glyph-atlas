"""Write the 異体字 graph into the site's `character_variants` table, as SQL parts D1 can import.

    python scripts/export_character_variants.py OUT

D1 imports each `--file` as one transaction: a part that fails leaves the database as it was. The
graph is too large for one statement, so the parts fill a staging table, `character_variants_next`,
and only the last part swaps it in, bumps the listing version and cites the sources. A failure before
the last part leaves the live table untouched; a rerun starts the staging table again.

OUT/sql/part-NN.sql are the parts in order and OUT/apply.sh imports them, retrying a refused part,
and checks the counts it expects. The full export (`export_cloudflare.py`) fills the same table
through `fill`.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from glyph_atlas import refs

ROOT = Path(__file__).resolve().parents[1]
PART_BYTES = 40 * 1024 * 1024
STATEMENT_BYTES = 90 * 1024
COLUMNS = ("a", "b", "relation", "source", "detail", "written", "widens")
STAGING = "character_variants_next"
# The Worker keys its cached listings and cards on this row; the swap writes it with the new graph.
VERSION_BUMP = ("INSERT OR REPLACE INTO metadata(key,value) VALUES('units_refreshed_at',"
                "json_quote(strftime('%Y-%m-%dT%H:%M:%fZ','now')));\n")


def quote(value) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    return "'" + str(value).replace("'", "''") + "'"


def citations() -> str:
    return json.dumps(refs.variant_sources(), ensure_ascii=False, separators=(",", ":"))


def expected() -> dict[str, int]:
    """What the live table holds once the parts are applied: rows are unique by their key."""
    rows = {(e["a"], e["b"], e["relation"], e["source"]): e for e in refs.variant_edges()}
    return {"edges": len(rows), "written": sum(e["written"] for e in rows.values()),
            "widens": sum(e["widens"] for e in rows.values())}


def statements() -> list[list[str]]:
    """The staging fill, in statements under D1's statement limit, then the swap as the last group."""
    fill = [f"DROP TABLE IF EXISTS {STAGING};\n",
            (f"CREATE TABLE {STAGING} (a TEXT NOT NULL, b TEXT NOT NULL, relation TEXT NOT NULL, source TEXT NOT NULL,"
             " detail TEXT NOT NULL, written INTEGER NOT NULL, widens INTEGER NOT NULL,"
             " PRIMARY KEY(a,b,relation,source)) WITHOUT ROWID;\n")]
    head = f"INSERT OR REPLACE INTO {STAGING}({','.join(COLUMNS)}) VALUES"
    rows, size = [], 0
    for edge in refs.variant_edges():
        row = "(" + ",".join(quote(edge[column]) for column in COLUMNS) + ")"
        if rows and size + len(row.encode()) + 1 > STATEMENT_BYTES - len(head):
            fill.append(head + ",".join(rows) + ";\n")
            rows, size = [], 0
        rows.append(row)
        size += len(row.encode()) + 1
    if rows:
        fill.append(head + ",".join(rows) + ";\n")
    swap = ["DELETE FROM character_variants;\n",
            f"INSERT INTO character_variants({','.join(COLUMNS)}) SELECT {','.join(COLUMNS)} FROM {STAGING};\n",
            f"DROP TABLE {STAGING};\n",
            "INSERT INTO metadata(key,value) VALUES('variant_sources'," + quote(citations())
            + ") ON CONFLICT(key) DO UPDATE SET value=excluded.value;\n",
            VERSION_BUMP]
    return [fill, swap]


def fill(db: sqlite3.Connection) -> None:
    """The same rows in a local catalogue built from the migrations."""
    db.execute("DELETE FROM character_variants")
    db.executemany(f"INSERT OR REPLACE INTO character_variants({','.join(COLUMNS)}) VALUES ({','.join('?' * len(COLUMNS))})",
                   [tuple(int(edge[c]) if c in ("written", "widens") else edge[c] for c in COLUMNS)
                    for edge in refs.variant_edges()])
    db.execute("INSERT OR REPLACE INTO metadata(key,value) VALUES('variant_sources',?)", (citations(),))


def write_parts(out: Path, groups: list[list[str]]) -> list[Path]:
    """The fill split into parts under D1's upload size; the swap always a part of its own, last."""
    directory = out / "sql"
    directory.mkdir(parents=True, exist_ok=True)
    for old in directory.glob("*.sql"):
        old.unlink()
    fill, swap = groups
    parts, part, size = [], [], 0
    for statement in fill:
        length = len(statement.encode())
        if length > STATEMENT_BYTES + 1024:
            raise SystemExit(f"a statement of {length} bytes is over D1's limit")
        if part and size + length > PART_BYTES:
            parts.append(part)
            part, size = [], 0
        part.append(statement)
        size += length
    parts += [part, swap] if part else [swap]
    paths = []
    for index, lines in enumerate(parts, 1):
        path = directory / f"part-{index:02d}.sql"
        path.write_text("".join(lines), encoding="utf-8")
        paths.append(path)
    return paths


APPLY = """#!/usr/bin/env bash
# Replace the site's 異体字 graph (character_variants) with this export. Each part is one D1
# transaction; the parts fill a staging table and the last one swaps it in, so a failure never
# leaves the live table empty. Safe to rerun.
set -uo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
cd ~/projects/Philology/glyph-atlas/apps/cloudflare
count="SELECT count(*) AS edges, coalesce(sum(written),0) AS written, coalesce(sum(widens),0) AS widens FROM character_variants"
q() {{ bunx wrangler d1 execute glyph-atlas --remote --json --command "$1" 2>/dev/null | jq -c '.[0].results[0]'; }}
echo "before: $(q "$count")"
echo "started $(date -u +%Y-%m-%dT%H:%M:%SZ); undo: bunx wrangler d1 time-travel restore glyph-atlas --timestamp=<that time>"
for part in "$here"/sql/part-*.sql; do
  done=0
  for try in 1 2 3 4; do
    if bunx wrangler d1 execute glyph-atlas --remote --yes --file "$part" 2>&1 | tee /dev/stderr | grep -q "Executed"; then done=1; break; fi
    sleep 30
  done
  [ "$done" -eq 1 ] || {{ echo "$(basename "$part") failed four times; the live table is unchanged unless it was the last part. Rerun." >&2; exit 1; }}
done
after=$(q "$count")
echo "after: $after"
[ "$after" = '{expected}' ] || {{ echo 'expected {expected}' >&2; exit 1; }}
echo "done"
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("output", type=Path)
    out = parser.parse_args().output
    groups = statements()
    paths = write_parts(out, groups)
    want = expected()
    apply = out / "apply.sh"
    apply.write_text(APPLY.format(expected=json.dumps(want, separators=(",", ":"))), encoding="utf-8")
    apply.chmod(0o755)
    print(json.dumps({**want, "parts": [str(p.relative_to(out)) for p in paths]}))


if __name__ == "__main__":
    main()
