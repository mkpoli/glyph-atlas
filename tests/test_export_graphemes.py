"""The cards of the reference input follow the character table's families."""
import importlib
import json
import sqlite3
import sys
from pathlib import Path

import pytest

from glyph_atlas import reference_input, refs


@pytest.fixture
def module(monkeypatch):
    monkeypatch.syspath_prepend(str(Path("scripts").resolve()))
    return importlib.import_module("export_graphemes")


def card(point, char, head, members):
    grapheme = {"code_point": head, "members": [{"code_point": m} for m in members]}
    return (point, char, "", json.dumps({"grapheme": grapheme, "occurrence_count": 1}), "{}")


def test_a_family_that_only_moves_its_head_is_still_a_change(module, monkeypatch):
    cards = {p: {"data": json.loads(card(p, "", "U+0041", ["U+0041", "U+0042"])[3])} for p in ("U+0041", "U+0042")}
    monkeypatch.setattr(module.refs, "graphemes", lambda: {"U+0041": ["U+0041", "U+0042"]})
    assert module.changed(cards) == []
    monkeypatch.setattr(module.refs, "graphemes", lambda: {"U+0042": ["U+0041", "U+0042"]})
    assert module.changed(cards) == ["U+0041", "U+0042"]


def test_a_character_with_no_card_is_a_change(module, monkeypatch):
    cards = {"U+0041": {"data": json.loads(card("U+0041", "A", "U+0041", ["U+0041"])[3])}}
    monkeypatch.setattr(module.refs, "graphemes", lambda: {"U+0041": ["U+0041"], "U+17000": ["U+17000"]})
    assert module.changed(cards) == ["U+17000"]


def run(module, monkeypatch, path, *flags):
    monkeypatch.setattr(sys, "argv", ["export_graphemes.py", "--input", str(path), *flags])
    module.main()
    return sqlite3.connect(path)


def test_the_cards_of_a_catalogue_replace_the_inputs(module, monkeypatch, tmp_path):
    catalogue = tmp_path / "catalogue.sqlite"
    db = sqlite3.connect(catalogue)
    db.execute("CREATE TABLE characters(code_point TEXT PRIMARY KEY, character TEXT, name TEXT, data TEXT, detail TEXT)")
    db.execute("INSERT INTO characters VALUES(?,?,?,?,?)", card("U+9084", "還", "U+9084", ["U+9084"]))
    db.execute("CREATE TABLE aliases(query TEXT, code_point TEXT, rank INTEGER, PRIMARY KEY(query, code_point))")
    db.execute("INSERT INTO aliases VALUES('かえる','U+9084',1)")
    db.commit()
    path = tmp_path / "reference-input.sqlite"
    with reference_input.writing(path) as held:
        reference_input.replace(held, "characters", [card("U+3042", "あ", "U+3042", ["U+3042"])])
        reference_input.replace(held, "aliases", [("あ", "U+3042", 0)])
    got = run(module, monkeypatch, path, "--catalogue", str(catalogue))
    assert got.execute("SELECT code_point, character FROM characters").fetchall() == [("U+9084", "還")]
    assert got.execute("SELECT * FROM aliases").fetchall() == [("かえる", "U+9084", 1)]


def test_the_search_names_come_from_their_own_copy_when_the_cards_file_lacks_them(module, monkeypatch, tmp_path):
    cards, names = tmp_path / "source-characters.sqlite", tmp_path / "source-aliases.sqlite"
    db = sqlite3.connect(cards)
    db.execute("CREATE TABLE characters(code_point TEXT PRIMARY KEY, character TEXT, name TEXT, data TEXT, detail TEXT)")
    db.execute("INSERT INTO characters VALUES(?,?,?,?,?)", card("U+9084", "還", "U+9084", ["U+9084"]))
    db.commit()
    db = sqlite3.connect(names)
    db.execute("CREATE TABLE aliases(query, code_point, rank)")
    db.execute("INSERT INTO aliases VALUES('u+9084','U+9084',0)")
    db.commit()
    got = run(module, monkeypatch, tmp_path / "reference-input.sqlite", "--catalogue", str(cards), "--aliases", str(names))
    assert got.execute("SELECT * FROM aliases").fetchall() == [("u+9084", "U+9084", 0)]


def test_a_family_that_grew_is_rewritten_and_a_sequence_the_input_lacks_is_added(module, monkeypatch, tmp_path):
    """Every card is in step with the table but ば's, whose family the cards hold as ば alone, and
    𛂞 + U+3099 and one character of a family of its own have no card yet. Every held card has a name
    but one, which an earlier run added."""
    path = tmp_path / "reference-input.sqlite"
    family = refs.graphemes()["U+3070"]
    lone = next(head for head, members in refs.graphemes().items() if members == [head] and " " not in head)
    kept = [card(p, refs.to_char(p), head, members) for head, members in refs.graphemes().items()
            for p in members if head not in ("U+3070", lone)]
    held = [card("U+3070", "ば", "U+3070", ["U+3070"])] + [
        card(p, refs.to_char(p), "U+3070", family) for p in family if p not in ("U+3070", "U+1B09E U+3099")]
    unnamed = kept[0][0]
    names = [(r[1], r[0], 3) for r in held + kept if r[0] != unnamed]
    with reference_input.writing(path) as db:
        reference_input.replace(db, "characters", held + kept)
        reference_input.replace(db, "aliases", names)
    got = run(module, monkeypatch, path)
    rows = {p: json.loads(d) for p, d in got.execute(
        f"SELECT code_point, data FROM characters WHERE code_point IN ({','.join('?' * len(family))})", family)}
    assert set(rows) == set(family)
    assert all(rows[p]["grapheme"]["code_point"] == "U+3070" for p in family)
    assert all({m["code_point"] for m in rows[p]["grapheme"]["members"]} == set(family) for p in family)
    assert got.execute("SELECT character FROM characters WHERE code_point='U+1B09E U+3099'").fetchone() == ("\U0001B09E゙",)
    # The new characters and the held card without a name are found by their text and code points; the
    # named cards keep their names and ranks.
    assert sorted(got.execute("SELECT * FROM aliases")) == sorted([
        *names, ("\U0001B09E゙", "U+1B09E U+3099", 0), ("u+1b09e u+3099", "U+1B09E U+3099", 0),
        (refs.to_char(lone), lone, 0), (lone.lower(), lone, 0), (kept[0][1], unnamed, 0), (unnamed.lower(), unnamed, 0)])
    assert got.execute("SELECT count(*) FROM characters").fetchone() == (len(kept) + len(family) + 1,)
    assert sorted(got.execute(f"SELECT * FROM characters WHERE code_point NOT IN ({','.join('?' * (len(family) + 1))})",
                              [*family, lone])) == sorted(kept), "the other cards stay as they were"
    got.close()
    # A rerun finds every card in step with the table.
    with reference_input.writing(path) as db:
        assert module.changed(module.held_cards(db)) == []


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


def test_a_fresh_row_is_shaped_like_a_published_one(module):
    found = module.fresh("U+1B09E U+3099")
    assert found["data"]["grapheme"]["code_point"] == "U+3070"
    assert found["data"]["kind"] == "kana" and found["data"]["char"] == "\U0001B09E゙"
    _, detail = module.rewritten("U+1B09E U+3099", {"U+1B09E U+3099": found}, {})
    assert detail["characters"][0]["code_point"] == "U+1B09E U+3099"
    assert {row["code_point"] for row in detail["characters"]} == set(refs.graphemes()["U+3070"])
