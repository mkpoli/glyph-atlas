"""Write the 異体字 graph, its derived tier and the spellings of words into the site's D1 tables, as SQL
parts D1 can import.

    python scripts/export_character_variants.py OUT

D1 imports each `--file` as one transaction: a part that fails leaves the database as it was. The
graph is too large for one statement, so its parts fill staging tables (`character_variants_next`,
`component_variants_next`, `words_next`, `word_spellings_next`) and one part swaps them in, bumps the
listing version and cites the sources. A failure before that part leaves the live tables untouched;
a rerun starts the staging tables again.

`character_variants` is the 異体字 graph itself. The derived tier is `component_variants` (each
substitution with its count and pairs) and `character_derived`: one row per character, its derived
list as `refs.derived_variants` ranks it, each form with its routes (`refs.derived_row`). Up to two
substitutions per form make that about half an hour of work on one processor, spread over the
processors and computed once per run. `character_derived` is too large to copy from staging in one
import (110 MB on 2026-10-04), so it is written last, in parts of about `DERIVED_PART_BYTES` each that
replace one key range of characters: each part deletes the rows of its range and inserts the new
ones in its one transaction, and bumps the listing version, so a reader sees a character's old list or
its new one, never neither. The parts are idempotent: a stopped run is resumed from any part
(`FROM=part-NNN.sql apply.sh`), and a rerun from the start is the same. `han_ids` holds each
character's descriptions, which the form picker's IDS editor starts from; it is written the same way,
after `character_derived`.
`words` and `word_spellings` are the hand tables of decision 0004, read through `refs.words` and
`refs.word_spellings`, which joins each 振り仮名 row to its counts; a character card shows them
beside its variants.

OUT/sql/part-NNN.sql are the parts in order and OUT/apply.sh imports them, retrying a refused part,
and checks the counts it expects. The full export (`export_cloudflare.py`) fills the same tables
through `fill`.
"""
from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import sqlite3
from functools import cache
from pathlib import Path

from glyph_atlas import han_components, refs

ROOT = Path(__file__).resolve().parents[1]
PART_BYTES = 40 * 1024 * 1024
STATEMENT_BYTES = 90 * 1024
COLUMNS = ("a", "b", "relation", "source", "detail", "written", "widens")
STAGING = "character_variants_next"
SUBSTITUTIONS_STAGING = "component_variants_next"
DERIVED_COLUMNS = ("a", "forms")
#: The size of one part of `character_derived`: about 2,000 characters' lists, a few seconds of D1.
DERIVED_PART_BYTES = 2 * 1024 * 1024
SUBSTITUTION_COLUMNS = ("a", "b", "count", "pairs")
WORDS_STAGING = "words_next"
IDS_COLUMNS = ("char", "sequences")
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


def _derived_part(chars: list[str]) -> list[tuple[str, str]]:
    return [row for char in chars if (row := refs.derived_row(char))]


@cache
def derived_rows() -> tuple[tuple[str, str], ...]:
    """Every row of the derived tier (refs.derived_rows), computed once per run on forked processes
    that share the descriptions read before the pool starts. Each chunk runs in a fresh process, so
    what a derivation keeps of each part never grows past one chunk's characters."""
    chars = sorted(refs._descriptions().trees)
    refs._maker()
    refs._stated_pairs()
    chunks = [chars[i::256] for i in range(256)]
    with multiprocessing.get_context("fork").Pool(max(1, (os.cpu_count() or 2) - 2), maxtasksperchild=1) as pool:
        found = [row for part in pool.imap_unordered(_derived_part, chunks) for row in part]
    return tuple(sorted(found))


def substitution_rows() -> list[tuple[str, str, int, str]]:
    """`component_variants`: each substitution with its count and the pairs behind it."""
    return [(left, right, item["count"], json.dumps(item["pairs"], ensure_ascii=False, separators=(",", ":")))
            for (left, right), item in sorted(refs.component_variants().items())]


