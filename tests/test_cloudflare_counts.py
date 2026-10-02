"""`unit_counts` holds what counting `units` would give, through every kind of write."""
import json
import sqlite3
from pathlib import Path

MIGRATIONS = sorted(Path("apps/cloudflare/migrations").glob("*.sql"))
COUNTS = Path("apps/cloudflare/migrations/0032_unit_counts.sql")

# The groups the Worker reads, counted from `units` itself.
COUNTED = """SELECT origin,coalesce(character,''),coalesce(document,''),state,coalesce(family,''),production,quiz,
  coalesce(json_extract(data,'$.source'),''),count(*) FROM units GROUP BY 1,2,3,4,5,6,7,8"""


def unit(db, identity, character="ア", document="hk:a", state="pending", family="U+30A2", source="Book A", quiz=1):
    db.execute("INSERT OR IGNORE INTO units(id,origin,character,family,visual_group,production,category,state,revision,quiz,priority,shuffle,data,snapshot,context,visual,document) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
        identity, "local", character, family, None, "handwritten", "kana", state, 0, quiz, 1, 0,
        json.dumps({"id": identity, "label": character, "source": source}), "{}", "{}", "{}", document))


def agrees(db):
    assert sorted(db.execute("SELECT * FROM unit_counts")) == sorted(db.execute(COUNTED))


def test_the_counts_follow_every_write_to_units():
    db = sqlite3.connect(":memory:")
    for migration in MIGRATIONS:
        if migration != COUNTS:
            db.executescript(migration.read_text())
    # Crops published before the table existed are counted when it is made.
    unit(db, "one"); unit(db, "two"); unit(db, "none", character=None, document=None, family=None, source=None)
    db.executescript(COUNTS.read_text())
    agrees(db)
    unit(db, "three", state="checked"); unit(db, "one")
    agrees(db)
    db.execute("UPDATE units SET state='checked',data=json_set(data,'$.revision',1) WHERE id='one'")
    db.execute("UPDATE units SET character='イ',family='U+30A4' WHERE id='two'")
    db.execute("UPDATE units SET data=json_set(data,'$.source','Book B') WHERE id='three'")
    db.execute("UPDATE units SET document='hk:b',quiz=0 WHERE id='none'")
    agrees(db)
    db.execute("DELETE FROM units WHERE id IN ('one','three')")
    agrees(db)
    assert db.execute("SELECT count(*) FROM unit_counts WHERE n<=0").fetchone()[0] == 0


def test_a_review_that_changes_only_the_record_leaves_the_counts_alone():
    db = sqlite3.connect(":memory:")
    for migration in MIGRATIONS:
        db.executescript(migration.read_text())
    unit(db, "one")
    before = db.total_changes
    db.execute("UPDATE units SET data=json_set(data,'$.note','x'),revision=1 WHERE id='one'")
    # The update itself is the only row written.
    assert db.total_changes - before == 1
    agrees(db)


def test_a_crop_written_anew_is_counted_once():
    """`INSERT OR REPLACE` removes the old row without the delete trigger while recursive triggers are off."""
    db = sqlite3.connect(":memory:")
    for migration in MIGRATIONS:
        db.executescript(migration.read_text())
    unit(db, "one"); unit(db, "two")
    # `table_info` leaves out the generated `style_order`, which takes no value.
    columns = [c[1] for c in db.execute("PRAGMA table_info(units)")]
    row = db.execute(f"SELECT {','.join(columns)} FROM units WHERE id='one'").fetchone()
    replace = f"INSERT OR REPLACE INTO units VALUES({','.join('?' * len(row))})"
    db.execute(replace, row)
    agrees(db)
    moved = list(row); moved[2] = "イ"; moved[4] = "U+30A4"
    db.execute(replace, moved)
    agrees(db)
    assert db.execute("SELECT count(*) FROM unit_count_keys").fetchone()[0] == 2


def test_the_backfill_counts_every_row_however_high_its_rowid():
    db = sqlite3.connect(":memory:")
    for migration in MIGRATIONS:
        if migration != COUNTS:
            db.executescript(migration.read_text())
    unit(db, "low")
    db.execute("UPDATE units SET rowid=900000 WHERE id='low'")
    unit(db, "one")
    db.executescript(COUNTS.read_text())
    agrees(db)
    assert db.execute("SELECT count(*) FROM unit_count_keys").fetchone()[0] == 2


def test_an_id_moved_onto_another_crop_leaves_one_crop_counted():
    db = sqlite3.connect(":memory:")
    for migration in MIGRATIONS:
        db.executescript(migration.read_text())
    unit(db, "one"); unit(db, "two")
    db.execute("UPDATE OR REPLACE units SET id='two' WHERE id='one'")
    agrees(db)
    db.execute("DELETE FROM units")
    agrees(db)
