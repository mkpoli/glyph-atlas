"""Component substitutions: what a pair of variant characters attests, and what that predicts."""

import csv
from pathlib import Path

from glyph_atlas import han_component_variants as v

TABLE = Path("data/vocab/han-component-variants.tsv")


def descriptions(sequences: dict[str, list[str]], unified: dict[str, str] | None = None) -> v.Descriptions:
    return v.Descriptions(sequences, unified or {})


def sources(*pairs: tuple[str, str]) -> list[tuple[str, str, tuple[str, ...]]]:
    return [(a, b, ("source",)) for a, b in pairs]


def test_a_sequence_is_a_tree_and_a_tree_is_a_sequence_again():
    assert v.parse("⿰弓𧈧") == ("⿰", ("弓", "𧈧"))
    assert v.text(v.parse("⿱宀⿰月口")) == "⿱宀⿰月口"
    # A part spelled out twice at the top reads as the one ternary operator.
    desc = descriptions({"甲": ["⿱X⿱YZ"], "乙": ["⿳XYZ"]})
    assert desc.trees["甲"] == desc.trees["乙"] == [("⿳", ("X", "Y", "Z"))]


def test_a_pair_differing_in_one_part_attests_that_substitution():
    desc = descriptions({"㗊": ["⿰口堯"], "哷": ["⿰厶堯"]})
    found = v.substitutions(desc, "㗊", "哷")
    assert set(found) == {v.ordered("口", "厶")}
    assert found[v.ordered("口", "厶")] == {None}


def test_a_pair_differing_at_the_top_or_in_two_parts_attests_nothing():
    assert v.substitutions(descriptions({"㐅": ["⿰口堯"], "丆": ["⿱口堯"]}), "㐅", "丆") == {}
    assert v.substitutions(descriptions({"㐆": ["⿰口堯"], "㐇": ["⿰厶夂"]}), "㐆", "㐇") == {}


def test_a_single_stroke_is_no_component():
    assert v.substitutions(descriptions({"㐈": ["⿰口一"], "㐉": ["⿰口丶"]}), "㐉", "㐈") == {}
    # 𠃌 is one stroke by Unihan's kTotalStrokes, though outside the CJK Strokes block.
    assert v.substitutions(descriptions({"万": ["⿸丆𠃌"], "㐂": ["⿸丆口"]}), "万", "㐂") == {}


def test_the_difference_inside_a_component_is_recorded_with_the_substitution_it_is_inside():
    # 強 is ⿰弓𧈧 and 强 is ⿰弓虽; 𧈧 and 虽 differ in 厶 against 口, one level down.
    desc = descriptions({"强": ["⿰弓虽"], "強": ["⿰弓𧈧"], "𧈧": ["⿱厶虫"], "虽": ["⿱口虫"]})
    found = v.substitutions(desc, "強", "强")
    assert found[v.ordered("𧈧", "虽")] == {None}
    assert found[v.ordered("口", "厶")] == {v.ordered("𧈧", "虽")}


def test_two_replacements_of_one_character_attest_the_difference_of_each():
    desc = descriptions({"㐜": ["⿰口堯", "⿰日夂"], "㐝": ["⿰厶堯", "⿰月夂"]})
    assert set(v.substitutions(desc, "㐜", "㐝")) == {v.ordered("口", "厶"), v.ordered("日", "月")}


def test_one_pair_is_not_a_pattern_and_does_not_pass_the_threshold():
    found = v.attest(descriptions({"㤜": ["⿰口堯"], "㤚": ["⿰厶堯"]}), sources(("㤜", "㤚")))
    assert len(found[v.ordered("口", "厶")].pairs) == 1
    assert v.kept(found) == []


def test_two_pairs_in_two_positions_pass_the_threshold():
    desc = descriptions({"㤛": ["⿰口堯"], "低": ["⿰厶堯"], "㤝": ["⿰口夂"], "㤞": ["⿰厶夂"]})
    found = v.attest(desc, sources(("㤛", "低"), ("㤝", "㤞")))
    assert [item.count for item in v.kept(found)] == [2]


def test_two_pairs_reaching_one_position_do_not_pass_the_threshold():
    # Both pairs differ at the top in the same one place, so the inner substitution is one claim.
    desc = descriptions({"㤠": ["⿰⿱口虫夂"], "㤡": ["⿰⿱厶虫夂"],
                         "㤢": ["⿰⿱口虫日"], "㤣": ["⿰⿱厶虫日"]})
    found = v.attest(desc, sources(("㤠", "㤡"), ("㤢", "㤣")))
    inner = found[v.ordered("口", "厶")]
    assert len(inner.pairs) == 2 and inner.count == 1
    assert v.ordered("口", "厶") not in {(item.a, item.b) for item in v.kept(found)}


