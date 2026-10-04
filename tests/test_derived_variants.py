"""The `derived-ids` tier: forms up to two component substitutions make of a character."""

import json

from glyph_atlas import refs


def test_a_form_no_character_has_is_derived_as_its_sequence_with_its_evidence():
    # 寰 is ⿱宀睘, and 宀↔宂 is attested (㝓 and 䆟, …): ⿱宂睘 is a form no character has.
    derived = {row["char"]: row for row in refs.derived_variants("寰")}
    row = derived["⿱宂睘"]
    assert (row["encoded"], row["code_point"]) == (False, None)
    assert row["tier"] == "attested"
    sub, = row["routes"][0]
    assert (sub["was"], sub["became"], sub["tier"]) == ("宀", "宂", "attested")
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
    attested = [row for row in rows if row["tier"] == "attested"]
    assert len(attested) <= refs.DERIVED_SHOWN
    assert sum(not row["encoded"] for row in attested) <= refs.DERIVED_IDS_SHOWN
    assert len(rows) - len(attested) <= refs.DERIVED_EDITORIAL_SHOWN
    assert [row["tier"] for row in rows] == sorted((row["tier"] for row in rows), key=refs.TIERS.index)


def test_the_owners_form_of_yi_is_derived_with_two_editorial_substitutions():
    # 疑 is ⿰𠤕⿱龴疋 (𠤕 ⿱匕矢); 矢↔失 and コ↔龴 are editorial rows, so the form is editorial.
    rows = {row["char"]: row for row in refs.derived_variants("疑")}
    row = rows["⿰⿱匕失⿱コ疋"]
    assert (row["tier"], row["encoded"], row["ids"]) == ("editorial", False, "⿰⿱匕失⿱コ疋")
    assert [(s["was"], s["became"], s["tier"]) for s in row["routes"][0]] == [
        ("矢", "失", "editorial"), ("龴", "コ", "editorial")]
    assert row["routes"][0][0]["asserted_by"] == "mkpoli" and row["routes"][0][0]["asserted_at"] == "2026-10-04"
    assert refs.EDITORIAL_SOURCE in row["sources"]
    # 匕↔上 is attested, so a form that swaps it alone stays attested.
    assert rows["⿰⿱上矢⿱龴疋"]["tier"] == "attested"
    form = next(form for form in refs.derived_forms("疑") if form.ids == "⿰⿱匕失⿱コ疋")
    assert (form.substitutions, form.tier, form.encoded) == ((("矢", "失"), ("龴", "コ")), "editorial", False)


def test_an_attested_form_carries_no_editorial_route_and_an_editorial_one_no_attested_route_alone():
    for char in ("疑", "寰", "還"):
        for row in refs.derived_variants(char, limit=None):
            tiers = [{sub["tier"] for sub in route} for route in row["routes"]]
            if row["tier"] == "attested":
                assert all(found == {"attested"} for found in tiers)
            else:
                assert all("editorial" in found for found in tiers)


def test_the_editorial_table_never_repeats_an_attested_substitution():
    assert refs.editorial_variants()
    assert not set(refs.editorial_variants()) & set(refs.component_variants())
    assert refs.substitution_tier("失", "矢") == refs.substitution_tier("矢", "失") == "editorial"
    assert refs.substitution_tier("上", "匕") == "attested"



def test_the_exported_rows_are_the_characters_own_list_in_order():
    # A derivation is not symmetric: 𪞱 derives 壳, and 壳 does not derive 𪞱.
    for char in ("壳", "㟄", "妳", "寰"):
        listed = refs.derived_variants(char)
        rows = refs.derived_rows_of(char)
        assert [(rank, form) for _, rank, form, _, _ in rows] == list(enumerate(e["char"] for e in listed))
        for (_, _, _, routes, tier), entry in zip(rows, listed, strict=True):
            assert json.loads(routes) == [[[s["was"], s["became"]] for s in route] for route in entry["routes"]]
            assert tier == entry["tier"]
