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
    found = families(variants, equivalents, ideographs or chars, figures or {}, set(jis), curated,
                     familiarity={char: 2 for char in jis})
    return {family.head: "".join(sorted(family.members)) for family in found}


#: 還 and the figures the 戸籍統一文字 registers under it, as the MJ tables state them.
KAN = [
    reduction("𮟃", "還", "MJ058863"),
    reduction("𨕔", "還", "MJ050966"),
    edge("還", "𮟃", "variant"),
    reduction("还", "還", "MJ025810"),
    edge("還", "还", "simplified", "unihan", "還→还 kSimplifiedVariant"),
    edge("還", "还", "simplified", "opencc", "STCharacters (also itself) | TSCharacters"),
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
    """X reaches 還 through Y, and X is a loan for 還: X stays out."""
    rows = [reduction("甲", "乙", "MJ1"), reduction("乙", "還", "MJ2"),
            edge("還", "甲", "borrowed", "cjkvi-variants", "還→甲 hydcd/borrowed")]
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
    """The reduction is refused; the 異寫字 pass then joins 唔, tied to 吾 alone, on the 戸籍統一文字's row."""
    rows = [reduction("唔", "吾", "MJ1"), edge("吾", "唔", "variant", "cjkvi-variants", "吾→唔 koseki/variant")]
    refused: list[tuple[str, object]] = []
    families(rows, [], {"吾", "唔"}, {"MJ1": "唔"}, {"吾", "唔"}, refused=refused)
    assert [(reason, edge.a, edge.b) for reason, edge in refused] == [
        ("no second source for a JIS X 0208 pair", "唔", "吾")]
    rows[1] = edge("吾", "唔", "variant", "cjkvi-variants", "吾→唔 koseki/variant | 吾→唔 hydzd/variant")
    refused.clear()
    families(rows, [], {"吾", "唔"}, {"MJ1": "唔"}, {"吾", "唔"}, refused=refused)
    assert refused == []


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


def test_a_simplified_form_of_one_character_joins_its_grapheme():
    """时 is 時 written another way and nothing else: the 異寫字 pass joins it."""
    rows = [reduction("旹", "時", "MJ1"), edge("時", "时", "simplified", "unihan", "時→时 kSimplifiedVariant"),
            edge("时", "時", "overlap", "yitizi", "ytenx/JihThex.csv 时旹時")]
    assert grouped(rows, figures={"MJ1": "旹"}) == {"時": "时旹時"}


def test_a_variant_of_two_characters_joins_neither():
    """寸 is listed as a variant of 時 and of 算, each of which has variants of its own."""
    rows = [edge("寸", "時", "variant", "wikidata"), edge("寸", "算", "variant", "wikidata"),
            edge("時", "𭥱", "variant", "wikidata"), edge("算", "祘", "variant", "wikidata")]
    assert grouped(rows) == {"時": "時𭥱", "祘": "祘算"}


def test_a_full_form_does_not_join_a_reduced_form_that_writes_another_character():
    """闲 is the simplified 閑 and the simplified 閒; 閑, tied to 闲 alone, stays out."""
    rows = [edge("閑", "闲", "simplified", "opencc", "TSCharacters"),
            edge("閒", "闲", "simplified", "opencc", "TSCharacters")]
    assert grouped(rows) == {}
    assert grouped(rows[:1]) == {"閑": "閑闲"}, "a reduced form of one character joins it"


def test_a_related_only_table_or_a_partial_relation_joins_nothing():
    for row in (edge("寸", "時", "variant", "cjkvi-variants", "寸→時 cjkvi/variant"),
                edge("時", "㫑", "overlap", "yitizi", "yitizi.txt 㫑時"),
                {"a": "䝰", "b": "時", "relation": "reduction", "source": "mj-shrink-map",
                 "detail": "MJ004730: 法務省告示582号別表第四, 表 二, 順位 第1順位"}):
        assert grouped([row]) == {}, row["relation"]
    assert grouped([edge("寸", "時", "variant", "cjkvi-variants", "寸→時 cjkvi/variant | 寸→時 hydzd/variant")]) \
        == {"寸": "寸時"}, "a 異体字 table's claim beside it does join"


def test_a_variant_in_some_senses_or_a_loan_keeps_a_pair_apart():
    for relation in ("specialized-semantic", "borrowed"):
        rows = [edge("㫑", "時", "variant", "wikidata"), edge("㫑", "時", relation, "wikidata")]
        assert grouped(rows) == {}, relation


def test_a_simplified_form_that_writes_a_character_of_its_own_stays_apart():
    rows = [edge("後", "后", "simplified", "opencc", "STCharacters (also itself) | TSCharacters"),
            edge("後", "后", "simplified", "unihan", "後→后 kSimplifiedVariant")]
    assert grouped(rows) == {}
    assert grouped([{**rows[0], "detail": "STCharacters | TSCharacters"}, rows[1]]) == {"後": "后後"}


def test_the_pass_joins_once_so_a_chain_does_not_carry_on():
    """甲 and 丁 are each tied to one character only and join it; 乙 and 丙 stay two families."""
    rows = [edge("甲", "乙", "variant"), edge("乙", "丙", "variant"), edge("丙", "丁", "variant")]
    assert grouped(rows) == {"乙": "乙甲", "丁": "丁丙"}


def test_two_characters_of_one_standard_join_on_its_own_tradition_only():
    rows = [edge("菓", "果", "variant", "cjkvi-variants", "果→菓 dypytz/variant | 果→菓 hydzd/variant"),
            edge("菓", "果", "simplified", "cjkvi-variants", "菓→果 cjkvi/simplified")]
    assert grouped(rows, jis="菓果") == {}
    assert list(grouped(rows).values()) == ["果菓"], "outside the standards the claims are enough"
    rows = [edge("躰", "體", "variant", "cjkvi-variants", "體→躰 hydzd/variant | 體→躰 koseki/variant")]
    assert grouped(rows, jis="躰體") == {"躰": "躰體"}
    rows = [edge("龍", "竜", "shinjitai", "unihan", "竜→龍 kJapaneseOldVariant")]
    assert grouped(rows, jis="竜龍") == {"竜": "竜龍"}
    rows = [edge("餘", "馀", "simplified", "cjkvi-variants", "餘→馀 cjkvi/simplified")]
    assert grouped(rows, ideographs={"餘", "馀"}) == {"餘": "餘馀"}
    found = families(rows, [], {"餘", "馀", "余"}, {}, set(), gb2312={"馀", "余"},
                     curated={"余": ["余", "餘"]})
    assert {family.head: "".join(sorted(family.members)) for family in found} == {"余": "余餘"}


def test_the_head_is_the_character_a_japanese_reader_knows_best():
    rows = [edge("羣", "群", "variant", "wikidata")]
    found = families(rows, [], {"羣", "群"}, {}, set(), familiarity={"群": 0, "羣": 3})
    assert [family.head for family in found] == ["群"]


def test_the_yixiezi_pass_never_joins_two_curated_families():
    rows = [edge("国", "学", "variant")]
    assert grouped(rows, curated={"国": ["国", "國"], "学": ["学", "學"]}) == {"国": "国國", "学": "学學"}


def test_groups_joining_one_family_are_checked_against_each_other():
    """踩 and 踹 are each tied to 跴 alone; both are GB 2312, so only the first joins."""
    rows = [edge("踩", "跴", "variant"), edge("踹", "跴", "semantic", "unihan", "跴→踹 kSemanticVariant")]
    found = families(rows, [], {"踩", "踹", "跴"}, {}, set(), gb2312={"踩", "踹"})
    assert {"".join(sorted(family.members)) for family in found} == {"跴踩"}
    rows = [edge("卥", "西", "variant"), edge("𠧧", "西", "variant"),
            edge("卥", "𠧧", "non-cognate", "cjkvi-variants", "卥→𠧧 cjkvi/non-cognate")]
    assert grouped(rows) == {"卥": "卥西"}


def test_a_simplified_form_that_writes_itself_stays_apart_whatever_else_relates_it():
    rows = [edge("衚", "胡", "simplified", "opencc", "STCharacters (also itself) | TSCharacters"),
            edge("衚", "胡", "variant", "cjkvi-variants", "胡→衚 hydzd/variant")]
    assert grouped(rows) == {}


def test_two_curated_families_never_meet_through_a_family_both_join():
    rows = [edge("乙", "丙", "variant"), edge("戊", "丙", "variant")]
    found = grouped(rows, curated={"甲": ["甲", "乙"], "丁": ["丁", "戊"]})
    assert found == {"丁": "丁丙戊", "甲": "乙甲"}


def test_with_no_ranked_member_the_head_is_of_the_main_block():
    """氯 is GB 2312; with no rank at all, 喎 of the main block heads over 㖞 of Extension A."""
    rows = [edge("㲶", "氯", "z", "unihan", "z")]
    assert [f.head for f in families(rows, [], {"㲶", "氯"}, {}, set(), familiarity={"氯": 6})] == ["氯"]
    rows = [edge("㖞", "喎", "z", "unihan", "z")]
    assert [f.head for f in families(rows, [], {"㖞", "喎"}, {}, set())] == ["喎"]


def test_a_traditional_form_or_the_seiji_heads_over_a_simplification():
    """墊 (CNS 11643 plane 1) heads over 垫 (GB 2312); 厲, the 正字 a reduction points at, over 厉."""
    rows = [edge("墊", "垫", "simplified", "unihan", "墊→垫 kSimplifiedVariant")]
    assert [f.head for f in families(rows, [], {"墊", "垫"}, {}, set(), familiarity={"墊": 4, "垫": 6})] == ["墊"]
    rows = [reduction("厉", "厲", "MJ1")]
    found = families(rows, [], {"厉", "厲"}, {"MJ1": "厉"}, set(), familiarity={"厉": 6})
    assert [f.head for f in found] == ["厲"]
    rows = [reduction("篙", "㰏", "MJ2")]
    found = families(rows, [], {"篙", "㰏"}, {"MJ2": "篙"}, set(), familiarity={"篙": 6})
    assert [f.head for f in found] == ["篙"], "a 正字 of an extension does not head a character a reader knows"
    rows = [reduction("玨", "珏", "MJ3")]
    found = families(rows, [], {"玨", "珏"}, {"MJ3": "玨"}, set(), familiarity={"玨": 4})
    assert [f.head for f in found] == ["玨"], "a traditional standard's member heads over a 正字 it lacks"
    rows = [edge("蕤", "甤", "compatibility", "unicode-ucd", "751D")]
    found = families(rows, [], {"蕤", "甤"}, {}, set(), familiarity={"蕤": 6})
    assert [f.head for f in found] == ["蕤"], "a decomposition target is no 正字 over a known member"
    rows = [edge("璉", "琏", "simplified", "unihan", "璉→琏 kSimplifiedVariant"),
            edge("\uf994", "璉", "compatibility", "unicode-ucd", "7489")]
    found = families(rows, [], {"璉", "琏", "\uf994"}, {}, set(), familiarity={"琏": 6})
    assert [f.head for f in found] == ["璉"], "a simplified form never heads its traditional form"
    rows = [reduction("泜", "坻", "MJ4")]
    found = families(rows, [], {"泜", "坻"}, {"MJ4": "泜"}, set(), familiarity={"泜": 4, "坻": 6})
    assert [f.head for f in found] == ["坻"], "a 正字 a Chinese standard holds heads over a rarer form"
    rows = [reduction("妤", "伃", "MJ5")]
    found = families(rows, [], {"妤", "伃"}, {"MJ5": "妤"}, set(), familiarity={"妤": 4})
    assert [f.head for f in found] == ["妤"], "an unranked 正字 yields to a standard's member"
    rows = [edge("戇", "戆", "simplified", "unihan", "戇→戆 kSimplifiedVariant"), edge("戅", "戇", "z", "unihan", "z")]
    found = families(rows, [], {"戇", "戆", "戅"}, {}, set())
    assert [f.head for f in found] == ["戇"], "with nothing ranked, the form a simplification simplifies"
    rows = [edge("垜", "垛", "simplified", "cjkvi-variants", "垜→垛 cjkvi/simplified")]
    found = families(rows, [], {"垜", "垛"}, {}, set(), familiarity={"垛": 5})
    assert [f.head for f in found] == ["垛"], "a CNS 11643 plane 2 character is no mere simplification"
    rows = [reduction("靚", "靘", "MJ6"), edge("靚", "靓", "simplified", "unihan", "靚→靓 kSimplifiedVariant")]
    found = families(rows, [], {"靚", "靘", "靓"}, {"MJ6": "靚"}, set(), familiarity={"靚": 4, "靘": 7, "靓": 6})
    assert [f.head for f in found] == ["靚"], "a 正字 of CNS 11643 plane 2 does not head over plane 1"
    rows = [edge("𥄨", "瞅", "simplified", "cjkvi-variants", "𥄨→瞅 cjkvi/simplified")]
    found = families(rows, [], {"𥄨", "瞅"}, {}, set(), familiarity={"瞅": 5})
    assert [f.head for f in found] == ["瞅"], "GB 2312 and CNS 11643 plane 2 both: Taiwan writes it too"