def test_one_pair_seen_in_two_positions_does_not_pass_the_threshold():
    # One replacement of the pair differs at the top, the other only inside: still one pair.
    desc = descriptions({"㤤": ["⿱口夂", "⿰木⿱日口"], "㤥": ["⿱厶夂", "⿰木⿱日厶"]})
    found = v.attest(desc, sources(("㤤", "㤥")))
    item = found[v.ordered("口", "厶")]
    assert len(item.pairs) == 1 and item.count == 2
    assert v.kept(found) == []


def test_a_substitution_makes_one_form_and_never_stacks_with_another():
    desc = descriptions({"㒺": ["⿲口日夂"]})
    table = v.equivalents([
        v.Attested(*v.ordered("口", "厶"), ((("㒺", "㒺", ("s",)),)), ("㒺/㒺",)),
        v.Attested(*v.ordered("日", "月"), ((("㒺", "㒺", ("s",)),)), ("㒺/㒺",)),
    ])
    forms = {(item.was, item.became) for item in v.derive(desc, table)}
    assert forms == {("口", "厶"), ("日", "月")}
    assert len(list(v.derive(desc, table))) == 2, "two substitutions never stack in one form"


def test_a_form_no_character_has_comes_out_as_its_sequence():
    desc = descriptions({"杏": ["⿱宀口"]})
    table = v.equivalents([v.Attested(*v.ordered("口", "厶"), (("杏", "杏", ("s",)),), ("x/y",))])
    derived = list(v.derive(desc, table))
    assert [(item.other, item.encoded, item.was, item.became) for item in derived] == [
        ("⿱宀厶", False, "口", "厶")]


def test_a_form_a_character_has_comes_out_as_that_character():
    desc = descriptions({"杏": ["⿱宀口"], "杲": ["⿱宀厶"]})
    table = v.equivalents([v.Attested(*v.ordered("口", "厶"), (("杏", "杲", ("s",)),), ("x/y",))])
    derived = list(v.derive(desc, table))
    assert {(item.other, item.encoded) for item in derived} == {("杲", True), ("杏", True)}


def test_no_form_holding_an_unrepresentable_part_is_written():
    # BabelStone's numbered component ({5}) can be a part, but nothing can display it.
    desc = descriptions({"㐠": ["⿰口{5}"]})
    table = v.equivalents([v.Attested(*v.ordered("口", "厶"), (("㐠", "㐠", ("s",)),), ("x/y",))])
    assert list(v.derive(desc, table)) == []


def test_a_component_is_named_by_the_character_it_spells():
    # ⿱一口 is 𠮛, so a part spelled out and a part named read as one shape.
    desc = descriptions({"𠍲": ["⿰木⿱一口"], "彬": ["⿰木𠮛"], "𠮛": ["⿱一口"]})
    assert v.substitutions(desc, "𠍲", "彬") == {}


def test_a_radical_form_is_read_through_the_unification():
    desc = descriptions({"汨": ["⿰氵日"], "㭞": ["⿰⺡日"]}, {"⺡": "氵"})
    assert v.substitutions(desc, "汨", "㭞") == {}


def rows() -> list[dict[str, str]]:
    with TABLE.open(encoding="utf-8") as handle:
        return list(csv.DictReader((line for line in handle if not line.startswith("#")), delimiter="\t"))


def test_a_substitution_whose_predictions_the_graph_mostly_does_not_state_is_not_kept():
    # 口 and 厶 swap in four pairs of characters; one attests it, and the other three are measured.
    desc = descriptions({"㗀": ["⿰口夂"], "㗁": ["⿰厶夂"], "㗂": ["⿰口日"], "㗃": ["⿰厶日"],
                         "㗄": ["⿰口月"], "㗅": ["⿰厶月"], "㗆": ["⿰口木"], "㗇": ["⿰厶木"]})
    item = v.Attested(*v.ordered("口", "厶"), (("㗀", "㗁", ("s",)),), ("x/y",), (("x/y",),))
    predicted = v.predictions(desc, [item])
    assert predicted[(item.a, item.b)] == {("㗀", "㗁"), ("㗂", "㗃"), ("㗄", "㗅"), ("㗆", "㗇")}
    assert v.agreeing([item], predicted, {("㗀", "㗁")}) == []
    kept = v.agreeing([item], predicted, {("㗀", "㗁"), ("㗂", "㗃")})
    assert [(k.predicted, k.agreed) for k in kept] == [(3, 1)]


