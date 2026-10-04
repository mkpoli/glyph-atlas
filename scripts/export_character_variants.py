"""Write the 異体字 graph, its derived tier and the spellings of words into the site's D1 tables, as SQL
parts D1 can import.

    python scripts/export_character_variants.py OUT

D1 imports each `--file` as one transaction: a part that fails leaves the database as it was. The
graph is too large for one statement, so the parts fill staging tables (`character_variants_next`,
`component_variants_next`, `character_derived_next`, `words_next`, `word_spellings_next`) and only the
last part swaps them in, bumps the listing version and cites the sources. A failure before the last part leaves the live tables
untouched; a rerun starts the staging tables again.

`character_variants` is the 異体字 graph itself. The derived tier is `component_variants` (each
attested substitution with its count and pairs) and `character_derived` (each character's derived
list, ranked as `refs.derived_variants` lists it), read through `refs.derived_rows`, which takes
about half a minute and is computed once per run. `words` and `word_spellings` are the hand tables of
decision 0004, read through `refs.words` and `refs.word_spellings`, which joins each 振り仮名 row to its
counts; a character card shows them beside its variants.

OUT/sql/part-NN.sql are the parts in order and OUT/apply.sh imports them, retrying a refused part,
and checks the counts it expects. The full export (`export_cloudflare.py`) fills the same tables
through `fill`.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from functools import cache
from pathlib import Path

from glyph_atlas import refs

ROOT = Path(__file__).resolve().parents[1]
PART_BYTES = 40 * 1024 * 1024
STATEMENT_BYTES = 90 * 1024
COLUMNS = ("a", "b", "relation", "source", "detail", "written", "widens")
STAGING = "character_variants_next"
SUBSTITUTIONS_STAGING = "component_variants_next"
DERIVED_STAGING = "character_derived_next"
DERIVED_COLUMNS = ("a", "rank", "b", "subs")
WORDS_STAGING = "words_next"
WORD_COLUMNS = ("id", "language", "reading", "class")
SPELLINGS_STAGING = "word_spellings_next"
SPELLING_COLUMNS = ("word", "spelling", "source", "locator", "tier", "word_by", "basis", "related", "statement",
                    "documents", "occurrences")
# The Worker keys its cached listings and cards on this row; the swap writes it with the new graph.
VERSION_BUMP = ("INSERT OR REPLACE INTO metadata(key,value) VALUES('units_refreshed_at',"
                "json_quote(strftime('%Y-%m-%dT%H:%M:%fZ','now')));\n")


def quote(value) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if value is None:
        return "NULL"
    if isinstance(value, int):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


@cache
def derived_rows() -> tuple[tuple[str, int, str, str], ...]:
    """Every row of the derived tier (refs.derived_rows), computed once per run."""
    return tuple(refs.derived_rows())


def citations() -> str:
    """Every source a row of any table may cite, the derived tier's own `derived-ids` included."""
    return json.dumps({**refs.variant_sources(), **refs.component_variant_sources()},
                      ensure_ascii=False, separators=(",", ":"))


def word_citations() -> str:
    """Every source a word or a spelling cites."""
    return json.dumps({**refs.word_sources(), **refs.word_spelling_sources()}, ensure_ascii=False, separators=(",", ":"))


def word_rows() -> list[tuple]:
    return [tuple(row[column] for column in WORD_COLUMNS) for row in refs.words().values()]


def spelling_rows() -> list[tuple]:
    return [tuple(row.get(column) for column in SPELLING_COLUMNS) for row in refs.word_spellings()]


def expected() -> dict[str, int]:
    """What the live tables hold once the parts are applied: rows are unique by their key."""
    rows = {(e["a"], e["b"], e["relation"], e["source"]): e for e in refs.variant_edges()}
    return {"edges": len(rows), "written": sum(e["written"] for e in rows.values()),
            "widens": sum(e["widens"] for e in rows.values()),
            "substitutions": len(refs.component_variants()), "derived": len(derived_rows()),
            "words": len(word_rows()), "spellings": len(spelling_rows())}


