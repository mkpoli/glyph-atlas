"""Write the 異体字 graph into the site's `character_variants` table, as SQL parts D1 can import.

    python scripts/export_character_variants.py OUT

OUT/sql/part-NN.sql replace the whole table with data/vocab/kanji-variants.tsv (every edge, each with its
relation, source and claims, and `written` from refs.WRITTEN_FOR) and write each source's citation to
metadata `variant_sources`. OUT/apply.sh imports them in order; it is safe to rerun. The full export
(`export_cloudflare.py`) fills the same table through `fill`.
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
COLUMNS = ("a", "b", "relation", "source", "detail", "written")


def quote(value) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    return "'" + str(value).replace("'", "''") + "'"


def statements() -> list[str]:
    """Clear the table, insert every edge in statements under D1's statement limit, cite the sources."""
    out = ["DELETE FROM character_variants;\n"]
    head, rows, size = "INSERT OR REPLACE INTO character_variants(a,b,relation,source,detail,written) VALUES", [], 0
    for edge in refs.variant_edges():
        row = "(" + ",".join(quote(edge[column]) for column in COLUMNS) + ")"
        if rows and size + len(row.encode()) + 1 > STATEMENT_BYTES - len(head):
            out.append(head + ",".join(rows) + ";\n")
            rows, size = [], 0
        rows.append(row)
        size += len(row.encode()) + 1
    if rows:
        out.append(head + ",".join(rows) + ";\n")
    citations = json.dumps(refs.variant_sources(), ensure_ascii=False, separators=(",", ":"))
    out.append("INSERT INTO metadata(key,value) VALUES('variant_sources'," + quote(citations)
               + ") ON CONFLICT(key) DO UPDATE SET value=excluded.value;\n")
    return out


def fill(db: sqlite3.Connection) -> None:
    """The same rows in a local catalogue built from the migrations."""
    db.execute("DELETE FROM character_variants")
    db.executemany("INSERT OR REPLACE INTO character_variants VALUES (?,?,?,?,?,?)",
                   [tuple(int(edge[c]) if c == "written" else edge[c] for c in COLUMNS) for edge in refs.variant_edges()])
    db.execute("INSERT OR REPLACE INTO metadata(key,value) VALUES('variant_sources',?)",
               (json.dumps(refs.variant_sources(), ensure_ascii=False, separators=(",", ":")),))


def write_parts(out: Path, found: list[str]) -> list[Path]:
    directory = out / "sql"
    directory.mkdir(parents=True, exist_ok=True)
    for old in directory.glob("*.sql"):
        old.unlink()
    parts, part, size = [], [], 0
    for statement in found:
        length = len(statement.encode())
        if length > STATEMENT_BYTES + 1024:
            raise SystemExit(f"a statement of {length} bytes is over D1's limit")
        if part and size + length > PART_BYTES:
            parts.append(part)
            part, size = [], 0
        part.append(statement)
        size += length
    if part:
        parts.append(part)
    paths = []
    for index, lines in enumerate(parts, 1):
        path = directory / f"part-{index:02d}.sql"
        path.write_text("".join(lines), encoding="utf-8")
        paths.append(path)
    return paths


APPLY = """#!/usr/bin/env bash
# Replace the site's 異体字 graph (character_variants) with this export. Safe to rerun.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
cd {cloudflare}
q() {{ bunx wrangler d1 execute glyph-atlas --remote --json --command "$1" 2>/dev/null | jq -c '.[0].results'; }}
echo "before: $(q "SELECT count(*) AS edges, sum(written) AS written FROM character_variants")"
echo "started $(date -u +%Y-%m-%dT%H:%M:%SZ); undo: bunx wrangler d1 time-travel restore glyph-atlas --timestamp=<that time>"
for part in "$here"/sql/part-*.sql; do
  for try in 1 2 3 4; do
    bunx wrangler d1 execute glyph-atlas --remote --yes --file "$part" 2>&1 | grep -E "Executed|ERROR" && break
    sleep 30
  done
done
echo "after: $(q "SELECT count(*) AS edges, sum(written) AS written FROM character_variants")"
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("output", type=Path)
    out = parser.parse_args().output
    found = statements()
    paths = write_parts(out, found)
    apply = out / "apply.sh"
    apply.write_text(APPLY.format(cloudflare=ROOT / "apps" / "cloudflare"), encoding="utf-8")
    apply.chmod(0o755)
    print(json.dumps({"edges": len(refs.variant_edges()), "statements": len(found),
                      "parts": [str(p.relative_to(out)) for p in paths]}))


if __name__ == "__main__":
    main()