def test_the_committed_table_holds_only_what_the_threshold_and_the_agreement_keep():
    found = rows()
    assert len(found) > 1500
    assert found == sorted(found, key=lambda row: (row["a"], row["b"]))
    for row in found:
        assert int(row["count"]) >= v.THRESHOLD
        assert int(row["agreed"]) >= v.AGREEMENT * int(row["predicted"]) > 0
        pairs = row["pairs"].split(" ")
        assert len(pairs) >= v.THRESHOLD
        for pair in pairs:
            shape, _, sources_of_pair = pair.partition("=")
            letters, _, _ = shape.partition(":")
            assert letters and shape != pair
            assert sources_of_pair


IDS_睘 = {"還": ["⿺辶睘(GHTJKPV)"], "𮟃": ["⿺辶𦊷(J)"], "環": ["⿰𤣩睘(GHTJKPV)"],
          "𤨔": ["⿰𤣩𦊷(GTP)"], "寰": ["⿱宀睘(GHTJKP)"], "睘": ["⿳罒𠮛𧘇(GHTJKP)"],
          "𦊷": ["⿳罒一⿱𠂈{25}(G)"]}


def test_睘_𦊷_writes_還_as_𮟃_and_寰_as_a_form_no_character_has():
    desc = descriptions(IDS_睘)
    found = v.attest(desc, sources(("還", "𮟃"), ("環", "𤨔")))
    assert {(p, q) for p, q, _ in found[v.ordered("睘", "𦊷")].pairs} == {("環", "𤨔"), ("還", "𮟃")}
    derived = {(d.char, d.other, d.encoded) for d in v.derive(desc, {"睘": {"𦊷"}, "𦊷": {"睘"}})}
    assert {("還", "𮟃", True), ("𮟃", "還", True), ("寰", "⿱宀𦊷", False)} <= derived


def test_睘_𦊷_is_not_kept_on_its_two_pairs_alone():
    # Two pairs attest it and its one other prediction, 寰 with ⿱宀𦊷, is no pair of characters: with
    # each attesting pair held out the other no longer passes the threshold, so nothing is measured.
    assert v.ordered("睘", "𦊷") not in {(row["a"], row["b"]) for row in rows()}
    desc = descriptions(IDS_睘)
    found = v.attest(desc, sources(("還", "𮟃"), ("環", "𤨔")))
    item = found[v.ordered("睘", "𦊷")]
    assert v.kept(found) == [item] and not item.held_out(0) and not item.held_out(1)
    assert v.agreeing([item], v.predictions(desc, [item]), {("還", "𮟃"), ("環", "𤨔")}) == []


def test_口_厶_is_kept_and_a_swap_of_meaning_is_not():
    by_key = {(row["a"], row["b"]) for row in rows()}
    assert v.ordered("口", "厶") in by_key and v.ordered("鳥", "鸟") in by_key
    assert v.ordered("口", "氵") not in by_key and v.ordered("扌", "木") not in by_key


def test_色_𮎜_keeps_the_token_its_sequence_writes():
    # 色 is ⿱⺈巴 and 𮎜 ⿱𠂉巴; ⺈ reads as 刀 only while two trees are compared, so the
    # substitution is ⺈ against 𠂉, and a 刀 written as 刀 is never rewritten by it.
    desc = descriptions({"色": ["⿱⺈巴(GHTJKPV)"], "𮎜": ["⿱𠂉巴(J)"], "分": ["⿱八刀(GHTJKPV)"],
                         "㓀": ["⿱刀巴"]}, {"⺈": "刀"})
    assert v.substitutions(desc, "色", "𮎜") == {v.ordered("⺈", "𠂉"): {None}}
    assert v.substitutions(desc, "色", "㓀") == {}
    table = v.equivalents([v.Attested(*v.ordered("⺈", "𠂉"), (), ())])
    assert {(d.char, d.other) for d in v.derive(desc, table, ["色", "分", "㓀"])} == {("色", "𮎜")}
    by_key = {(row["a"], row["b"]): row for row in rows()}
    assert "色:𮎜" in by_key[v.ordered("⺈", "𠂉")]["pairs"]
    assert v.ordered("刀", "𠂉") not in by_key


