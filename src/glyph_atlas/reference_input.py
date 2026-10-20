"""The reference input: the tables `scripts/atlas3/reference.py` moves onto the new layout (decision record 0007).

One SQLite file, `reference-input.sqlite` in `ATLAS3_DIR` (default `work/atlas-v3`), written by the
generators and read by the load:

- `characters`, the cards, and `aliases`, the names a search finds them by: `export_graphemes.py
  --catalogue` copies those of a full export (`export_cloudflare.py`), and `export_graphemes.py`
  rewrites the family parts of the cards whose family changed since;
- `character_variants`, `character_derived`, `component_variants`, `grapheme_writers`, `words`,
  `word_spellings`, `han_ids` and `sources` (the citations, `variant_sources` and `word_sources`):
  `export_character_variants.py`;
- `han_components`, `han_component_names`: `export_components_cloudflare.py`.

A generator replaces the tables it writes in one transaction and notes the time in `written`, so a
table no generator has written yet is told from one written empty. Keys are those the site's tables
had, so a row a generator lists twice is kept once, the later one.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def default_path() -> Path:
    """`reference-input.sqlite` in `ATLAS3_DIR`, as the environment names it now."""
    return Path(os.environ.get("ATLAS3_DIR", ROOT / "work" / "atlas-v3")) / "reference-input.sqlite"


#: Each table's columns, in the order a generator gives a row.
COLUMNS = {
    "characters": ("code_point", "character", "name", "data", "detail"),
    "aliases": ("query", "code_point", "rank"),
    "character_variants": ("a", "b", "relation", "source", "detail", "written", "widens"),
    "character_derived": ("a", "forms"),
    "component_variants": ("a", "b", "count", "pairs"),
    "grapheme_writers": ("writer", "grapheme", "own"),
    "words": ("id", "language", "reading", "class"),
    "word_spellings": ("word", "spelling", "source", "locator", "tier", "word_by", "basis", "related", "statement",
                       "documents", "occurrences"),
    "han_ids": ("char", "sequences", "shapes"),
    "han_components": ("component", "tier", "size", "code_point", "n", "direct"),
    "han_component_names": ("query", "component", "total"),
    "sources": ("key", "value"),
}

DDL = """
CREATE TABLE IF NOT EXISTS characters(code_point TEXT PRIMARY KEY, character TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
  data TEXT NOT NULL, detail TEXT NOT NULL) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS aliases(query TEXT NOT NULL, code_point TEXT NOT NULL, rank INTEGER NOT NULL,
  PRIMARY KEY(query, code_point)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS character_variants(a TEXT NOT NULL, b TEXT NOT NULL, relation TEXT NOT NULL, source TEXT NOT NULL,
  detail TEXT NOT NULL, written INTEGER NOT NULL, widens INTEGER NOT NULL, PRIMARY KEY(a, b, relation, source)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS character_derived(a TEXT PRIMARY KEY, forms TEXT NOT NULL) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS component_variants(a TEXT NOT NULL, b TEXT NOT NULL, count INTEGER NOT NULL, pairs TEXT NOT NULL,
  PRIMARY KEY(a, b)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS grapheme_writers(writer TEXT NOT NULL, grapheme TEXT NOT NULL, own INTEGER NOT NULL,
  PRIMARY KEY(writer, grapheme)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS words(id TEXT PRIMARY KEY, language TEXT NOT NULL, reading TEXT NOT NULL, class TEXT NOT NULL) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS word_spellings(word TEXT NOT NULL, spelling TEXT NOT NULL, source TEXT NOT NULL, locator TEXT NOT NULL,
  tier TEXT NOT NULL, word_by TEXT NOT NULL, basis TEXT NOT NULL, related TEXT NOT NULL, statement TEXT NOT NULL,
  documents INTEGER, occurrences INTEGER, PRIMARY KEY(word, spelling, source, locator)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS han_ids(char TEXT PRIMARY KEY, sequences TEXT NOT NULL, shapes TEXT) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS han_components(component TEXT NOT NULL, tier INTEGER NOT NULL, size INTEGER NOT NULL,
  code_point TEXT NOT NULL, n INTEGER NOT NULL, direct INTEGER NOT NULL, PRIMARY KEY(component, tier, size, code_point)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS han_component_names(query TEXT PRIMARY KEY, component TEXT NOT NULL, total INTEGER NOT NULL) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS sources(key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS written(name TEXT PRIMARY KEY, at TEXT NOT NULL) WITHOUT ROWID;
"""


@contextmanager
def writing(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    """A connection to the input, its tables made; what the block writes is one transaction."""
    path = path or default_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    try:
        db.executescript(DDL)
        with db:
            yield db
    finally:
        db.close()


def replace(db: sqlite3.Connection, table: str, rows: Iterable[tuple]) -> int:
    """Every row of `table` replaced by `rows`; how many the table holds after."""
    columns = COLUMNS[table]
    db.execute(f"DELETE FROM {table}")
    noted(db, table)
    db.executemany(f"INSERT OR REPLACE INTO {table}({','.join(columns)}) VALUES({','.join('?' * len(columns))})", rows)
    return db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def put_source(db: sqlite3.Connection, key: str, value: str) -> None:
    db.execute("INSERT OR REPLACE INTO sources(key, value) VALUES(?, ?)", (key, value))
    noted(db, "sources")


def noted(db: sqlite3.Connection, table: str) -> None:
    db.execute("INSERT OR REPLACE INTO written(name, at) VALUES(?, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))", (table,))


def reading(path: Path | None = None) -> sqlite3.Connection:
    """The input, read only; a missing file stops the caller."""
    path = path or default_path()
    if not path.exists():
        raise SystemExit(f"{path} is missing; the generators in glyph_atlas.reference_input write it")
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)
