"""Review batches must roll back completely when one crop changes concurrently."""
import json
import sqlite3
from pathlib import Path

import pytest


def database():
    db = sqlite3.connect(":memory:")
    db.execute("PRAGMA foreign_keys=ON")
    db.executescript(Path("apps/cloudflare/migrations/0001_catalogue.sql").read_text())
    for identity in ("one", "two"):
        data = json.dumps({"id": identity, "label": "ア", "reading": "ア", "state": "pending", "revision": 0})
        db.execute("INSERT INTO units VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            identity, "local", "ア", "ア", None, None, "manuscript", "kana", "pending", 0, 1, 1, 0,
            data, "{}", "{}", "{}"))
    db.commit()
    return db


def event(db, identity, revision):
    data = json.dumps({"id": identity, "label": "カ", "reading": "ア", "state": "checked", "revision": revision+1})
    db.execute("INSERT INTO events VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (
        "event-"+identity, "round", identity, "test", revision, "{}", data, "{}", "{}", "review", "now", 0))


def test_atomic_round_rejects_a_stale_member():
    db = database()
    with pytest.raises(sqlite3.IntegrityError, match="review_revision_conflict"), db:
        db.execute("INSERT INTO submissions VALUES(?,?,?,?,?,?)", ("round", "test", "{}", "{}", "now", 0))
        event(db, "one", 0)
        event(db, "two", 4)
    assert db.execute("SELECT count(*) FROM events").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM submissions").fetchone()[0] == 0
    assert db.execute("SELECT character,reading,revision FROM units WHERE id='one'").fetchone() == ("ア", "ア", 0)


def test_correction_updates_identity_without_overwriting_reading():
    db = database()
    with db:
        db.execute("INSERT INTO submissions VALUES(?,?,?,?,?,?)", ("round", "test", "{}", "{}", "now", 0))
        event(db, "one", 0)
    assert db.execute("SELECT character,reading,revision FROM units WHERE id='one'").fetchone() == ("カ", "ア", 1)
    assert db.execute("SELECT character,state,revision FROM units WHERE id='two'").fetchone() == ("ア", "pending", 0)
