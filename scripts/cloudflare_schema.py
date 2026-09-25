"""The D1 schema as the Worker's migrations define it, for the SQLite files a publication is built in."""
from __future__ import annotations

import re
from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parents[1] / "apps/cloudflare/migrations"

# Counts assigned corpus glyphs per character and material, with the statements the migration runs.
# Every publication that rewrites `corpus_units` runs them after it.
CORPUS_CHARACTERS = "\n".join(re.findall(r"DELETE FROM corpus_characters;|INSERT INTO corpus_characters [\s\S]*?;",
                                         (MIGRATIONS / "0006_corpus_rounds.sql").read_text()))
assert CORPUS_CHARACTERS.count(";") == 2, "0006 no longer regenerates corpus_characters"


def schema(db):
    """Apply the migrations `db` has not had, in order, as D1 does; `user_version` counts them.

    A file made before the count was kept has had only the first migration, and every statement up to
    0006 is idempotent, so it is brought up to date the same way. Its corpus rows read as unknown
    material, as D1's do until a publication rewrites them.
    """
    applied = db.execute("PRAGMA user_version").fetchone()[0]
    names = sorted(MIGRATIONS.glob("*.sql"))
    for path in names[applied:]:
        db.executescript(path.read_text())
    db.execute(f"PRAGMA user_version={len(names)}")
