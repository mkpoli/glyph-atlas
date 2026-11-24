"""A small seal character is a form of its Han character's grapheme (decision 0015).

Each of them is a member of the family, and none changes what the Han members were: which forms a
normalised corpus may write, a clustering's forms, a class the classifier votes for, or the scope a
gallery opens on."""

import importlib
from collections import Counter
from pathlib import Path

import pytest

from glyph_atlas import form_clusters, ngrams, refs, tables, visual_samples
from glyph_atlas.corpus import CorpusAPI, build_chars
from glyph_atlas.corpus.identity import identity_fields
from glyph_atlas.importers.codh import classification_of
from glyph_atlas.review import atlas, characters, suggestions
from glyph_atlas.schema import Box, Classification, Document, Page, Unit

#: 𾷱 of 大, 𾛷 of 假 (in 仮's family), 𽌇 of 萅 (in 春's) and 𽀅 of 叓.
DAI, KA, CHUN, LI = "\U0003EDB1", "\U0003E6F7", "\U0003D307", "\U0003D005"


def chars(point: str) -> list[str]:
    return [member["char"] for member in refs.grapheme_info(point)["members"]]


def stated() -> list[dict]:
    return [row for row in refs._read_tsv(refs.VARIANTS_TSV) if row["relation"] == "small-seal"]


def test_every_small_seal_character_is_under_the_grapheme_of_its_equivalent():
    rows = stated()
    assert len(rows) == len({row["b"] for row in rows}) == 11_328 == len(refs.SEAL_BLOCK)
    for row in rows:
        han, seal = refs.character(refs.to_code_point(row["a"])), refs.character(refs.to_code_point(row["b"]))
        assert (str(seal.script), str(han.script)) == ("seal", "han")
        assert refs.grapheme(seal.code_point) == refs.grapheme(han.code_point) != seal.code_point
    evidence = [row for row in refs._read_tsv(refs.FAMILIES_TSV) if row["role"] == refs.SEAL]
    assert {(row["a"], row["b"]) for row in evidence} == {(row["a"], row["b"]) for row in rows}
    assert all(row["head"] == refs.grapheme(refs.to_code_point(row["b"])) for row in evidence)


def test_a_seal_character_joins_through_the_head_and_by_the_stated_equivalent_alone():
    assert refs.grapheme("U+3D307") == refs.grapheme("U+8405") == "U+6625", "𽌇 joins 春 through 萅"
    # 叓 is its own grapheme; that 𽀅 is the seal form of 吏 is inferred, and joins nothing.
    assert refs.grapheme("U+3D005") == "U+53D3" and refs.grapheme("U+540F") == "U+540F"
    assert chars("U+53D3") == ["叓", LI] and LI not in chars("U+540F")
    # Several of one Han character are all its members.
    assert chars("U+6C11")[-3:] == ["\U0003F54D", "\U0003F54E", "\U0003F54F"]


def test_seal_members_come_last_and_after_the_drawn_forms():
    for head in refs.graphemes():
        seals = [refs.is_small_seal(char) for char in chars(head)]
        assert seals == sorted(seals), head
    assert chars("U+4E8B")[-4:] == ["\U000F0000", "\U000F0001", "\U000F0002", "\U0003D8B7"]


def test_a_seal_character_changes_no_relation_of_the_family():
    assert refs.grapheme_info("U+3E6F7")["relation"] == "shinjitai-kyujitai"
    assert refs.grapheme_info("U+3D307")["relation"] == "shape-variant"
    info = refs.grapheme_info("U+5927")
    assert (info["relation"], info["label"], info["character_count"]) == ("small-seal", f"大 = {DAI}", 2)
    assert info["evidence"] == [{"id": "unicode-ucd", "title": info["evidence"][0]["title"], "url": info["evidence"][0]["url"],
                                 "edges": [{"a": "大", "b": DAI, "relation": "small-seal", "role": "seal",
                                            "detail": f"{DAI}→大 kSEAL_MCJK"}]}]
    # 萅's seal edge attests no Han form: the order of 春's forms is the sources' alone.
    assert chars("U+6625")[:4] == ["春", "旾", "㫩", "萅"]
    assert refs.curated_members("U+3E6F7") == ("仮", "假")


