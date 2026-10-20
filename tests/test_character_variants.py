"""The 異体字 graph as a character card lists it, and as the site's `character_variants` table holds it."""

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

from glyph_atlas import reference_input, refs
from glyph_atlas.review import characters

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("export_character_variants", ROOT / "scripts/export_character_variants.py")
export = importlib.util.module_from_spec(spec)
spec.loader.exec_module(export)


def test_every_edge_is_kept_and_only_written_relations_are_written():
    edges = refs.variant_edges_of("刻")
    assert {(e["a"], e["b"], e["relation"], e["written"]) for e in edges} >= {
        ("刻", "𠚰", "variant", True), ("克", "刻", "borrowed", False), ("刻", "剋", "borrowed", False)}
    assert len(refs.variant_edges()) == sum(1 for line in (refs.VOCAB / refs.VARIANTS_TSV).open(encoding="utf-8")
                                            if not line.startswith("#")) - 1


def test_the_card_lists_written_variants_apart_from_related_characters(monkeypatch):
    monkeypatch.setattr(characters.corpus_source, "counts", lambda chars: {"𠚰": {"n_glyphs": 3}})
    card = characters.variant_card("刻", {"U+206B0": 2})
    items = [row["char"] for row in card["items"]]
    assert items[:4] == ["𠚰", "𠛳", "𠜇", "𠞫"]
    assert [row["char"] for row in card["related"]] == ["克", "剋"]
    first = card["items"][0]
    assert first["sources"] == ["cjkvi-variants", "mj-shrink-map", "wikidata"]
    assert (first["count"], first["corpus_count"]) == (2, 3)
    assert {r["relation"] for r in card["related"][0]["relations"]} == {"borrowed"}
    assert all(row["written"] for row in card["items"]) and not any(row["written"] for row in card["related"])
    assert card["sources"]["wikidata"].startswith("Wikidata, property P5475")


def test_a_simplified_pair_is_kept_apart_from_the_widening():
    items, related = characters.variant_pairs("干")
    assert all(row["written"] for row in related if row["char"] in "乾幹"), "a simplification is written, though apart"
    assert {"乾", "幹"} <= {row["char"] for row in related}, "four sources give them as simplifications"
    assert not {"乾", "幹"} & {row["char"] for row in items}
    items, _ = characters.variant_pairs("刻")
    assert "𠛳" in [row["char"] for row in items], "a specialized-semantic edge alone does not keep a pair apart"


def test_the_graph_replaces_every_table_of_the_reference_input(tmp_path, monkeypatch):
    # The whole derived tier takes minutes; a few rows of it stand in.
    rows = (("寰", '[["⿱宂睘",[[["宀","宂"]]]]]'), ("還", '[["⿺廴睘",[[["廴","辶"]]]]]'),
            ("鬱", '[["⿳木缶⿰木鬯",[[["林","木"]]]]]'))
    ids = (("寰", '["⿱宀睘"]', '[{"text":"⿱宀睘","regions":"G"},{"text":"⿱宂睘","regions":"J"}]'), ("還", '["⿺辶睘"]', None))
    monkeypatch.setattr(export, "derived_rows", lambda: rows)
    monkeypatch.setattr(export, "ids_rows", lambda: ids)
    path = tmp_path / "reference-input.sqlite"
    with reference_input.writing(path) as db:
        db.execute("INSERT INTO character_variants VALUES('a','b','variant','x','',1,1)")
        db.execute("INSERT INTO character_derived VALUES('㐀','[]')")
    monkeypatch.setattr(sys, "argv", ["export_character_variants.py", "--input", str(path)])
    export.main()
    db = sqlite3.connect(path)
    edges = {(e["a"], e["b"], e["relation"], e["source"]): e for e in refs.variant_edges()}
    assert db.execute("SELECT count(*), sum(written), sum(widens) FROM character_variants").fetchone() == (
        len(edges), sum(e["written"] for e in edges.values()), sum(e["widens"] for e in edges.values()))
    assert db.execute("SELECT count(*) FROM character_variants WHERE a='a'").fetchone() == (0,), "an earlier row goes"
    assert db.execute("SELECT written, widens FROM character_variants WHERE a='克' AND b='刻'").fetchone() == (0, 0)
    assert db.execute("SELECT a, forms FROM character_derived ORDER BY a").fetchall() == list(rows)
    assert db.execute("SELECT char, sequences, shapes FROM han_ids ORDER BY char").fetchall() == list(ids)
    assert db.execute("SELECT count(*) FROM words").fetchone() == (len(export.word_rows()),)
    assert db.execute("SELECT count(*) FROM word_spellings").fetchone() == (len(export.spelling_rows()),)
    assert db.execute("SELECT documents FROM word_spellings WHERE spelling='斗' AND source='honkoku-ruby'").fetchone()[0] >= 2
    assert db.execute("SELECT documents FROM word_spellings WHERE source='wiktionary-ja' LIMIT 1").fetchone() == (None,)
    cited = dict(db.execute("SELECT key, value FROM sources"))
    assert "honkoku-ruby" in cited["word_sources"]
    assert all(s in cited["variant_sources"] for s in ("cjkvi-variants", "derived-ids", "mkpoli-2026-10-04"))
    assert db.execute("SELECT count(*) FROM component_variants").fetchone() == (len(export.substitution_rows()),)
    pairs = db.execute("SELECT pairs FROM component_variants WHERE a='宀' AND b='宂'").fetchone()[0]
    assert ("㝓", "䆟") in {(p["a"], p["b"]) for p in json.loads(pairs)}
    assert {name for (name,) in db.execute("SELECT name FROM written")} == {
        "character_variants", "component_variants", "character_derived", "han_ids", "grapheme_writers", "words",
        "word_spellings", "sources"}


