"""The shape-variant rule that groups kanji into grapheme families (glyph_atlas.shape_families)."""

from __future__ import annotations

import random

from glyph_atlas import refs
from glyph_atlas.shape_families import families

OYAJI = "{figure}: 法務省戸籍法関連通達・通知, 種別 戸籍統一文字情報 親字・正字, ホップ数 1"


def reduction(a: str, b: str, figure: str, *extra: str) -> dict[str, str]:
    return {"a": a, "b": b, "relation": "reduction", "source": "mj-shrink-map",
            "detail": " | ".join([OYAJI.format(figure=figure), *extra])}


def edge(a: str, b: str, relation: str, source: str = "wikidata", detail: str = "Q1") -> dict[str, str]:
    return {"a": a, "b": b, "relation": relation, "source": source, "detail": detail}


def grouped(variants, *, equivalents=(), figures=None, jis=(), curated=None, ideographs=None):
    """The families as {head: sorted members}."""
    chars = {char for row in [*variants, *equivalents] for char in (row["a"], row["b"])}
    found = families(variants, equivalents, ideographs or chars, figures or {}, set(jis), curated)
    return {family.head: "".join(sorted(family.members)) for family in found}


#: 還 and the figures the 戸籍統一文字 registers under it, as the MJ tables state them.
KAN = [
    reduction("𮟃", "還", "MJ058863"),
    reduction("𨕔", "還", "MJ050966"),
    edge("還", "𮟃", "variant"),
    reduction("还", "還", "MJ025810"),
    edge("還", "还", "simplified", "unihan", "還→还 kSimplifiedVariant"),
    reduction("𢕼", "還", "MJ035930"),
    edge("𢕼", "還", "specialized-semantic", "wikidata", "Q109816001"),
]
KAN_FIGURES = {"MJ058863": "𮟃", "MJ050966": "𨕔", "MJ025810": "还", "MJ035930": "𢕼"}


def test_a_reduction_to_the_seiji_joins_the_two_characters():
    assert grouped(KAN, figures=KAN_FIGURES, jis="還")["還"] == "還𨕔𮟃"


def test_a_simplified_or_specialized_semantic_pair_stays_apart_despite_its_reduction():
    found = grouped(KAN, figures=KAN_FIGURES, jis="還")
    assert "还" not in found["還"] and "𢕼" not in found["還"]
    assert "还" not in found and "𢕼" not in found, "neither has a family of its own"


def test_a_pair_kept_apart_blocks_a_chain_as_well():
    """X reaches 還 through Y, and X is 還's simplification: X stays out."""
    rows = [reduction("甲", "乙", "MJ1"), reduction("乙", "還", "MJ2"),
            edge("還", "甲", "simplified", "opencc", "TSCharacters")]
    assert grouped(rows, figures={"MJ1": "甲", "MJ2": "乙"}) == {"還": "乙還"}


def test_a_loan_or_a_non_cognate_homograph_keeps_a_pair_apart():
    for relation in ("borrowed", "non-cognate", "substitute", "spoofing"):
        rows = [reduction("甲", "乙", "MJ1"), edge("甲", "乙", relation, "cjkvi-variants", "tag")]
        assert grouped(rows, figures={"MJ1": "甲"}) == {}, relation


def test_only_the_code_points_default_figure_speaks_for_it():
    """師's non-default figures reduce to 帥; they say what those drawings are, not what 師 is."""
    rows = [{"a": "師", "b": "帥", "relation": "reduction", "source": "mj-shrink-map",
             "detail": " | ".join(OYAJI.format(figure=f) for f in ("MJ057420", "MJ057421"))}]
    assert grouped(rows, figures={"MJ010848": "師"}) == {}


def test_a_figure_registered_under_two_seiji_joins_neither():
    rows = [reduction("𠆤", "丁", "MJ030534"), reduction("𠆤", "介", "MJ030534")]
    assert grouped(rows, figures={"MJ030534": "𠆤"}) == {}


def test_a_hop_two_or_a_fallback_reduction_joins_nothing():
    rows = [{"a": "㐄", "b": "井", "relation": "reduction", "source": "mj-shrink-map",
             "detail": "MJ000007: 法務省告示582号別表第四, 表 二, 順位 第2順位 | "
                       "MJ000007: 法務省戸籍法関連通達・通知, 種別 戸籍統一文字情報 親字・正字, ホップ数 2"}]
    assert grouped(rows, figures={"MJ000007": "㐄"}) == {}


def test_two_jis_characters_need_a_second_source():
    rows = [reduction("傅", "伝", "MJ1"), reduction("嶋", "島", "MJ2"), edge("島", "嶋", "variant")]
    assert grouped(rows, figures={"MJ1": "傅", "MJ2": "嶋"}, jis="傅伝嶋島") == {"島": "島嶋"}


def test_the_koseki_table_does_not_corroborate_itself():
    rows = [reduction("唔", "吾", "MJ1"), edge("吾", "唔", "variant", "cjkvi-variants", "吾→唔 koseki/variant")]
    assert grouped(rows, figures={"MJ1": "唔"}, jis="吾唔") == {}
    rows[1] = edge("吾", "唔", "variant", "cjkvi-variants", "吾→唔 koseki/variant | 吾→唔 hydzd/variant")
    assert grouped(rows, figures={"MJ1": "唔"}, jis="吾唔") == {"吾": "吾唔"}


