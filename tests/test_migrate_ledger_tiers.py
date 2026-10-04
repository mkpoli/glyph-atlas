"""Migration 0069 and its local runner: the ledger keeps three tiers, each row its rowid, and stays immutable."""
import sqlite3
import sys
from pathlib import Path

import pytest

from glyph_atlas.review import ledger

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import migrate_ledger_tiers as migrate

FORMER = "'attested','observed','derived','editorial'"


def former_store(path: Path) -> None:
    """A store made before 0069: the ledger's table allows the former fourth tier and holds two such rows."""
    conn = sqlite3.connect(path, isolation_level=None)
    conn.execute("CREATE TABLE units (id TEXT PRIMARY KEY)")
    for ddl in ledger.DDL_PATHS:
        conn.executescript(ddl.read_text(encoding="utf-8").replace("'attested','observed','derived'", FORMER))
    rows = [("a1", "fm:1", "represented_by", "rp:1", None, "editorial", "u1"),
            ("a2", "doc", "date_composed", None, '{"of":"work"}', "editorial", "source:ainu-records"),
            ("a3", "hk:1", "has_form", "fm:1", None, "observed", "u1")]
    for rowid, row in zip((7, 3, 11), rows):
        conn.execute("INSERT INTO assertions(rowid,id,subject,predicate,object,value,tier,asserted_by,asserted_at) "
                     "VALUES(?,?,?,?,?,?,?,?,'t')", (rowid, *row))
    conn.close()


def test_a_former_store_is_reported_then_rebuilt(tmp_path):
    path = tmp_path / "review.sqlite"
    former_store(path)
    assert migrate.migrate(path, apply=False) == {"store": str(path), "ledger": True, "migrated": False, "rows": 3,
                                                  "retiered": 2}
    assert migrate.migrate(migrate.store_path(tmp_path), apply=True)["migrated"]
    conn = sqlite3.connect(path, isolation_level=None)
    assert conn.execute("SELECT rowid,id,tier FROM assertions ORDER BY rowid").fetchall() == [
        (3, "a2", "attested"), (7, "a1", "observed"), (11, "a3", "observed")]
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO assertions(id,subject,predicate,object,tier,asserted_by,asserted_at) "
                     "VALUES('a4','x','has_form','fm:1','editorial','u','t')")
    with pytest.raises(sqlite3.IntegrityError, match="ledger_immutable"):
        conn.execute("UPDATE assertions SET tier='derived' WHERE id='a1'")
    conn.execute("INSERT INTO assertion_evidence(assertion,kind,ref) VALUES('a1','source','x')")
    with pytest.raises(sqlite3.IntegrityError, match="ledger_immutable"):
        conn.execute("DELETE FROM assertion_evidence WHERE assertion='a1'")
    names = {name for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE tbl_name='assertions'")}
    assert {"assertion_slot", "assertion_object", "assertion_set", "assertion_actor", "assertion_subject",
            "assertion_kept", "assertion_kept_delete"} <= names
    conn.close()
    assert migrate.migrate(path, apply=True) == {"store": str(path), "ledger": True, "migrated": True, "rows": 3,
                                                 "retiered": 0}


def test_a_store_made_now_needs_nothing_and_one_without_a_ledger_is_left_alone(tmp_path):
    made = tmp_path / "made.sqlite"
    conn = sqlite3.connect(made)
    ledger.schema(conn)
    conn.close()
    assert migrate.migrate(made, apply=False)["migrated"] is True
    bare = tmp_path / "bare.sqlite"
    sqlite3.connect(bare).close()
    assert migrate.migrate(bare, apply=True) == {"store": str(bare), "ledger": False}
