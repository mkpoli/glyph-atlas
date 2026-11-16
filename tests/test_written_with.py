"""Characters of another script a grapheme is also written with: the table and the card rows it gives."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from glyph_atlas import refs, written_with
from glyph_atlas.review import characters

ROOT = Path(__file__).resolve().parents[1]
KATAKANA_KOTO, HIRAGANA_KOTO = "ヿ", "\U0001b123"


def test_the_table_names_each_character_its_grapheme_and_what_states_it():
    assert [writer.char for writer in written_with.writers("事")] == [KATAKANA_KOTO, HIRAGANA_KOTO]
    assert written_with.writers("也") == []
    koto = written_with.writes(KATAKANA_KOTO)
    assert (koto.grapheme, koto.relation, koto.tier, koto.code_point) == ("事", "semantic", "attested", "U+30FF")
    # No source names 事 for the hiragana digraph: the link is the table's, and says so.
    assert written_with.writes(HIRAGANA_KOTO).tier == "editorial"
    # Each grapheme is the head of its family, and each relation a name the variant graph uses.
    for writer in written_with.writers("事"):
        assert refs.grapheme_info(refs.to_code_point(writer.grapheme))["code_point"] == refs.to_code_point(writer.grapheme)
        assert writer.relation in refs.WRITTEN_FOR
    assert written_with.writes("事") is None
    # Every statement cites a source the table describes.
    cited = written_with.sources()
    for writer in written_with.writers("事"):
        assert writer.evidence and all(source in cited and detail for source, detail in writer.evidence)


def test_a_graphemes_card_lists_them_as_shared_forms_in_their_own_script(monkeypatch):
    monkeypatch.setattr(characters.corpus_source, "safe", lambda *args, **kwargs: ({}, None))
    card = characters.variant_card("事", {"U+30FF": 3})
    rows = {row["char"]: row for row in card["related"]}
    assert rows[KATAKANA_KOTO] == {
        "char": KATAKANA_KOTO, "code_point": "U+30FF", "widens": False, "written": True,
        "relations": written_with.writes(KATAKANA_KOTO).relations,
        "sources": ["en-wiktionary-koto", "ja-wikipedia-koto"], "count": 3, "corpus_count": 0}
    assert rows[HIRAGANA_KOTO]["sources"] == ["l2-24-150"]
    assert {"code_point": "U+30FF", "char": KATAKANA_KOTO, "shared": True} in card["writers"]
    assert {"code_point": "U+1B123", "char": HIRAGANA_KOTO, "shared": True} in card["writers"]
    assert card["sources"]["l2-24-150"].startswith("Gen Kojitani")
    # No gallery widens to them, and a member of the family lists them too.
    assert all(row["char"] not in (KATAKANA_KOTO, HIRAGANA_KOTO) for row in card["items"])
    assert KATAKANA_KOTO in {row["char"] for row in characters.variant_card("亊", {})["related"]}
    # The character's own card names the grapheme it also writes, and stays its own.
    own = characters.variant_card(KATAKANA_KOTO, {})
    assert [write["char"] for write in own["writes"]] == ["事"] and own["own"] is True
    assert sorted(own["sources"]) == ["en-wiktionary-koto", "ja-wikipedia-koto"]
    assert all(row["char"] != "事" for row in own["related"])
    # A grapheme the table does not name is as it was.
    assert not [row for row in characters.variant_card("也", {})["related"] if row["char"] in (KATAKANA_KOTO, HIRAGANA_KOTO)]


def test_the_workers_table_is_that_of_the_yaml():
    spec = importlib.util.spec_from_file_location("build_written_with", ROOT / "scripts" / "build_written_with.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    assert build.MODULE.read_text(encoding="utf-8") == build.module()
