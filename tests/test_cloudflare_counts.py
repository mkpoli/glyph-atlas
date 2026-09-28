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
    db.execute("INSERT OR IGNORE INTO units VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
        identity, "local", character, character, family, None, "handwritten", "kana", state, 0, quiz, 1, 0,
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
