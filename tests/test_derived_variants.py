"""The `derived-ids` tier: forms one attested component substitution makes of a character."""

import json

from glyph_atlas import refs


def test_a_form_no_character_has_is_derived_as_its_sequence_with_its_evidence():
    derived = {row["char"]: row for row in refs.derived_variants("寰")}
    row = derived["⿱宀𦊷"]
    assert (row["encoded"], row["code_point"]) == (False, None)
    sub = row["substitutions"][0]
    assert (sub["was"], sub["became"]) == ("睘", "𦊷")
    assert {(p["a"], p["b"]) for p in sub["pairs"]} == {("環", "𤨔"), ("還", "𮟃")}
    assert "wikidata" in row["sources"]


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
        for (_, _, _, subs), entry in zip(rows, listed, strict=True):
            assert json.loads(subs) == [[s["was"], s["became"]] for s in entry["substitutions"]]