def insert_rows(into: str, columns: tuple[str, ...], rows) -> list[str]:
    """Multi-row INSERTs, each statement under D1's limit; `rows` are the values in column order."""
    head = f"INSERT OR REPLACE INTO {into}({','.join(columns)}) VALUES"
    statements, batch, size = [], [], 0
    for row in rows:
        values = "(" + ",".join(quote(value) for value in row) + ")"
        if batch and size + len(values.encode()) + 1 > STATEMENT_BYTES - len(head):
            statements.append(head + ",".join(batch) + ";\n")
            batch, size = [], 0
        batch.append(values)
        size += len(values.encode()) + 1
    if batch:
        statements.append(head + ",".join(batch) + ";\n")
    return statements


def statements() -> list[list[str]]:
    """The staging fill, in statements under D1's statement limit, then the swap as the last group."""
    fill = [
        f"DROP TABLE IF EXISTS {STAGING};\n",
        (f"CREATE TABLE {STAGING} (a TEXT NOT NULL, b TEXT NOT NULL, relation TEXT NOT NULL, source TEXT NOT NULL,"
         " detail TEXT NOT NULL, written INTEGER NOT NULL, widens INTEGER NOT NULL,"
         " PRIMARY KEY(a,b,relation,source)) WITHOUT ROWID;\n"),
        f"DROP TABLE IF EXISTS {SUBSTITUTIONS_STAGING};\n",
        (f"CREATE TABLE {SUBSTITUTIONS_STAGING} (a TEXT NOT NULL, b TEXT NOT NULL, count INTEGER NOT NULL,"
         " pairs TEXT NOT NULL, PRIMARY KEY(a,b)) WITHOUT ROWID;\n"),
        f"DROP TABLE IF EXISTS {DERIVED_STAGING};\n",
        (f"CREATE TABLE {DERIVED_STAGING} (a TEXT NOT NULL, rank INTEGER NOT NULL, b TEXT NOT NULL,"
         " subs TEXT NOT NULL, PRIMARY KEY(a,rank)) WITHOUT ROWID;\n"),
        f"DROP TABLE IF EXISTS {WORDS_STAGING};\n",
        (f"CREATE TABLE {WORDS_STAGING} (id TEXT PRIMARY KEY, language TEXT NOT NULL, reading TEXT NOT NULL,"
         " class TEXT NOT NULL) WITHOUT ROWID;\n"),
        f"DROP TABLE IF EXISTS {SPELLINGS_STAGING};\n",
        (f"CREATE TABLE {SPELLINGS_STAGING} (word TEXT NOT NULL, spelling TEXT NOT NULL, source TEXT NOT NULL,"
         " locator TEXT NOT NULL, tier TEXT NOT NULL, word_by TEXT NOT NULL, basis TEXT NOT NULL, related TEXT NOT NULL,"
         " statement TEXT NOT NULL, documents INTEGER, occurrences INTEGER,"
         " PRIMARY KEY(word,spelling,source,locator)) WITHOUT ROWID;\n"),
    ]
    fill += insert_rows(STAGING, COLUMNS, (tuple(edge[column] for column in COLUMNS)
                                            for edge in refs.variant_edges()))
    fill += insert_rows(SUBSTITUTIONS_STAGING, ("a", "b", "count", "pairs"), (
        (left, right, item["count"], json.dumps(item["pairs"], ensure_ascii=False, separators=(",", ":")))
        for (left, right), item in sorted(refs.component_variants().items())))
    fill += insert_rows(DERIVED_STAGING, DERIVED_COLUMNS, derived_rows())
    fill += insert_rows(WORDS_STAGING, WORD_COLUMNS, word_rows())
    fill += insert_rows(SPELLINGS_STAGING, SPELLING_COLUMNS, spelling_rows())
    swap = []
    for live, staging, columns in (
        ("character_variants", STAGING, COLUMNS),
        ("component_variants", SUBSTITUTIONS_STAGING, ("a", "b", "count", "pairs")),
        ("character_derived", DERIVED_STAGING, DERIVED_COLUMNS),
        ("words", WORDS_STAGING, WORD_COLUMNS),
        ("word_spellings", SPELLINGS_STAGING, SPELLING_COLUMNS),
    ):
        swap += [f"DELETE FROM {live};\n",
                 f"INSERT INTO {live}({','.join(columns)}) SELECT {','.join(columns)} FROM {staging};\n",
                 f"DROP TABLE {staging};\n"]
    swap += [
        "INSERT INTO metadata(key,value) VALUES('variant_sources'," + quote(citations())
        + ") ON CONFLICT(key) DO UPDATE SET value=excluded.value;\n",
        "INSERT INTO metadata(key,value) VALUES('word_sources'," + quote(word_citations())
        + ") ON CONFLICT(key) DO UPDATE SET value=excluded.value;\n",
        VERSION_BUMP]
    return [fill, swap]


