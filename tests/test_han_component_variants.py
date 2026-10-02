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
    # 口 and 厶 swap in four pairs of characters; the graph gives one of them as variants.
    desc = descriptions({"㗀": ["⿰口夂"], "㗁": ["⿰厶夂"], "㗂": ["⿰口日"], "㗃": ["⿰厶日"],
                         "㗄": ["⿰口月"], "㗅": ["⿰厶月"], "㗆": ["⿰口木"], "㗇": ["⿰厶木"]})
    item = v.Attested(*v.ordered("口", "厶"), (("㗀", "㗁", ("s",)),), ("x/y",))
    predicted = v.predictions(desc, [item])
    assert predicted[(item.a, item.b)] == {("㗀", "㗁"), ("㗂", "㗃"), ("㗄", "㗅"), ("㗆", "㗇")}
    assert v.agreeing([item], predicted, {("㗀", "㗁")}) == []
    kept = v.agreeing([item], predicted, {("㗀", "㗁"), ("㗂", "㗃")})
    assert [(k.predicted, k.agreed) for k in kept] == [(4, 2)]


def test_the_committed_table_holds_only_what_the_threshold_and_the_agreement_keep():
    found = rows()
    assert len(found) > 2000
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


def test_the_example_substitution_is_in_the_committed_table():
    by_key = {(row["a"], row["b"]): row for row in rows()}
    row = by_key[v.ordered("睘", "𦊷")]
    assert (row["count"], row["predicted"], row["agreed"]) == ("2", "3", "2")
    assert {token.split("=")[0] for token in row["pairs"].split(" ")} == {"環:𤨔", "還:𮟃"}


def test_口_厶_is_kept_and_a_swap_of_meaning_is_not():
    by_key = {(row["a"], row["b"]) for row in rows()}
    assert v.ordered("口", "厶") in by_key
    assert v.ordered("口", "氵") not in by_key and v.ordered("扌", "木") not in by_key