def test_a_run_is_spelled_by_the_han_head():
    assert [ngrams.fold(char) for char in (CHUN, LI, KA)] == ["春", "叓", "仮"]


def test_a_normalised_corpus_is_no_less_sure_of_a_han_character_with_a_seal_form():
    sure = identity_fields({"unicode": "U+5927", "text_source": "大"}, "codh-full")
    assert (sure["written_character"], sure["identity_status"], sure["requires_family_scope"]) == ("大", "assigned", False)
    assert [member["char"] for member in sure["family_members"]] == ["大"]
    # 仮 stays as uncertain as it was, between its Han forms alone.
    family = identity_fields({"unicode": "U+4EEE", "text_source": "仮"}, "codh-full")
    assert family["written_character"] is None and family["requires_family_scope"]
    assert [member["char"] for member in family["family_members"]] == [char for char in chars("U+4EEE") if char != KA]
    assert classification_of(ord("大")) is Classification.IDENTIFIED
    assert classification_of(ord("仮")) is Classification.UNASSESSED
    # A glyph transcribed as a seal character is written as it, under its Han character's grapheme.
    seal = identity_fields({"unicode": "U+3E6F7", "text_source": KA}, "codh-tensho")
    assert (seal["written_character"], seal["grapheme"], seal["requires_family_scope"]) == (KA, "U+4EEE", False)
    assert [member["char"] for member in seal["family_members"]] == chars("U+4EEE")


def test_the_corpus_counts_ask_for_the_family_scope_as_they_did(tmp_path):
    root, index = tmp_path / "work", tmp_path / "index"
    out = root / "codh-full"
    out.mkdir(parents=True)
    tables.write(out / "documents.parquet", [Document(
        id="codh:book", title="Book", production="printed/woodblock",
        image_rights={"licence": "CC-BY-4.0", "holder": "Library", "attribution": "Library"})], Document)
    tables.write(out / "pages.parquet", [Page(id="codh:book:page", document_id="codh:book", seq=1, width=1000, height=1000,
                                              image="https://example.org/iiif/page")], Page)
    tables.write(out / "units.parquet", [Unit(
        id=f"codh:unit:{i}", document_id="codh:book", page_id="codh:book:page", text_source=char, unicode=refs.to_code_point(char),
        kind="char", method="import", box=Box(x=10 + i * 20, y=10, w=16, h=20)) for i, char in enumerate("大仮")], Unit)
    build_chars(root, index)
    api = CorpusAPI(root, index)
    rows = {char: api._count_row(char, api.index.summary(char)) for char in ("大", DAI, "仮", "假", KA)}
    assert {char: row["requires_family_scope"] for char, row in rows.items()} == {
        "大": False, DAI: False, "仮": True, "假": True, KA: False}
    # The family's glyphs are the whole family's, on its seal character's row too.
    assert rows[KA]["n_family_glyphs"] == rows["仮"]["n_family_glyphs"] == 1


def test_a_clustering_names_no_seal_form():
    assert form_clusters.family_of("U+5927")["relation"] == "kanji-variants", "大 keeps its variants as its forms"
    assert DAI not in [member["char"] for member in form_clusters.family_of("U+5927")["members"]]
    family = form_clusters.family_of("U+5047")
    assert [member["char"] for member in family["members"]] == chars("U+4EEE")[:-1]
    assert (family["character_count"], family["label"]) == (len(family["members"]), " = ".join(chars("U+4EEE")[:-1]))
    assert form_clusters.family_of("U+3E6F7") is None and form_clusters.family_of("U+3EDB1") is None


