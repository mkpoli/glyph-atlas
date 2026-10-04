"""The 異体字 graph as a character card lists it, and as the site's `character_variants` table holds it."""

import glob
import importlib.util
import json
import sqlite3
from pathlib import Path

from glyph_atlas import refs
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
    assert card["sources"]["wikidata"].startswith("Wikidata, property P5475")


def test_a_simplified_pair_is_kept_apart_from_the_widening():
    items, related = characters.variant_pairs("干")
    assert {"乾", "幹"} <= {row["char"] for row in related}, "four sources give them as simplifications"
    assert not {"乾", "幹"} & {row["char"] for row in items}
    items, _ = characters.variant_pairs("刻")
    assert "𠛳" in [row["char"] for row in items], "a specialized-semantic edge alone does not keep a pair apart"


def migrated() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    for path in sorted(glob.glob(str(ROOT / "apps/cloudflare/migrations/*.sql"))):
        db.executescript(Path(path).read_text(encoding="utf-8"))
    return db


def test_the_parts_fill_a_staging_table_and_only_the_last_swaps_it_in(tmp_path, monkeypatch):
    # The whole derived tier takes half a minute; a few rows of it stand in.
    rows = (("寰", 0, "⿱宂睘", '[[["宀","宂"]]]'), ("還", 0, "⿺廴睘", '[[["廴","辶"]]]'))
    monkeypatch.setattr(export, "derived_rows", lambda: rows)
    db = migrated()
    db.execute("INSERT INTO character_variants VALUES('a','b','variant','x','',1,1)")
    paths = export.write_parts(tmp_path, export.statements())
    assert len(paths) >= 2
    for path in paths[:-1]:
        text = path.read_text(encoding="utf-8")
        assert "DELETE FROM character_variants;" not in text
        assert max(len(line.encode()) for line in text.splitlines()) <= export.STATEMENT_BYTES + 1024
        db.executescript(text)
    assert db.execute("SELECT count(*) FROM character_variants").fetchone() == (1,), "the live table is untouched"
    db.executescript(paths[-1].read_text(encoding="utf-8"))
    want = export.expected()
    assert db.execute("SELECT count(*), sum(written), sum(widens) FROM character_variants").fetchone() == (
        want["edges"], want["written"], want["widens"])
    assert db.execute("SELECT written, widens FROM character_variants WHERE a='克' AND b='刻'").fetchone() == (0, 0)
    for staging in (export.STAGING, export.SUBSTITUTIONS_STAGING, export.DERIVED_STAGING, export.WORDS_STAGING,
                    export.SPELLINGS_STAGING):
        assert db.execute("SELECT name FROM sqlite_master WHERE name=?", (staging,)).fetchone() is None
    assert db.execute("SELECT count(*) FROM words").fetchone() == (want["words"],)
    assert db.execute("SELECT count(*) FROM word_spellings").fetchone() == (want["spellings"],)
    assert db.execute("SELECT documents FROM word_spellings WHERE spelling='斗' AND source='honkoku-ruby'").fetchone()[0] >= 2
    assert db.execute("SELECT documents FROM word_spellings WHERE source='wiktionary-ja' LIMIT 1").fetchone() == (None,)
    assert "honkoku-ruby" in db.execute("SELECT value FROM metadata WHERE key='word_sources'").fetchone()[0]
    cited = db.execute("SELECT value FROM metadata WHERE key='variant_sources'").fetchone()[0]
    assert "cjkvi-variants" in cited and "derived-ids" in cited
    assert db.execute("SELECT count(*) FROM component_variants").fetchone() == (want["substitutions"],)
    assert db.execute("SELECT rank, b, routes FROM character_derived WHERE a='寰'").fetchall() == [
        (0, "⿱宂睘", '[[["宀","宂"]]]')]
    pairs = json.loads(db.execute("SELECT pairs FROM component_variants WHERE a='コ' AND b='龴'").fetchone()[0])
    assert pairs == [{"a": "コ", "b": "龴", "sources": ["mkpoli-2026-10-04"]}]
    assert "mkpoli-2026-10-04" in cited
    pairs = db.execute("SELECT pairs FROM component_variants WHERE a='宀' AND b='宂'").fetchone()[0]
    assert ("㝓", "䆟") in {(p["a"], p["b"]) for p in json.loads(pairs)}



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
