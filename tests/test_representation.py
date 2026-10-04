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


def test_ids_are_what_the_worker_derives_for_the_same_names():
    # The Worker's representation.test.ts holds the same values.
    vectors = {"𮟃": ("rp:b78a8585df2e755ac741ca9c16c933f4", "fm:9a7e168e47aef52e2958cff4ba7cf858"),
               "⿺辶𦊷": ("rp:4fd4eff3084f8bbbe5489e119eea2862", "fm:9c8fb02868fdcbc3357a0b63ab17f5f4"),
               "葛\U000E0100": ("rp:e3640385ca612cd97bec3b1e527fd705", "fm:8b7650bbf9099669cfa0f0132a3bd18a")}
    for value, (rep, form) in vectors.items():
        typed = representation.typed(value)
        assert (typed.id, representation.anchored_form(typed)) == (rep, form)
    assert Representation("pua", "", "example", "1").id != Representation("pua", "", "example", "2").id
