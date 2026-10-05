"""The D1 schema as the Worker's migrations define it, for the SQLite files a publication is built in."""
from __future__ import annotations

import re
from pathlib import Path

from glyph_atlas.representation import described

MIGRATIONS = Path(__file__).resolve().parents[1] / "apps/cloudflare/migrations"

# A decided glyph's corpus character is its form or the character it or its cluster was reported as, or, with none decided, the character it had before
# any decision covered it (the Worker applies one decision the same way). A rewritten corpus row
# carries the source's character, so the forms are applied to it again, and the glyphs the Worker had
# still to move (`corpus_follow`) have been moved with the rest. A glyph already right is not written:
# D1 bills every row and index entry an UPDATE writes, changed or not.
FORMS_REAPPLY = """INSERT OR IGNORE INTO form_bases(id,character,family) SELECT c.id,c.character,c.family FROM corpus_units c JOIN form_units f ON f.id=c.id
  WHERE f.glyph_set=1 OR f.cluster_form IS NOT NULL OR f.cluster_issue IS NOT NULL;
UPDATE corpus_units SET character=CASE WHEN f.glyph_set=1 OR f.form IS NOT NULL OR f.issue IS NOT NULL THEN coalesce(f.issue_character,f.form) ELSE b.character END,
  family=coalesce(f.written_family,b.family)
  FROM form_units f JOIN form_bases b ON b.id=f.id WHERE f.id=corpus_units.id AND corpus_units.named=0
  AND (corpus_units.character IS NOT CASE WHEN f.glyph_set=1 OR f.form IS NOT NULL OR f.issue IS NOT NULL THEN coalesce(f.issue_character,f.form) ELSE b.character END
    OR corpus_units.family IS NOT coalesce(f.written_family,b.family));
DELETE FROM corpus_follow;"""

# Reapplies the forms, marks the corpus glyphs that have a `units` row as named, then counts assigned
# glyphs per character and material (0006). Every publication that rewrites `corpus_units` runs them
# after it. A count is written only where it changed and a material no glyph has any more is removed,
# so a publication that moves a few glyphs rewrites a few counts. A named glyph then takes its published
# row's style, which `corpus_style` (0035) gave it whenever the upsert rewrote that row.
CORPUS_REFRESH = FORMS_REAPPLY + "\n" + re.search(
    r"UPDATE corpus_units SET named=1 WHERE id IN [\s\S]*?;", (MIGRATIONS / "0006_corpus_rounds.sql").read_text()).group(0) + """
INSERT INTO corpus_characters SELECT character,production,count(*),sum(named) FROM corpus_units
 WHERE character IS NOT NULL GROUP BY character,production
 ON CONFLICT(character,production) DO UPDATE SET n=excluded.n,named=excluded.named
 WHERE corpus_characters.n IS NOT excluded.n OR corpus_characters.named IS NOT excluded.named;
DELETE FROM corpus_characters WHERE NOT EXISTS (SELECT 1 FROM corpus_units c
 WHERE c.character=corpus_characters.character AND c.production=corpus_characters.production);
UPDATE units SET style=c.style FROM corpus_units c WHERE units.origin='corpus' AND c.id=units.id AND units.style IS NOT c.style;"""
# The Worker caches the grapheme browser's corpus counts on this stamp; it is written with the recount,
# in the same part, so no request between them caches the old counts under a new key.
CORPUS_REFRESH += ("\nINSERT OR REPLACE INTO metadata(key,value) "
                   "VALUES('corpus_counts_at',json_quote(strftime('%Y-%m-%dT%H:%M:%fZ','now')));")

# Code points whose script makes a label kana, kanji or hangul, generated from the Unicode script
# properties the Worker's `categoryOf` tests; 0006 names the kana and Han ranges in SQL and 0008 the
# Hangul ones. GUGYEOL has no Unicode script property: it is the Hanyang private-use range
# `data/vocab/characters.tsv` holds the 구결자 at, and 0012 names it in SQL the same way.
KANA = ((0x3041, 0x3096), (0x309D, 0x309F), (0x30A1, 0x30FA), (0x30FD, 0x30FF), (0x31F0, 0x31FF), (0x32D0, 0x32FE),
        (0x3300, 0x3357), (0xFF66, 0xFF6F), (0xFF71, 0xFF9D), (0x1AFF0, 0x1AFF3), (0x1AFF5, 0x1AFFB), (0x1AFFD, 0x1AFFE),
        (0x1B000, 0x1B122), (0x1B132, 0x1B132), (0x1B150, 0x1B152), (0x1B155, 0x1B155), (0x1B164, 0x1B167),
        (0x1F200, 0x1F200))
HAN = ((0x2E80, 0x2E99), (0x2E9B, 0x2EF3), (0x2F00, 0x2FD5), (0x3005, 0x3005), (0x3007, 0x3007), (0x3021, 0x3029),
       (0x3038, 0x303B), (0x3400, 0x4DBF), (0x4E00, 0x9FFF), (0xF900, 0xFA6D), (0xFA70, 0xFAD9), (0x16FE2, 0x16FE3),
       (0x16FF0, 0x16FF6), (0x20000, 0x2A6DF), (0x2A700, 0x2B81D), (0x2B820, 0x2CEAD), (0x2CEB0, 0x2EBE0),
       (0x2EBF0, 0x2EE5D), (0x2F800, 0x2FA1D), (0x30000, 0x3134A), (0x31350, 0x33479))