def test_a_gallery_widens_to_exactly_the_cards_first_row():
    assert characters.split_expansions("variants") == {"variants"}
    points = characters.variant_code_points("U+523B")
    card = characters.variant_card("刻", {})
    assert points == [row["code_point"] for row in card["items"]]
    assert "U+514B" not in points, "克 is a borrowed character, not a variant"
    assert not {"U+4E7E", "U+5E79"} & set(characters.variant_code_points("U+5E72")), "干 keeps 乾 and 幹 apart"


def test_the_local_corpus_side_widens_in_character_then_id_order(monkeypatch):
    answers = {"刻": [{"id": "b"}, {"id": "a"}], "𠚰": [{"id": "c"}]}
    monkeypatch.setattr(characters.corpus_source, "candidates",
                        lambda char, limit, offset, **kw: {"items": answers.get(char, []), "total": len(answers.get(char, []))})
    found, fault = characters._widened_corpus(["𠚰", "刻"], 2, 1, None)
    assert fault is None and found["total"] == 3
    assert [item["id"] for item in found["items"]] == ["b", "c"]


def test_a_derived_form_joins_neither_the_widening_nor_the_attested_rows(monkeypatch):
    monkeypatch.setattr(characters.corpus_source, "counts", lambda chars: {})
    card = characters.variant_card("寰", {})
    assert card["derived"] and card["sources"][refs.DERIVED_IDS].startswith("Predicted component variants")
    attested = {row["char"] for row in card["items"] + card["related"]}
    assert not attested & {row["char"] for row in card["derived"]}
    widened = set(characters.variant_code_points("U+5BF0"))
    assert not widened & {row["code_point"] for row in card["derived"] if row["code_point"]}



def test_the_card_lists_the_words_a_character_writes():
    card = characters.word_card("斗")
    (word,) = card["items"]
    assert (word["id"], word["reading"]) == ("ja/ばかり/副助詞", "ばかり")
    spellings = [entry["spelling"] for entry in word["spellings"]]
    assert {"計", "許", "斗"} <= set(spellings) and spellings[0] == "許", "most cited first"
    assert [entry["current"] for entry in word["spellings"]].count(True) == 1
    assert "chiebukuro-garan-2022" in card["sources"]
    assert characters.word_card("盃") == {"items": [], "sources": {}}


def test_a_spelling_counted_under_two_readings_names_each():
    (word,) = characters.word_card("等")["items"]
    (pou,) = [entry for entry in word["spellings"] if entry["spelling"] == "抔"]
    assert {s["ruby"] for s in pou["sources"] if s["source"] == "honkoku-ruby"} == {"など", "なと"}
    assert all(s["ruby"] is None for s in pou["sources"] if s["source"] != "honkoku-ruby")


def test_the_card_lists_the_graphemes_a_reduced_form_writes_and_the_forms_that_write_its_own(monkeypatch):
    monkeypatch.setattr(characters.corpus_source, "counts", lambda chars: {})
    assert characters.variant_card("后", {})["writes"] == [{"code_point": "U+5F8C", "char": "後"}]
    assert characters.variant_card("後", {})["writers"] == [{"code_point": "U+540E", "char": "后", "shared": True}]
    stand_in = characters.variant_card("𠮮", {})
    assert [w["char"] for w in stand_in["writes"]] == ["后", "𠯑"] and stand_in["own"] is False
    assert {w["char"]: w["shared"] for w in characters.variant_card("后", {})["writers"]}["𠮮"] is True
    card = characters.variant_card("时", {})
    assert card["writes"] == [] and card["writers"] == []
