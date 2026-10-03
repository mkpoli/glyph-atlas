"""The grapheme export's SQL runs on the migrated D1 schema and moves crops and glyphs to their family."""
import importlib
import json
import sqlite3
from pathlib import Path

import pytest

from glyph_atlas import refs


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
    db.execute("INSERT INTO units(id,origin,character,family,visual_group,production,category,state,revision,"
               "quiz,priority,shuffle,data,snapshot,context,visual) VALUES(?,?,?,?,NULL,'unknown','han',"
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


def family_members() -> list[str]:
    return refs.graphemes()["U+9084"]


def test_a_row_without_counts_of_its_own_still_gets_the_family_flags(module):
    """𮟃 joins 還's family and has no corpus counts of its own; its gallery still needs the family."""
    members = family_members()
    live = {point: {"data": {"candidates": {"known": False, "sources": [], "glyphs": None}},
                    "detail": {}} for point in members}
    live["U+9084"]["data"]["candidates"] = {"known": True, "sources": ["codh", "hilab"], "glyphs": 12}
    data, detail = module.rewritten("U+2E7C3", live, {point: 0 for point in members})
    assert data["candidates"]["requires_family_scope"] is True
    assert data["candidates"]["family_glyphs"] == 12
    assert data["default_scope"] == "grapheme"
    assert detail["grapheme"]["code_point"] == "U+9084"
    # Without any member from a normalized corpus the flags say so instead of staying absent.
    live["U+9084"]["data"]["candidates"] = {"known": True, "sources": ["hilab"], "glyphs": 12}
    data, _ = module.rewritten("U+2E7C3", live, {point: 0 for point in members})
    assert data["candidates"]["requires_family_scope"] is False


def test_a_stored_analysis_from_before_the_family_grew_is_not_reused(module, monkeypatch):
    members = family_members()
    stale = {"status": "ready", "family": "U+9084", "members": ["U+9084"], "sample_count": 9,
             "assigned_count": 4, "unassigned_count": 5, "groups": [{"id": "old"}], "model_revision": "r1"}
    live = {"U+9084": {"detail": {"visual_analysis": stale}}}
    monkeypatch.setattr(module.visual_families, "family_analysis", lambda point: dict(stale))
    assert module._visual_analysis("U+2E7C3", "U+9084", live, members)["status"] == "not_analyzed"
    covered = {**stale, "members": members}
    monkeypatch.setattr(module.visual_families, "family_analysis",
                        lambda point: {"status": "not_analyzed", "family": "U+9084", "model_revision": "r1",
                                       "sample_count": 0, "assigned_count": 0, "unassigned_count": 0,
                                       "groups": []})
    live = {"U+9084": {"detail": {"visual_analysis": covered}}}
    reused = module._visual_analysis("U+2E7C3", "U+9084", live, members)
    assert reused["members"] == members and reused["status"] == "ready"


def test_a_family_that_only_moves_its_head_is_still_a_change(module, tmp_path, monkeypatch):
    path = tmp_path / "before.tsv"
    path.write_text("code_point\tgrapheme\nU+0041\tU+0041\nU+0042\tU+0041\n", encoding="utf-8")
    previous = module.previous_families(path)
    monkeypatch.setattr(module.refs, "graphemes", lambda: {"U+0041": ["U+0041", "U+0042"]})
    assert module.changed(previous) == []
    monkeypatch.setattr(module.refs, "graphemes", lambda: {"U+0042": ["U+0041", "U+0042"]})
    assert module.changed(previous) == ["U+0041", "U+0042"]


def insert_forms(db):
    db.execute("INSERT INTO form_families(code_point,char,label,count,cluster_count,forms,assigned,revision) "
               "VALUES('U+2E7C3','𮟃','x',0,0,'{}',0,'r'),('U+9084','還','y',0,0,'{}',0,'r')")
    db.execute("INSERT INTO form_clusters(id,family,label,count,coherence,shape_position,size_position,"
               "representatives) VALUES('k1','U+2E7C3','c',1,0.9,0,0,'[]')")
    db.execute("INSERT INTO form_units(id,family,cluster,rank,similarity,split) "
               "VALUES('f1','U+2E7C3','k1',0,1.0,'')")
    db.execute("INSERT INTO form_decisions(seq,id,at,actor,kind,family,revision,units,note) "
               "VALUES(1,'d1','a','x','set','U+2E7C3','r','[]','')")
    db.execute("INSERT INTO form_bases(id,character,family) VALUES('g1','𮟃','U+2E7C3')")


def test_the_swap_moves_forms_codes_and_a_local_records_grapheme(module):
    db = schema()
    for point, char in (("U+9084", "還"), ("U+2E7C3", "𮟃")):
        grapheme = {"code_point": "U+9084", "members": [{"code_point": "U+9084", "char": "還"},
                                                        {"code_point": "U+2E7C3", "char": "𮟃"}]}
        db.execute("INSERT INTO characters VALUES(?,?,'',?,'{}')", (point, char, json.dumps({"grapheme": grapheme})))
    unit(db, "l1", "local", "𮟃", "U+2E7C3", {"grapheme": "U+2E7C3"})
    # the head was already right on this corpus row, but its family_members still names the old family
    unit(db, "c2", "corpus", "還", "U+9084", {"grapheme": "U+9084", "family_members": []})
    insert_forms(db)
    members = [{"code_point": "U+9084", "char": "還"}, {"code_point": "U+2E7C3", "char": "𮟃"}]
    data = {"grapheme": {"code_point": "U+9084", "members": members}}
    fill, swap = module.statements({"U+9084": (data, {}), "U+2E7C3": (data, {})})
    db.executescript("".join(fill) + "".join(swap))
    assert json.loads(db.execute("SELECT data FROM units WHERE id='l1'").fetchone()[0]) == {"grapheme": "U+9084"}
    corpus = json.loads(db.execute("SELECT data FROM units WHERE id='c2'").fetchone()[0])
    assert corpus == {"grapheme": "U+9084", "family_members": members}
    assert db.execute("SELECT family FROM form_units").fetchone() == ("U+9084",)
    assert db.execute("SELECT family FROM form_clusters").fetchone() == ("U+9084",)
    assert db.execute("SELECT family FROM form_decisions").fetchone() == ("U+9084",)
    assert db.execute("SELECT family FROM form_bases").fetchone() == ("U+9084",)
    # the moved head's row folds into the head it joins, whose row stays
    assert db.execute("SELECT code_point FROM form_families").fetchall() == [("U+9084",)]


def test_a_sequence_the_site_lacks_is_inserted_and_its_crops_follow(module):
    """𛂞 + U+3099 has no row on the site yet: the swap writes it with its aliases, and files a crop
    written as it, and the バ crops, under ば."""
    db = schema()
    ha = "\U0001B09E゙"
    for point, char in (("U+3070", "ば"), ("U+30D0", "バ")):
        db.execute("INSERT INTO characters VALUES(?,?,'',?,'{}')", (point, char, json.dumps({"grapheme": {"code_point": point}})))
    unit(db, "l1", "local", "バ", "U+30D0", {})
    unit(db, "l2", "local", ha, "U+1B09E U+3099", {})
    data = {"grapheme": {"code_point": "U+3070", "members": refs.grapheme_info("U+3070")["members"]}}
    rows = {point: (data, {}) for point in ("U+3070", "U+30D0", "U+1B09E U+3099")}
    fill, swap = module.statements(rows)
    db.executescript("".join(fill) + "".join(swap))
    assert db.execute("SELECT character,json_extract(data,'$.grapheme.code_point') FROM characters"
                      " WHERE code_point='U+1B09E U+3099'").fetchone() == (ha, "U+3070")
    assert {row[0] for row in db.execute("SELECT query FROM aliases WHERE code_point='U+1B09E U+3099'")} == {
        ha, "u+1b09e u+3099"}
    assert db.execute("SELECT family FROM units ORDER BY id").fetchall() == [("U+3070",), ("U+3070",)]
    db.executescript("".join(fill) + "".join(swap))  # a rerun inserts nothing twice
    assert db.execute("SELECT count(*) FROM characters").fetchone() == (3,)


def test_a_fresh_row_is_shaped_like_a_published_one(module):
    found = module.fresh("U+1B09E U+3099")
    assert found["data"]["grapheme"]["code_point"] == "U+3070"
    assert found["data"]["kind"] == "kana" and found["data"]["char"] == "\U0001B09E゙"
    data, detail = module.rewritten("U+1B09E U+3099", {"U+1B09E U+3099": found}, {})
    assert detail["characters"][0]["code_point"] == "U+1B09E U+3099"
    assert {row["code_point"] for row in detail["characters"]} == set(refs.graphemes()["U+3070"])