HANGUL = ((0x1100, 0x11FF), (0x302E, 0x302F), (0x3131, 0x318E), (0x3200, 0x321E), (0x3260, 0x327E), (0xA960, 0xA97C),
          (0xAC00, 0xD7A3), (0xD7B0, 0xD7C6), (0xD7CB, 0xD7FB), (0xFFA0, 0xFFBE), (0xFFC2, 0xFFC7), (0xFFCA, 0xFFCF),
          (0xFFD2, 0xFFD7), (0xFFDA, 0xFFDC))
GUGYEOL = ((0xF67E, 0xF77C),)


# A published corpus row, in the order `corpus_upsert` takes it.
CORPUS_COLUMNS = ("id", "character", "family", "visual_group", "shuffle", "object", "offset", "size", "production",
                  "style", "document")


def corpus_upsert(values) -> str:
    """One published corpus row as D1 SQL.

    A rewrite updates every column but `named`, which records that a round or review reached the glyph
    and is D1's own: a publication that reset it would deal a named glyph twice until its last part ran.
    A row with no `document`, from an export made before 0053, keeps the one D1 holds.

    A row D1 already holds as published is left alone, and so is a glyph whose only difference is the
    form `FORMS_REAPPLY` would put back on it: its base (`form_bases`) is the published character and
    family, and it is unnamed. Rewriting either would bill the row, its indexes and its triggers again
    for the value it already has.
    """
    quoted = ("NULL" if v is None else str(v) if isinstance(v, int) else "'" + str(v).replace("'", "''") + "'"
              for v in values)
    updates = ",".join("document=coalesce(excluded.document,corpus_units.document)" if c == "document"
                       else f"{c}=excluded.{c}" for c in CORPUS_COLUMNS[1:])
    same = " AND ".join("corpus_units.document IS coalesce(excluded.document,corpus_units.document)" if c == "document"
                        else f"corpus_units.{c} IS excluded.{c}" for c in CORPUS_COLUMNS[3:])
    return (f"INSERT INTO corpus_units({','.join(CORPUS_COLUMNS)}) VALUES({','.join(quoted)}) "
            f"ON CONFLICT(id) DO UPDATE SET {updates} WHERE NOT ({same} AND ("
            "corpus_units.character IS excluded.character AND corpus_units.family IS excluded.family"
            " OR corpus_units.named=0 AND EXISTS (SELECT 1 FROM form_bases b JOIN form_units f ON f.id=b.id"
            " WHERE b.id=excluded.id AND b.character IS excluded.character AND b.family IS excluded.family)));")


def document_count_statements(db) -> list[str]:
    """The glyph count of each document a corpus publication holds (0054), replacing the one D1 has.
    A document is published whole by one export, so its count here is its count on the site."""
    rows = [f"('{document.replace("'", "''")}',{n})" for document, n in db.execute(
        "SELECT document,count(*) FROM corpus_units WHERE document IS NOT NULL GROUP BY document ORDER BY document")]
    return [f"INSERT OR REPLACE INTO corpus_document_counts(document,n) VALUES{','.join(rows[i:i + 200])};"
            for i in range(0, len(rows), 200)]


def category_of(label: str | None) -> str:
    """A label's category, by its first character's script, as the Worker writes it."""
    point = ord(label[0]) if label else -1
    # A well-formed ideographic description sequence describes a Han character Unicode lacks.
    if label and described(label):
        return "kanji"
    if any(a <= point <= b for a, b in KANA):
        return "kana"
    if any(a <= point <= b for a, b in HAN):
        return "kanji"
    if any(a <= point <= b for a, b in HANGUL):
        return "hangul"
    return "gugyeol" if any(a <= point <= b for a, b in GUGYEOL) else "other"


def schema(db):
    """Apply the migrations `db` has not had, in order, as D1 does; `user_version` counts them.

    A file made before the count was kept has had only the first migration, and every statement up to
    0006 is idempotent, so it is brought up to date the same way, unless it already holds corpus rows:
    those were written without their material, and would read as unknown, so it is refused.
    """
    applied = db.execute("PRAGMA user_version").fetchone()[0]
    columns = {row[1] for row in db.execute("PRAGMA table_info(corpus_units)")}
    names = sorted(MIGRATIONS.glob("*.sql"))
    if columns and "production" not in columns and db.execute("SELECT 1 FROM corpus_units LIMIT 1").fetchone():
        raise ValueError("this export's corpus rows predate their material; export them again")
    # A file counted past 0006 before 0006 gained `named` holds a schema no migration describes.
    if applied >= 6 and "named" not in columns:
        raise ValueError("this export was built with an earlier draft of migration 0006; rebuild this export")
    for path in names[applied:]:
        db.executescript(path.read_text())
    db.execute(f"PRAGMA user_version={len(names)}")
