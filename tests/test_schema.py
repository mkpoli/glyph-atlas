import pytest

from glyph_atlas import refs
from glyph_atlas.schema import Box, Character, Classification, Script, Unit


def test_a_unit_names_its_character_and_the_character_layer_has_its_kana():
    unit = Unit(
        id="u1", page_id="p1", box=Box(x=10, y=20, w=30, h=40),
        text_source="あ", unicode="U+1B003", script=Script.HENTAIGANA,
    )
    assert unit.box.iiif_region() == "10,20,30,40"
    assert unit.unicode == "U+1B003" and "reading" not in Unit.model_fields
    assert refs.character("U+1B003").readings == ["あ"]
    # The 字母 is not a field of the unit: it belongs to the character, and the layer has it.
    assert refs.jibo_of_unit(unit.unicode) == "愛"
    assert refs.character("U+1B003").jibo == ["愛"]


def test_a_character_is_the_middle_layer():
    character = Character(
        code_point="U+1B127", char="𛄧", name="KATAKANA LETTER ALTERNATE NE", script=Script.KATAKANA,
        age="18.0", block="Kana Extended-A", jibo=["子"], readings=["ね"], grapheme="U+306D",
        confusables=["U+5B50"],
    )
    assert character.jibo == ["子"] and character.readings == ["ね"]
    assert character.grapheme == "U+306D" and character.confusables == ["U+5B50"]


def test_a_standalone_crop_needs_no_page():
    unit = Unit(id="hi:34010096", crop="all/characters/U+755B/34010096.jpg", unicode="U+755B", script=Script.HAN,
                classification=Classification.IDENTIFIED)
    assert unit.page_id is None and unit.box is None


def test_a_unit_written_with_the_fields_an_earlier_schema_had_reads_while_they_are_empty():
    unit = Unit.model_validate({"id": "u2", "unicode": "U+1B019", "written_form": None, "variants": []})
    assert "variants" not in unit.model_dump() and "written_form" not in unit.model_dump()
    for retired in ({"variants": [{"scheme": "mj", "id": "MJ090024"}]}, {"written_form": "𮟃"}):
        with pytest.raises(ValueError, match="migrate_written_forms"):
            Unit.model_validate({"id": "u2", **retired})