def test_only_trees_of_a_common_region_are_compared():
    # 免's J tree is ⿱{2}儿 and 𭀠 is J only: set against it, two parts differ, so the pair
    # attests nothing; 免's G tree, ⿱⺈…, describes a glyph 𭀠 is not drawn beside.
    sequences = {"免": ["⿱⺈⿸⿻口丿乚(GHTKP[B])", "⿱{2}儿(JV)"], "𭀠": ["⿱𠂉⿸⿻口丿乚(J)"]}
    assert v.substitutions(descriptions(sequences, {"⺈": "刀"}), "免", "𭀠") == {}
    # With no region in common, every tree is compared.
    elsewhere = {**sequences, "𭀠": ["⿱𠂉⿸⿻口丿乚(K)"], "免": ["⿱⺈⿸⿻口丿乚(G)", "⿱{2}儿(J)"]}
    assert set(v.substitutions(descriptions(elsewhere), "免", "𭀠")) == {v.ordered("⺈", "𠂉")}


def test_余_除_adds_a_component_where_口_厶_swaps_one():
    desc = descriptions({"除": ["⿰阝余"], "余": ["⿱𠆢⿱一朩"], "政": ["⿰正攵"], "正": ["⿱一止"]})
    assert v.adds(desc, *v.ordered("余", "除")) and v.adds(desc, *v.ordered("正", "政"))
    assert not v.adds(desc, *v.ordered("口", "厶"))
    # The rule scores both kinds alike; 余 against 除 is kept on its three pairs.
    row = {(row["a"], row["b"]): row for row in rows()}[v.ordered("余", "除")]
    assert {token.split("=")[0] for token in row["pairs"].split(" ")} == {"㾻:𤶠", "涂:滁", "蜍:𮔲"}


def test_a_description_that_names_a_component_matches_one_that_spells_it_out_across_the_operator():
    # 鸂 is ⿰溪鳥 with 溪 ⿰氵奚; 㶉 spells 溪 out across the operator as ⿲氵奚鸟, and differs in
    # 鳥 against 鸟.
    desc = descriptions({"鸂": ["⿰溪鳥"], "溪": ["⿰氵奚"], "奚": ["⿱爫𡗞"], "㶉": ["⿲氵奚鸟"]})
    assert v.substitutions(desc, "鸂", "㶉") == {("鳥", "鸟"): {None}}
    # The form comes out as the character, and the spelled-out reading writes no second sequence.
    assert {(d.other, d.encoded) for d in v.derive(desc, {"鳥": {"鸟"}}, ["鸂"])} == {("㶉", True)}
    by_key = {(row["a"], row["b"]): row for row in rows()}
    assert "㶉:鸂" in by_key[v.ordered("鳥", "鸟")]["pairs"]
    # A spelling under an operator it does not flatten into keeps the boundary: ⿱溪鳥 is no ⿰溪鳥.
    desc2 = descriptions({"鸂": ["⿰溪鳥"], "溪": ["⿰氵奚"], "別": ["⿱⿰氵奚鳥"]})
    assert v.substitutions(desc2, "鸂", "別") == {}


def test_a_component_spelled_out_inside_a_description_reads_the_same_shape():
    # 蘂 is ⿱艹橤 with 橤 ⿱惢木; 蘃 writes the same stack spelled out as ⿳艹歮木.
    # 惢 is itself ⿱心𢗰, which flattens 橤 to ⿳心𢗰木; 蘂 still reads ⿳艹惢木.
    desc = descriptions({"蘂": ["⿱艹橤"], "橤": ["⿱惢木"], "惢": ["⿱心𢗰"], "蘃": ["⿳艹歮木"]})
    assert v.substitutions(desc, "蘂", "蘃") == {("惢", "歮"): {None}}
    assert {(d.other, d.encoded) for d in v.derive(desc, {"惢": {"歮"}}, ["蘂"])} == {("蘃", True)}


def test_a_pair_that_shares_a_tree_attests_nothing():
    # 㤁 and 忝 are both ⿱天心 in one analysis: their other analyses show each character's own
    # looseness, not a way the two differ.
    desc = descriptions({"㤁": ["⿱天心"], "忝": ["⿱天心", "⿱夭心"]})
    assert v.substitutions(desc, "㤁", "忝") == {}
    alone = descriptions({"㤞": ["⿱天心"], "㤟": ["⿱夭心"]})
    assert set(v.substitutions(alone, "㤞", "㤟")) == {v.ordered("天", "夭")}


def test_a_component_without_a_description_of_its_own_still_predicts_its_bare_pair():
    # 火 and 灬 have no description of their own (their sequence is themselves); the pair they make
    # with each other is still a prediction.
    desc = v.Descriptions({"火": ["火"], "灬": ["灬"], "炎": ["⿱火火"]}, {})
    item = v.Attested(*v.ordered("火", "灬"), (("炎", "炎", ("s",)),), ("炎/炎",))
    predicted = v.predictions(desc, [item])
    assert ("火", "灬") in predicted[v.ordered("火", "灬")]