def citations() -> str:
    """Every source a row of any table may cite, the derived tier's own `derived-ids` included."""
    return json.dumps({**refs.variant_sources(), **refs.component_variant_sources()},
                      ensure_ascii=False, separators=(",", ":"))


def word_citations() -> str:
    """Every source a word or a spelling cites."""
    return json.dumps({**refs.word_sources(), **refs.word_spelling_sources()}, ensure_ascii=False, separators=(",", ":"))


@cache
def ids_rows() -> tuple[tuple[str, str], ...]:
    """`han_ids`: each character's descriptions the IDS editor starts from (`refs.ids_sequences`)."""
    return tuple((char, json.dumps(found, ensure_ascii=False, separators=(",", ":")))
                 for char in sorted(han_components._sequences()) if (found := refs.ids_sequences(char)))


def word_rows() -> list[tuple]:
    return [tuple(row[column] for column in WORD_COLUMNS) for row in refs.words().values()]


def spelling_rows() -> list[tuple]:
    return [tuple(row.get(column) for column in SPELLING_COLUMNS) for row in refs.word_spellings()]


def expected() -> dict[str, int]:
    """What the live tables hold once the parts are applied: rows are unique by their key."""
    rows = {(e["a"], e["b"], e["relation"], e["source"]): e for e in refs.variant_edges()}
    return {"edges": len(rows), "written": sum(e["written"] for e in rows.values()),
            "widens": sum(e["widens"] for e in rows.values()),
            "substitutions": len(substitution_rows()), "derived": len(derived_rows()),
            "words": len(word_rows()), "spellings": len(spelling_rows()), "ids": len(ids_rows())}


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


def ranged_parts(table: str, key: str, columns: tuple[str, ...], rows, budget: int) -> list[list[str]]:
    """`rows` (sorted by `key`, their first column) as parts of about `budget` bytes, each replacing
    one key range of `table` in its one transaction: the range's rows are deleted, the new ones
    inserted, and the listing version bumped. The first part's range is open below and the last's
    above, so a key no longer listed goes too."""
    groups: list[list[tuple]] = [[]]
    size = 0
    for row in rows:
        length = sum(len(str(value).encode()) for value in row) + 8
        if groups[-1] and size + length > budget:
            groups.append([])
            size = 0
        groups[-1].append(row)
        size += length
    parts = []
    for at, group in enumerate(groups):
        bounds = []
        if at:
            bounds.append(f"{key}>={quote(group[0][0])}")
        if at + 1 < len(groups):
            bounds.append(f"{key}<{quote(groups[at + 1][0][0])}")
        where = f" WHERE {' AND '.join(bounds)}" if bounds else ""
        parts.append([f"DELETE FROM {table}{where};\n", *insert_rows(table, columns, group), VERSION_BUMP])
    return parts


