"""Bring a review store's ledger to the tiers attested, observed and derived, as migration 0069 does in D1.

    uv run scripts/migrate_ledger_tiers.py STORE... [--apply]

STORE is a review store (`review.sqlite`) or the dataset directory that holds one. A store whose
`assertions` table still allows the former fourth tier is rebuilt by the migration's own SQL, in one
transaction: a form's naming claim becomes observed, a date claim attested. Without `--apply` it
reports each store's rows and writes nothing; a store already migrated, or with no ledger, is left
as it is.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from glyph_atlas.review.store import STORE_NAME

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "apps" / "cloudflare" / "migrations" / "0069_ledger_tiers.sql"
TIERS = ("attested", "observed", "derived")


def store_path(path: Path) -> Path:
    return path / STORE_NAME if path.is_dir() else path


def stale(conn: sqlite3.Connection) -> bool | None:
    """Whether the store's `assertions` allows a tier outside `TIERS`; None when it has no ledger."""
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='assertions'").fetchone()
    if row is None:
        return None
    check = row[0].split("tier IN (", 1)[1].split(")", 1)[0]
    return {t.strip(" '") for t in check.split(",")} != set(TIERS)


def migrate(path: Path, apply: bool) -> dict:
    conn = sqlite3.connect(path, isolation_level=None)
    try:
        found = stale(conn)
        if found is None:
            return {"store": str(path), "ledger": False}
        rows = conn.execute("SELECT count(*) FROM assertions").fetchone()[0]
        other = conn.execute(f"SELECT count(*) FROM assertions WHERE tier NOT IN ({','.join('?' * len(TIERS))})",
                             TIERS).fetchone()[0]
        report = {"store": str(path), "ledger": True, "migrated": not found, "rows": rows, "retiered": other}
        if found and apply:
            conn.executescript("BEGIN IMMEDIATE;\n" + MIGRATION.read_text(encoding="utf-8") + "\nCOMMIT;")
            report["migrated"] = True
        return report
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("stores", nargs="+", type=Path)
    parser.add_argument("--apply", action="store_true", help="rebuild the stores that need it")
    args = parser.parse_args()
    for path in args.stores:
        print(json.dumps(migrate(store_path(path), args.apply), ensure_ascii=False))


if __name__ == "__main__":
    main()