def fill(db: sqlite3.Connection) -> None:
    """The same rows in a local catalogue built from the migrations."""
    db.execute("DELETE FROM character_variants")
    db.executemany(f"INSERT OR REPLACE INTO character_variants({','.join(COLUMNS)}) VALUES ({','.join('?' * len(COLUMNS))})",
                   [tuple(int(edge[c]) if c in ("written", "widens") else edge[c] for c in COLUMNS)
                    for edge in refs.variant_edges()])
    db.execute("DELETE FROM component_variants")
    db.executemany("INSERT OR REPLACE INTO component_variants(a,b,count,pairs) VALUES (?,?,?,?)",
                   [(left, right, item["count"], json.dumps(item["pairs"], ensure_ascii=False, separators=(",", ":")))
                    for (left, right), item in refs.component_variants().items()])
    db.execute("DELETE FROM character_derived")
    db.executemany("INSERT OR REPLACE INTO character_derived(a,rank,b,subs) VALUES (?,?,?,?)", derived_rows())
    db.execute("INSERT OR REPLACE INTO metadata(key,value) VALUES('variant_sources',?)", (citations(),))
    db.execute("DELETE FROM words")
    db.executemany(f"INSERT INTO words({','.join(WORD_COLUMNS)}) VALUES ({','.join('?' * len(WORD_COLUMNS))})", word_rows())
    db.execute("DELETE FROM word_spellings")
    db.executemany(f"INSERT INTO word_spellings({','.join(SPELLING_COLUMNS)}) VALUES ({','.join('?' * len(SPELLING_COLUMNS))})",
                   spelling_rows())
    db.execute("INSERT OR REPLACE INTO metadata(key,value) VALUES('word_sources',?)", (word_citations(),))


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
# Replace the site's variant and word tables (character_variants, component_variants, character_derived,
# words, word_spellings) with this export. Each part is one D1 transaction; the parts fill staging tables and the last one
# swaps them in, so a failure never leaves the live tables empty. Safe to rerun.
set -uo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
cd ~/projects/Philology/glyph-atlas/apps/cloudflare
count="SELECT (SELECT count(*) FROM character_variants) AS edges, (SELECT count(*) FROM character_variants WHERE written=1) AS written, (SELECT count(*) FROM character_variants WHERE widens=1) AS widens, (SELECT count(*) FROM component_variants) AS substitutions, (SELECT count(*) FROM character_derived) AS derived, (SELECT count(*) FROM words) AS words, (SELECT count(*) FROM word_spellings) AS spellings"
q() {{ bunx wrangler d1 execute glyph-atlas --remote --json --command "$1" 2>/dev/null | jq -c '.[0].results[0]'; }}
echo "before: $(q "$count")"
echo "started $(date -u +%Y-%m-%dT%H:%M:%SZ); undo: bunx wrangler d1 time-travel restore glyph-atlas --timestamp=<that time>"
for part in "$here"/sql/part-*.sql; do
  ../../scripts/d1_import.sh "$part" || {{ echo "$(basename "$part") did not apply; the live table is unchanged unless it was the last part. Rerun." >&2; exit 1; }}
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