def test_a_character_unicode_unifies_elsewhere_does_not_bring_its_twin():
    """苿 is 茉 encoded twice and registered under 味: 茉 and 味 stay apart."""
    rows = [edge("苿", "茉", "z", "unihan", "苿→茉 kZVariant"), reduction("苿", "味", "MJ021731")]
    assert grouped(rows, figures={"MJ021731": "苿"}, jis="茉味") == {"茉": "苿茉"}


def test_a_family_never_holds_two_seiji():
    rows = [reduction("甲", "乙", "MJ1"), reduction("丙", "丁", "MJ2"), edge("甲", "丙", "z", "unihan", "z")]
    assert grouped(rows, figures={"MJ1": "甲", "MJ2": "丙"}) == {"丁": "丁丙甲"}, "甲 would bring 乙 to 丁"


def test_compatibility_z_and_jis_unification_join():
    rows = [edge("﨑", "崎", "compatibility", "unicode-ucd", "5D0E"), edge("㒨", "𠑗", "z", "unihan", "z")]
    found = grouped(rows, equivalents=[{"a": "剝", "b": "剥", "kind": "itaiji", "source": "mj-main-table"}],
                    jis="剝")
    assert found == {"崎": "崎﨑", "㒨": "㒨𠑗", "剝": "剝剥"}


def test_the_head_is_the_seiji_then_jis_then_unified_then_lowest_code_point():
    assert grouped([reduction("𮟃", "還", "MJ1")], figures={"MJ1": "𮟃"}) == {"還": "還𮟃"}
    assert grouped([edge("﨑", "崎", "compatibility", "unicode-ucd", "5D0E")]) == {"崎": "崎﨑"}
    assert grouped([edge("㒨", "𠑗", "z", "unihan", "z")]) == {"㒨": "㒨𠑗"}
    assert grouped([edge("㒨", "𠑗", "z", "unihan", "z")], jis="𠑗") == {"𠑗": "㒨𠑗"}


def test_a_curated_family_keeps_its_head_and_is_never_joined_to_another():
    rows = [reduction("囶", "國", "MJ1"), reduction("學", "学", "MJ2"), edge("國", "學", "z", "unihan", "z")]
    found = grouped(rows, figures={"MJ1": "囶", "MJ2": "學"},
                    curated={"国": ["国", "國"], "学": ["学", "學"]})
    assert found == {"国": "囶国國", "学": "学學"}


def test_a_refused_merge_is_collected_for_the_build_summary():
    rows = [reduction("囶", "國", "MJ1"), reduction("學", "学", "MJ2"), edge("國", "學", "z", "unihan", "z")]
    refused: list[tuple[str, object]] = []
    families(rows, [], {c for row in rows for c in (row["a"], row["b"])},
             {"MJ1": "囶", "MJ2": "學"}, set(),
             curated={"国": ["国", "國"], "学": ["学", "學"]}, refused=refused)
    assert [(reason, edge.a, edge.b) for reason, edge in refused] == [("two curated families", "國", "學")]


def test_a_repeated_reduction_row_adds_its_claim_rather_than_replacing_it():
    """The same figure stated twice, or two figures of one character, both reach the detail."""
    rows = [reduction("𠆡", "己", "MJ1"), reduction("𠆡", "己", "MJ1"), reduction("𠆡", "己", "MJ2")]
    found = families(rows, [], {"𠆡", "己"}, {"MJ1": "𠆡", "MJ2": "𠆡"}, set())
    (family,) = found
    merge, = [e for e in family.edges if e.relation == "reduction"]
    assert merge.detail == " | ".join([OYAJI.format(figure="MJ1"), OYAJI.format(figure="MJ2")])


def test_the_rule_gives_the_same_families_in_any_input_order():
    rows = KAN + [reduction("嶋", "島", "MJ2"), edge("島", "嶋", "variant"), edge("島", "嶋", "equivalent", "yitizi", "y"),
                  reduction("嶌", "島", "MJ3"), edge("島", "嶌", "variant")]
    figures = {**KAN_FIGURES, "MJ2": "嶋", "MJ3": "嶌"}

    def run(order):
        found = families(order, [], {c for r in order for c in (r["a"], r["b"])}, figures, set("還島嶋嶌"))
        return [(f.head, sorted(f.members), f.edges) for f in found]

    expected = run(rows)
    for seed in range(5):
        shuffled = rows[:]
        random.Random(seed).shuffle(shuffled)
        assert run(shuffled) == expected


def test_the_site_puts_kan_and_its_mj_figure_in_one_grapheme():
    assert refs.grapheme("U+2E7C3") == refs.grapheme("U+9084") == "U+9084"
    info = refs.grapheme_info("𮟃")
    assert info["relation"] == "shape-variant" and info["members"][0]["char"] == "還"
    cited = {entry["id"]: entry for entry in info["evidence"]}
    assert cited["mj-shrink-map"]["title"] == "MJ縮退マップ"
    assert {"a": "𮟃", "b": "還", "relation": "reduction", "role": "merge",
            "detail": OYAJI.format(figure="MJ058863")} in cited["mj-shrink-map"]["edges"]


def test_the_site_keeps_kan_apart_from_its_simplification_and_its_partial_variant():
    assert refs.grapheme("还") == "U+8FD8" and refs.grapheme("𢕼") == "U+2257C"
    assert refs.grapheme_info("还")["relation"] == refs.grapheme_info("𢕼")["relation"] == "self"


def test_every_source_a_family_cites_has_a_source_record():
    for head, family in refs._grapheme_families().items():
        for entry in family["evidence"]:
            assert entry["title"] and str(entry["url"]).startswith("https://"), (head, entry["id"])