def test_an_attesting_pair_counts_only_where_the_others_predict_it_without_it():
    key = v.ordered("口", "厶")
    two = v.Attested(*key, (("㗀", "㗁", ("s",)), ("㗂", "㗃", ("s",))), ("a", "b"), (("a",), ("b",)))
    # Two pairs predicting only themselves: either one held out, the other alone is no pattern.
    assert v.agreeing([two], {key: {("㗀", "㗁"), ("㗂", "㗃")}}, {("㗀", "㗁"), ("㗂", "㗃")}) == []
    # One more prediction the graph states is the whole measure, and it agrees.
    wider = {key: {("㗀", "㗁"), ("㗂", "㗃"), ("㗄", "㗅")}}
    kept = v.agreeing([two], wider, {("㗀", "㗁"), ("㗂", "㗃"), ("㗄", "㗅")})
    assert [(k.predicted, k.agreed) for k in kept] == [(1, 1)]
    assert v.agreeing([two], wider, {("㗀", "㗁"), ("㗂", "㗃")}) == []
    # Three pairs in three contexts: each is predicted by the other two, so each counts.
    three = v.Attested(*key, (("㗀", "㗁", ("s",)), ("㗂", "㗃", ("s",)), ("㗄", "㗅", ("s",))),
                       ("a", "b", "c"), (("a",), ("b",), ("c",)))
    kept = v.agreeing([three], wider, {("㗀", "㗁"), ("㗂", "㗃"), ("㗄", "㗅")})
    assert [(k.predicted, k.agreed) for k in kept] == [(3, 3)]
    # Three pairs from two contexts: holding out the pair with a context of its own leaves one.
    shared = v.Attested(*key, three.pairs, ("a", "b"), (("a",), ("a",), ("b",)))
    assert [shared.held_out(at) for at in range(3)] == [True, True, False]


def test_a_substitution_makes_a_form_at_any_depth_of_the_characters_own_sequence():
    desc = descriptions({"㑑": ["⿱⿰宀口大"], "㑒": ["⿱⿰宀厶大"]})
    derived = list(v.derive(desc, {"口": {"厶"}}, ["㑑"]))
    assert [(d.other, d.encoded, d.was, d.became) for d in derived] == [("㑒", True, "口", "厶")]


def test_a_sequence_marked_approximate_subtracted_or_unrepresentable_describes_nothing():
    desc = v.Descriptions({"㑞": ["〾⿻一乚"], "㐆": ["㇯上一"], "㑇": ["⿰口？"]}, {})
    assert not ({"㑞", "㐆", "㑇"} & desc.trees.keys())
    assert all(not desc.trees.get(char) for char in ("㑞", "㐆", "㑇"))


def test_every_attesting_pair_is_among_the_predictions():
    desc = descriptions({"㗀": ["⿰口夂"], "㗁": ["⿰厶夂"]})
    item = v.Attested(*v.ordered("口", "厶"), (("㗀", "㗁", ("s",)),), ("㗀/㗁",))
    predicted = v.predictions(desc, [item])
    assert tuple(sorted(("㗀", "㗁"), key=ord)) in predicted[(item.a, item.b)]


def test_a_difference_inside_a_named_part_stays_one_position_when_the_part_is_spelled_out():
    # 溪 and 渓 differ in 奚 against 𢀖; spelled out, ⿰溪鳥 and ⿰渓鳥 also differ there at the top,
    # but both pairs reach it through 溪 against 渓, which is one position.
    desc = descriptions({"甲": ["⿰溪鳥"], "乙": ["⿰渓鳥"], "丙": ["⿰溪木"], "丁": ["⿰渓木"],
                         "溪": ["⿰氵奚"], "渓": ["⿰氵𢀖"]})
    found = v.attest(desc, sources(("甲", "乙"), ("丙", "丁")))
    assert found[v.ordered("奚", "𢀖")].contexts == ("渓/溪",)
    assert [(item.a, item.b) for item in v.kept(found)] == [v.ordered("溪", "渓")]


def test_a_bracketed_letter_of_a_region_tag_is_no_region():
    assert v.regions("⿱⺈⿸⿻口丿乚(GHTKP[B])") == frozenset("GHTKP")
    assert v.regions("⿰口夂(G[B])") & v.regions("⿰厶夂(J[B])") == frozenset()
    assert v.regions("⿰口夂([G])") == v.regions("⿰口夂") == v.EVERYWHERE