def test_a_vote_and_a_sample_are_for_no_seal_form():
    family, members = suggestions._class_family("U+4EEE")
    assert family == "U+4EEE" and list(members) == chars("U+4EEE")[:-1]
    assert suggestions._class_family("U+5927") == ("U+5927", ("大",))
    assert "U+3E6F7" not in visual_samples.families("仮")["U+4EEE"]
    assert not any(refs.is_small_seal(kanji) for kanji in refs.kana_origins())


def test_a_gallery_opens_on_the_scope_it_did():
    scope = lambda point: characters._row(refs.character(point), {})["default_scope"]
    assert (scope("U+5927"), scope("U+4EEE"), scope("U+5047")) == ("character", "grapheme", "grapheme")
    # No crop of a seal character is among the normalised ones a family's gallery gathers.
    assert (scope("U+3E6F7"), scope("U+3EDB1")) == ("character", "character")


@pytest.fixture
def export(monkeypatch):
    monkeypatch.syspath_prepend(str(Path("scripts").resolve()))
    return importlib.import_module("export_graphemes")


def live(head: str, sources: dict[str, list[str]]) -> dict[str, dict]:
    return {point: {"data": {"candidates": {"known": True, "sources": sources.get(point, []), "glyphs": 4 if point in sources else 0}},
                    "detail": {}} for point in refs.graphemes()[head]}


def test_a_card_of_a_han_character_asks_for_the_family_scope_as_it_did(export):
    cards = live("U+5927", {"U+5927": ["codh"], "U+3EDB1": ["codh-tensho"]})
    data, detail = export.rewritten("U+5927", cards, dict.fromkeys(cards, 0))
    assert (data["candidates"]["requires_family_scope"], data["default_scope"]) == (False, "character")
    assert data["candidates"]["family_glyphs"] == 8
    assert [form["char"] for form in detail["characters"]] == ["大", DAI]
    assert [entry["key"] for entry in detail["expansions"]] == ["grapheme"]
    cards = live("U+4EEE", {"U+4EEE": ["codh"], "U+3E6F7": ["codh-tensho"]})
    for point, wanted in (("U+4EEE", True), ("U+5047", True), ("U+3E6F7", False)):
        data, _ = export.rewritten(point, cards, dict.fromkeys(cards, 0))
        assert data["candidates"]["requires_family_scope"] is wanted
        assert data["grapheme"]["code_point"] == "U+4EEE" and data["grapheme"]["members"][-1]["char"] == KA


def test_a_stored_grouping_of_the_han_members_is_kept(export, monkeypatch):
    members = [point for point in refs.graphemes()["U+4EEE"] if point != "U+3E6F7"]
    stored = {"status": "ready", "family": "U+4EEE", "members": members, "sample_count": 9, "assigned_count": 4,
              "unassigned_count": 5, "groups": [{"id": "a"}], "model_revision": "r1"}
    cards = live("U+4EEE", {"U+4EEE": ["codh"]})
    cards["U+4EEE"]["detail"] = {"visual_analysis": stored}
    _, detail = export.rewritten("U+4EEE", cards, dict.fromkeys(cards, 0))
    assert detail["visual_analysis"] == stored


def test_a_round_deals_the_seal_characters_of_its_grapheme_and_is_named_by_its_head():
    assert atlas.grapheme_of(CHUN) == "U+6625" and atlas.grapheme_members("U+6625")[-1] == CHUN
    # As for ハ, a key that names a member is answered with the grapheme it is filed under.
    for key, message in (("U+30CF", "ハ is filed under は."), ("U+3D307", f"{CHUN} is filed under 春.")):
        with pytest.raises(atlas.BadRequest, match=message):
            atlas.grapheme_members(key)


def test_no_seal_character_heads_a_grapheme():
    heads = Counter(str(refs.character(head).script) for head in refs.graphemes())
    assert heads["seal"] == 0 and heads["han"] > 60_000
