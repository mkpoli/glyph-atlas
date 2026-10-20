"""The tables of a publication catalogue (`catalogue.sql`, and the ledger's from `glyph_atlas.review.ledger`)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from glyph_atlas.review import ledger

DDL = Path(__file__).with_name("catalogue.sql")


def schema(db: sqlite3.Connection) -> None:
    """Make the catalogue's tables that `db` lacks.

    A file made with the site database's migrations holds their triggers, which fill tables a catalogue
    no longer has; it is refused.
    """
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='trigger' AND tbl_name NOT IN "
                  "('assertions','assertion_evidence','assertion_premises','assertion_actions','forms','representations')").fetchone():
        raise ValueError("this catalogue was made with the site database's migrations; export it again into a new folder")
    db.executescript(DDL.read_text(encoding="utf-8"))
    ledger.schema(db)
