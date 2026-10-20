"""The book facts input: what `scripts/atlas3/books.py` moves into glyph-atlas-v3 beside the books.

One SQLite file, `book-facts-input.sqlite` in `ATLAS3_DIR` (default `work/atlas-v3`), written by the
generators and read by the load:

- `date_claims` and `document_dating`: `export_dates_cloudflare.py`, from an `atlas dates` output. A
  claim is a ledger assertion with its source as evidence; `document_dating` is each document's
  dating on both axes as `glyph_atlas.dates` resolved it from the claims, for every document the
  export read, a book of the new layout or not;
- `document_characters`: `export_document_characters.py`, from a dataset `atlas ainu merge` wrote,
  the documents it covers replaced and the rest kept.

A generator writes in one transaction and notes in `written` when, so a table no generator has
written yet is told from one written empty. The dates export's time is its stamp, which names the
claims it asserts again and the retractions it makes.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

CLAIM_COLUMNS = ("id", "subject", "predicate", "value", "tier", "asserted_by", "kind", "ref", "locator")
DATING_COLUMNS = ("document", "axis", "kind", "start", "end", "precision", "qualifier", "uncertain", "text", "label",
                  "status", "claims", "calendar", "conversion", "source", "resolver")
CHARACTER_COLUMNS = ("document", "ord", "unit", "data")

DDL = """
CREATE TABLE IF NOT EXISTS date_claims(id TEXT PRIMARY KEY, subject TEXT NOT NULL, predicate TEXT NOT NULL, value TEXT NOT NULL,
  tier TEXT NOT NULL, asserted_by TEXT NOT NULL, kind TEXT NOT NULL, ref TEXT NOT NULL, locator TEXT) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS document_dating(document TEXT NOT NULL, axis TEXT NOT NULL CHECK (axis IN ('witness','composed')),
  kind TEXT NOT NULL, start INTEGER, end INTEGER, precision TEXT NOT NULL, qualifier TEXT, uncertain INTEGER NOT NULL DEFAULT 0,
  text TEXT NOT NULL, label TEXT NOT NULL, status TEXT NOT NULL, claims TEXT NOT NULL, calendar TEXT NOT NULL, conversion TEXT,
  source TEXT NOT NULL, resolver TEXT NOT NULL, PRIMARY KEY(document, axis)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS document_characters(document TEXT NOT NULL, ord INTEGER NOT NULL, unit TEXT NOT NULL,
  data TEXT NOT NULL, PRIMARY KEY(document, ord)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS written(name TEXT PRIMARY KEY, at TEXT NOT NULL) WITHOUT ROWID;
"""


def default_path() -> Path:
    """`book-facts-input.sqlite` in `ATLAS3_DIR`, as the environment names it now."""
    return Path(os.environ.get("ATLAS3_DIR", ROOT / "work" / "atlas-v3")) / "book-facts-input.sqlite"


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


def noted(db: sqlite3.Connection, table: str, at: str) -> None:
    db.execute("INSERT OR REPLACE INTO written(name, at) VALUES(?, ?)", (table, at))


def reading(path: Path | None = None) -> sqlite3.Connection:
    """The input, read only; a missing file stops the caller."""
    path = path or default_path()
    if not path.exists():
        raise SystemExit(f"{path} is missing; export_dates_cloudflare.py and export_document_characters.py write it")
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)
