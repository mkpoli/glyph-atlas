"""The `derived-ids` tier: forms up to two component substitutions make of a character."""

import json

from glyph_atlas import refs


def test_a_form_no_character_has_is_derived_as_its_sequence_with_its_evidence():
    # 寰 is ⿱宀睘, and 宀↔宂 is attested (㝓 and 䆟, …): ⿱宂睘 is a form no character has.
    derived = {row["char"]: row for row in refs.derived_variants("寰")}
    row = derived["⿱宂睘"]
    assert (row["encoded"], row["code_point"]) == (False, None)
    sub, = row["routes"][0]
    assert (sub["was"], sub["became"]) == ("宀", "宂")
    assert ("㝓", "䆟") in {(p["a"], p["b"]) for p in sub["pairs"]}
    assert row["sources"]


def test_a_pair_a_source_states_is_left_to_the_attested_tiers():
    assert "𮟃" not in {row["char"] for row in refs.derived_variants("還", limit=None)}


def test_two_unified_ideographs_are_never_derived_from_each_other():
    # 也↔它 is attested (蛇 and 虵, …), but 馳 and 駝 are two characters.
    assert "駝" not in {row["char"] for row in refs.derived_variants("馳", limit=None)}
    rows = refs.derived_variants("還", limit=None)
    assert "遝" not in {row["char"] for row in rows}


def test_a_card_lists_at_most_the_shown_rows_and_sequences():
    rows = refs.derived_variants("還")
    assert len(rows) <= refs.DERIVED_SHOWN
    assert sum(not row["encoded"] for row in rows) <= refs.DERIVED_IDS_SHOWN



def test_the_exported_rows_are_the_characters_own_list_in_order():
    # A derivation is not symmetric: 𪞱 derives 壳, and 壳 does not derive 𪞱.
    for char in ("壳", "㟄", "妳", "寰"):
        listed = refs.derived_variants(char)
        rows = refs.derived_rows_of(char)
        assert [(rank, form) for _, rank, form, _ in rows] == list(enumerate(e["char"] for e in listed))
        for (_, _, _, routes), entry in zip(rows, listed, strict=True):
            assert json.loads(routes) == [[[s["was"], s["became"]] for s in route] for route in entry["routes"]]


def test_the_owners_form_of_yi_is_derived_by_two_substitutions():
    # 疑 is ⿰𠤕⿱龴疋 and 𠤕 is ⿱匕矢: 矢→失 inside 𠤕 and 龴→コ beside it.
    forms = {form.form: form for form in refs.derived_forms("疑", limit=None)}
    form = forms["⿰⿱匕失⿱コ疋"]
    assert (form.ids, form.encoded, form.substitutions) == ("⿰⿱匕失⿱コ疋", False, (("矢", "失"), ("龴", "コ")))
    row = next(row for row in refs.derived_variants("疑", limit=None) if row["char"] == "⿰⿱匕失⿱コ疋")
    assert "mkpoli-2026-10-04" in row["sources"] and "tier" not in row


def test_a_stated_substitution_is_a_row_like_any_other_with_its_statement_as_a_pair():
    row = refs.component_variants()[("失", "矢")]
    assert {"a": "失", "b": "矢", "sources": ["mkpoli-2026-10-04"]} in row["pairs"]
    assert ("迭", "𨒔") in {(p["a"], p["b"]) for p in row["pairs"]}
    assert refs.component_variants()[("コ", "龴")]["pairs"] == [{"a": "コ", "b": "龴", "sources": ["mkpoli-2026-10-04"]}]
    assert refs.component_variant_sources()["mkpoli-2026-10-04"].startswith("mkpoli (Glyph Atlas maintainer), instruction of 2026-10-04")