def statements() -> list[list[str]]:
    """The staging fill, in statements under D1's statement limit, then the swap, then each ranged part
    of `character_derived` as a group of its own."""
    fill = [
        f"DROP TABLE IF EXISTS {STAGING};\n",
        (f"CREATE TABLE {STAGING} (a TEXT NOT NULL, b TEXT NOT NULL, relation TEXT NOT NULL, source TEXT NOT NULL,"
         " detail TEXT NOT NULL, written INTEGER NOT NULL, widens INTEGER NOT NULL,"
         " PRIMARY KEY(a,b,relation,source)) WITHOUT ROWID;\n"),
        f"DROP TABLE IF EXISTS {SUBSTITUTIONS_STAGING};\n",
        (f"CREATE TABLE {SUBSTITUTIONS_STAGING} (a TEXT NOT NULL, b TEXT NOT NULL, count INTEGER NOT NULL,"
         " pairs TEXT NOT NULL, PRIMARY KEY(a,b)) WITHOUT ROWID;\n"),
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
    fill += insert_rows(SUBSTITUTIONS_STAGING, SUBSTITUTION_COLUMNS, substitution_rows())
    fill += insert_rows(WORDS_STAGING, WORD_COLUMNS, word_rows())
    fill += insert_rows(SPELLINGS_STAGING, SPELLING_COLUMNS, spelling_rows())
    swap = []
    for live, staging, columns in (
        ("character_variants", STAGING, COLUMNS),
        ("component_variants", SUBSTITUTIONS_STAGING, SUBSTITUTION_COLUMNS),
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
    return [fill, swap, *ranged_parts("character_derived", "a", DERIVED_COLUMNS, derived_rows(), DERIVED_PART_BYTES),
            *ranged_parts("han_ids", "char", IDS_COLUMNS, ids_rows(), DERIVED_PART_BYTES)]


def fill(db: sqlite3.Connection, *, derived: bool = True) -> None:
    """The same rows in a local catalogue built from the migrations.

    `derived=False` leaves out the derived forms and the descriptions: deriving them runs over every
    character and takes more memory than a collection export has, and a collection publication never
    reads them, since this script publishes them to the site itself."""
    db.execute("DELETE FROM character_variants")
    db.executemany(f"INSERT OR REPLACE INTO character_variants({','.join(COLUMNS)}) VALUES ({','.join('?' * len(COLUMNS))})",
                   [tuple(int(edge[c]) if c in ("written", "widens") else edge[c] for c in COLUMNS)
                    for edge in refs.variant_edges()])
    db.execute("DELETE FROM component_variants")
    db.executemany(f"INSERT OR REPLACE INTO component_variants({','.join(SUBSTITUTION_COLUMNS)}) VALUES (?,?,?,?)",
                   substitution_rows())
    if derived:
        db.execute("DELETE FROM character_derived")
        db.executemany(f"INSERT OR REPLACE INTO character_derived({','.join(DERIVED_COLUMNS)}) VALUES (?,?)",
                       derived_rows())
        db.execute("DELETE FROM han_ids")
        db.executemany("INSERT INTO han_ids(char,sequences) VALUES (?,?)", ids_rows())
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
    fill, swap, *ranged = groups
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
    parts += ranged
    paths = []
    for index, lines in enumerate(parts, 1):
        path = directory / f"part-{index:03d}.sql"
        path.write_text("".join(lines), encoding="utf-8")
        paths.append(path)
    return paths


APPLY = """#!/usr/bin/env bash
# Replace the site's variant and word tables (character_variants, component_variants, words,
# word_spellings) with this export, then character_derived and han_ids range by range. Each part is
# one D1 transaction. The first parts fill staging tables and one swaps them in, so a failure never leaves the
# live tables empty; each later part replaces one key range of character_derived or han_ids and is
# idempotent.
# Safe to rerun, and to resume from a part: FROM=part-NNN.sql ./apply.sh
set -uo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
cd ~/projects/Philology/glyph-atlas/apps/cloudflare
count="SELECT (SELECT count(*) FROM character_variants) AS edges, (SELECT count(*) FROM character_variants WHERE written=1) AS written, (SELECT count(*) FROM character_variants WHERE widens=1) AS widens, (SELECT count(*) FROM component_variants) AS substitutions, (SELECT count(*) FROM character_derived) AS derived, (SELECT count(*) FROM words) AS words, (SELECT count(*) FROM word_spellings) AS spellings, (SELECT count(*) FROM han_ids) AS ids"
q() {{ bunx wrangler d1 execute glyph-atlas --remote --json --command "$1" 2>/dev/null | jq -c '.[0].results[0]'; }}
echo "before: $(q "$count")"
echo "started $(date -u +%Y-%m-%dT%H:%M:%SZ); undo: bunx wrangler d1 time-travel restore glyph-atlas --timestamp=<that time>"
for part in "$here"/sql/part-*.sql; do
  [ -n "${{FROM:-}}" ] && [[ "$(basename "$part")" < "$FROM" ]] && continue
  ../../scripts/d1_import.sh "$part" || {{ echo "$(basename "$part") did not apply; nothing of it is in the database. Resume with FROM=$(basename "$part")." >&2; exit 1; }}
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
