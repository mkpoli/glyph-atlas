"""The grapheme export's SQL runs on the migrated D1 schema and moves crops and glyphs to their family."""
import importlib
import json
import sqlite3
from pathlib import Path

import pytest


@pytest.fixture
def module(monkeypatch):
    monkeypatch.syspath_prepend(str(Path("scripts").resolve()))
    return importlib.import_module("export_graphemes")


def schema():
    db = sqlite3.connect(":memory:")
    for migration in sorted(Path("apps/cloudflare/migrations").glob("*.sql")):
        db.executescript(migration.read_text())
    return db


def unit(db, id_, origin, character, family, data):
    db.execute("INSERT INTO units(id,origin,character,reading,family,visual_group,production,category,state,revision,"
               "quiz,priority,shuffle,data,snapshot,context,visual) VALUES(?,?,?,NULL,?,NULL,'unknown','han',"
               "'unreviewed',1,0,0,0,?,'{}','{}','{}')", (id_, origin, character, family, json.dumps(data)))


def test_the_swap_moves_the_characters_crops_and_glyphs_of_a_family(module):
    db = schema()
    for point, char in (("U+9084", "還"), ("U+2E7C3", "𮟃")):
        grapheme = {"code_point": point, "members": [{"code_point": point, "char": char}]}
        db.execute("INSERT INTO characters VALUES(?,?,'',?,'{}')", (point, char, json.dumps({"grapheme": grapheme})))
    unit(db, "l1", "local", "𮟃", "U+2E7C3", {})
    unit(db, "c1", "corpus", "𮟃", "U+2E7C3", {"grapheme": "U+2E7C3", "family_members": []})
    db.execute("INSERT INTO corpus_units(id,character,family,visual_group,shuffle,object,offset,size) "
               "VALUES('g1','𮟃','U+2E7C3',NULL,0,'o',0,1)")
    members = [{"code_point": "U+9084", "char": "還"}, {"code_point": "U+2E7C3", "char": "𮟃"}]
    data = {"grapheme": {"code_point": "U+9084", "members": members}}
    fill, swap = module.statements({"U+9084": (data, {}), "U+2E7C3": (data, {})})
    for sql in [*fill, *swap]:
        assert len(sql.encode()) < 100_000
    db.executescript("".join(fill))
    for sql in swap:
        if sql.startswith(("UPDATE units", "UPDATE corpus_units")):
            plan = " ".join(row[-1] for row in db.execute("EXPLAIN QUERY PLAN " + sql))
            assert "SCAN units" not in plan and "SCAN corpus_units" not in plan, plan
    db.executescript("".join(swap))
    assert db.execute("SELECT family FROM units ORDER BY id").fetchall() == [("U+9084",), ("U+9084",)]
    corpus = json.loads(db.execute("SELECT data FROM units WHERE id='c1'").fetchone()[0])
    assert corpus == {"grapheme": "U+9084", "family_members": members}
    assert db.execute("SELECT family FROM corpus_units").fetchone() == ("U+9084",)
    assert db.execute("SELECT count(*) FROM metadata WHERE key IN ('units_refreshed_at','corpus_counts_at')").fetchone() == (2,)
    assert db.execute("SELECT count(*) FROM sqlite_master WHERE name=?", (module.STAGING,)).fetchone() == (0,)
    db.executescript("".join(fill) + "".join(swap))  # a rerun changes nothing further
    assert db.execute("SELECT family FROM corpus_units").fetchone() == ("U+9084",)
