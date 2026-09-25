"""The D1 schema as the Worker's migrations define it, for the SQLite files a publication is built in."""
from __future__ import annotations

import re
from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parents[1] / "apps/cloudflare/migrations"

# Marks the corpus glyphs that have a `units` row as named, then counts assigned glyphs per character
# and material, with the statements the migration runs. Every publication that rewrites `corpus_units`
# runs them after it, since a rewritten row starts unnamed.
CORPUS_REFRESH = "\n".join(re.findall(
    r"UPDATE corpus_units SET named=1 WHERE id IN [\s\S]*?;|DELETE FROM corpus_characters;|INSERT INTO corpus_characters [\s\S]*?;",
    (MIGRATIONS / "0006_corpus_rounds.sql").read_text()))
assert CORPUS_REFRESH.count(";") == 3, "0006 no longer restores and counts corpus_characters"

# Code points whose script makes a label kana or kanji, generated from the Unicode script properties
# the Worker's `categoryOf` tests; 0006 names the same ranges in SQL.
KANA = ((0x3041, 0x3096), (0x309D, 0x309F), (0x30A1, 0x30FA), (0x30FD, 0x30FF), (0x31F0, 0x31FF), (0x32D0, 0x32FE),
        (0x3300, 0x3357), (0xFF66, 0xFF6F), (0xFF71, 0xFF9D), (0x1AFF0, 0x1AFF3), (0x1AFF5, 0x1AFFB), (0x1AFFD, 0x1AFFE),
        (0x1B000, 0x1B122), (0x1B132, 0x1B132), (0x1B150, 0x1B152), (0x1B155, 0x1B155), (0x1B164, 0x1B167),
        (0x1F200, 0x1F200))
HAN = ((0x2E80, 0x2E99), (0x2E9B, 0x2EF3), (0x2F00, 0x2FD5), (0x3005, 0x3005), (0x3007, 0x3007), (0x3021, 0x3029),
       (0x3038, 0x303B), (0x3400, 0x4DBF), (0x4E00, 0x9FFF), (0xF900, 0xFA6D), (0xFA70, 0xFAD9), (0x16FE2, 0x16FE3),
       (0x16FF0, 0x16FF6), (0x20000, 0x2A6DF), (0x2A700, 0x2B81D), (0x2B820, 0x2CEAD), (0x2CEB0, 0x2EBE0),
       (0x2EBF0, 0x2EE5D), (0x2F800, 0x2FA1D), (0x30000, 0x3134A), (0x31350, 0x33479))


def category_of(label: str | None) -> str:
    """A label's category, by its first character's script, as the Worker writes it."""
    point = ord(label[0]) if label else -1
    if any(a <= point <= b for a, b in KANA):
        return "kana"
    return "kanji" if any(a <= point <= b for a, b in HAN) else "other"


def schema(db):
    """Apply the migrations `db` has not had, in order, as D1 does; `user_version` counts them.

    A file made before the count was kept has had only the first migration, and every statement up to
    0006 is idempotent, so it is brought up to date the same way, unless it already holds corpus rows:
    those were written without their material, and would read as unknown, so it is refused.
    """
    applied = db.execute("PRAGMA user_version").fetchone()[0]
    columns = {row[1] for row in db.execute("PRAGMA table_info(corpus_units)")}
    if columns and "production" not in columns and db.execute("SELECT 1 FROM corpus_units LIMIT 1").fetchone():
        raise ValueError("this export's corpus rows predate their material; export them again")
    names = sorted(MIGRATIONS.glob("*.sql"))
    for path in names[applied:]:
        db.executescript(path.read_text())
    db.execute(f"PRAGMA user_version={len(names)}")
