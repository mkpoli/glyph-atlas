from pathlib import Path

import pytest

from glyph_atlas import representation
from glyph_atlas.representation import Representation


@pytest.mark.parametrize("value", ["𮟃", "還", "が", "葛\U000E0100", "⿺辶𦊷", "⿰氵⿱冖月", "⿲木木木", "⿱⿰口口⿰口口",
                                   "〾⿰木？", "⿰木", "⿰⺡骨"])
def test_a_character_or_a_description_is_a_form_a_reviewer_may_type(value):
    assert representation.check_text(value) == value


@pytest.mark.parametrize("value", ["", " 還", "還還", "⿰木", "⿰木木木", "⿲木木", "⿰木a", "⿰木 木", "゙",
                                   "⿰", "x" * 65, "⿰木\u200b", "⿰木あ", "⿰木ー"])
def test_anything_else_is_refused(value):
    with pytest.raises(ValueError):
        representation.check_text(value)


def test_what_is_typed_takes_the_scheme_it_is_written_in():
    assert representation.typed("𮟃") == Representation("unicode", "𮟃")
    assert representation.typed("⿺辶𦊷") == Representation("ids", "⿺辶𦊷")
    assert representation.typed("葛\U000E0100") == Representation("ivs", "葛\U000E0100")
    with pytest.raises(ValueError, match="namespace"):
        representation.typed("")


@pytest.mark.parametrize("bad", [Representation("mj", "MJ12345"), Representation("glyphwiki", "U2E7C3"),
                                 Representation("pua", ""), Representation("pua", "", "example"),
                                 Representation("pua", "木", "example", "1"), Representation("ids", "木"),
                                 Representation("ivs", "葛"), Representation("unicode", "葛\U000E0100"),
                                 Representation("unicode", "⿰木木"), Representation("lsk", "x")])
def test_each_scheme_refuses_a_value_it_cannot_hold(bad):
    with pytest.raises(ValueError):
        representation.check(bad)


def test_the_registries_and_a_namespaced_private_use_form_are_held():
    for good in (Representation("mj", "MJ012345", version="006.01"), Representation("glyphwiki", "u2e7c3-j"),
                 Representation("pua", "", "example", "1")):
        assert representation.check(good) is good


def test_a_text_names_its_characters_and_descriptions_one_after_another():
    # apps/cloudflare/src/representation.test.ts holds the same values.
    assert representation.written("說⿰月兊説") == ["說", "⿰月兊", "説"]
    assert representation.written("⿰氵⿵戊天⿱𥫗冊") == ["⿰氵⿵戊天", "⿱𥫗冊"]
    assert representation.written("葛\U000E0100峰") == ["葛\U000E0100", "峰"]
    assert representation.written("峰⿰山") == ["峰"]
    assert representation.written("⿰山a峰") == []
    assert representation.written("") == []
    assert representation.written("峰" + "〾" * 2000 + "山峯") == ["峰"]


def test_ids_are_what_the_worker_derives_for_the_same_names():
    # The Worker's representation.test.ts holds the same values.
    vectors = {"𮟃": ("rp:b78a8585df2e755ac741ca9c16c933f4", "fm:9a7e168e47aef52e2958cff4ba7cf858"),
               "⿺辶𦊷": ("rp:4fd4eff3084f8bbbe5489e119eea2862", "fm:9c8fb02868fdcbc3357a0b63ab17f5f4"),
               "葛\U000E0100": ("rp:e3640385ca612cd97bec3b1e527fd705", "fm:8b7650bbf9099669cfa0f0132a3bd18a")}
    for value, (rep, form) in vectors.items():
        typed = representation.typed(value)
        assert (typed.id, representation.anchored_form(typed)) == (rep, form)
    assert Representation("pua", "", "example", "1").id != Representation("pua", "", "example", "2").id


def test_a_compatibility_ideograph_stands_for_a_unified_one_and_the_blocks_unified_ideographs_do_not():
    # 說 U+F96F and U+F9A1 stand for 說 U+8AAA, 突 U+FA55 for 突 U+7A81, 杓 U+2F8DC for 杓 U+6753.
    assert all(representation.compatibility(char) for char in "\uf96f\uf9a1\ufa55\U0002f8dc")
    # 﨑 U+FA11 and 﨟 U+FA1F are unified ideographs; a sequence and a description are no code point.
    assert not any(representation.compatibility(text) for text in ("說", "\ufa11", "\ufa1f", "說︀", "⿰言兌", "が", ""))


def test_a_typed_compatibility_ideograph_is_its_registered_figure_or_its_unified_ideograph():
    # 突 U+FA55 implements MJ030283, 突 U+7A81 U+E0103; 說 U+F96F has no registered figure.
    assert representation.typed("\ufa55") == representation.Representation("ivs", "突\U000e0103")
    assert representation.typed("\uf96f") == representation.Representation("unicode", "說")
    assert representation.stands_for("\ufa11") == "\ufa11"
    # As the base of a sequence or a part of a description it is its unified ideograph.
    assert representation.typed("\ufa55\U000e0103") == representation.Representation("ivs", "\u7a81\U000e0103")
    assert representation.typed("⿰\ufa55木") == representation.Representation("ids", "⿰\u7a81木")
    assert len(representation.figures()) == 89
    assert all(representation.compatibility(char) and len(sequence) == 2 for char, sequence in representation.figures().items())


def test_the_workers_figures_are_those_of_the_mj_table():
    import importlib.util
    path = Path(__file__).resolve().parents[1] / "scripts" / "build_han_compatibility.py"
    spec = importlib.util.spec_from_file_location("build_han_compatibility", path)
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    assert build.MODULE.read_text(encoding="utf-8") == build.module()


def test_a_form_holding_a_part_no_code_point_names_is_typed_in_its_one_spelling():
    one = Representation("ids", "⿱&X-00087;龴")
    assert representation.typed("⿱{88}龴") == representation.typed("⿱⿵𠆢一龴") == representation.typed("⿱&X-00087;龴") == one
    assert representation.typed("⿱&X-00087;龴").id == one.id
    assert representation.described("⿱&X-00087;龴") and representation.written("⿱&X-00087;龴令") == ["⿱&X-00087;龴", "令"]
    # A part is one component however long its identifier: sixteen of them fit in a form.
    assert representation.check_text("⿰" * 15 + "&X-00087;" * 16)
    with pytest.raises(ValueError, match="no such part"):
        representation.typed("⿱{999}龴")
    for stale in ("⿱&X-01653;龴", "⿱&X-99999;龴", "⿱&CDP-8BD0;龴"):
        with pytest.raises(ValueError):
            representation.check(Representation("ids", stale))
