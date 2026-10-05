"""A corpus republication writes only what it changes: D1 bills every row, index entry and trigger write."""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path("scripts").resolve()))
from cloudflare_schema import CORPUS_REFRESH, corpus_upsert

PLAIN, DECIDED, NAMED = "codh:b:p:B0001:C0001", "codh:b:p:B0001:C0002", "codh:b:p:B0001:C0003"
# id, character, family, visual_group, shuffle, object, offset, size, production, style, document
SOURCE = {PLAIN: (PLAIN, "は", "U+306F", None, 1, "x", 0, 1, "unknown", "unassessed", "codh:b"),
          DECIDED: (DECIDED, "は", "U+306F", None, 2, "x", 1, 1, "unknown", "unassessed", "codh:b"),
          NAMED: (NAMED, "ほ", "U+307B", None, 3, "x", 2, 1, "print", "formal", "codh:b")}


def schema():
    db = sqlite3.connect(":memory:")
    for migration in sorted(Path("apps/cloudflare/migrations").glob("*.sql")):
        db.executescript(migration.read_text())
    return db


def publish(db, rows):
    db.executescript("\n".join(corpus_upsert(row) for row in rows) + "\n" + CORPUS_REFRESH)


def state(db):
    return (db.execute("SELECT * FROM corpus_units ORDER BY id").fetchall(),
            db.execute("SELECT * FROM corpus_characters ORDER BY 1,2").fetchall())


def site():
    db = schema()
    publish(db, SOURCE.values())
    # A decision names a form for one glyph, and a round names another.
    db.execute("INSERT INTO form_units(id,family,cluster,rank,similarity,split,glyph_set,glyph_form,form)"
               " VALUES(?,?,'U+306F:one',0,1,'train',1,?,?)", (DECIDED, "U+306F", "𛂥", "𛂥"))
    db.execute("INSERT INTO units(id,origin,character,family,production,category,state,revision,quiz,priority,shuffle,data,snapshot,context,visual)"
               " VALUES(?,'corpus','ほ','U+307B','print','kana','pending',1,0,1,3,'{}','{}','{}','{}')", (NAMED,))
    db.executescript(CORPUS_REFRESH)
    return db


def writes(db, sql):
    before = db.total_changes
    db.executescript(sql)
    return db.total_changes - before


def test_republishing_the_same_rows_writes_only_the_stamp():
    db = site()
    settled = state(db)
    assert settled[0][1][1] == "𛂥", "the decided glyph shows its form"
    sql = "\n".join(corpus_upsert(row) for row in SOURCE.values()) + "\n" + CORPUS_REFRESH
    assert writes(db, sql) == 1, "only `corpus_counts_at` is written"
    assert state(db) == settled


def test_a_changed_row_is_written_and_its_form_put_back():
    db = site()
    moved = (*SOURCE[DECIDED][:4], 9, *SOURCE[DECIDED][5:])
    retitled = (*SOURCE[PLAIN][:8], "manuscript", *SOURCE[PLAIN][9:])
    publish(db, [retitled, moved, SOURCE[NAMED]])
    rows = {row[0]: row for row in db.execute("SELECT id,character,shuffle,production FROM corpus_units")}
    assert rows[DECIDED] == (DECIDED, "𛂥", 9, "unknown"), "a moved decided glyph keeps its form"
    assert rows[PLAIN] == (PLAIN, "は", 1, "manuscript")
    counts = db.execute("SELECT character,production,n,named FROM corpus_characters ORDER BY 1,2").fetchall()
    assert ("は", "unknown", 1, 0) not in counts and ("は", "manuscript", 1, 0) in counts, \
        "a material no glyph has any more loses its count"
    assert counts == sorted(db.execute("SELECT character,production,count(*),sum(named) FROM corpus_units"
                                       " WHERE character IS NOT NULL GROUP BY 1,2").fetchall())


def test_a_new_source_character_reaches_an_undecided_glyph():
    db = site()
    publish(db, [(*SOURCE[PLAIN][:1], "ば", *SOURCE[PLAIN][2:])])
    assert db.execute("SELECT character FROM corpus_units WHERE id=?", (PLAIN,)).fetchone() == ("ば",)


def test_a_named_glyph_takes_its_published_style_though_its_row_is_unchanged():
    db = site()
    db.execute("UPDATE units SET style='unassessed' WHERE id=?", (NAMED,))
    publish(db, SOURCE.values())
    assert db.execute("SELECT style,style_order FROM units WHERE id=?", (NAMED,)).fetchone() == ("formal", 2)
