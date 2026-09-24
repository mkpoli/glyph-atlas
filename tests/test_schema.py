from glyph_atlas import refs
from glyph_atlas.schema import Box, Character, Classification, Script, Unit, VariantRef


def test_unit_keeps_hentaigana_and_the_modern_reading_apart():
    unit = Unit(
        id="u1", page_id="p1", box=Box(x=10, y=20, w=30, h=40),
        text_source="あ", reading="あ", unicode="U+1B003", script=Script.HENTAIGANA,
    )
    assert unit.box.iiif_region() == "10,20,30,40"
    assert unit.unicode == "U+1B003" and unit.reading == "あ"
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


def test_a_split_pair_keeps_a_local_shape_id_beside_the_code_point():
    unit = Unit(id="u2", page_id="p1", box=Box(x=0, y=0, w=1, h=1), reading="か", unicode="U+1B019",
                script=Script.HENTAIGANA, variants=[VariantRef(scheme="mj", id="MJ090024"), VariantRef(scheme="local", id="ka-ka-a")])
    assert [v.scheme for v in unit.variants] == ["mj", "local"]
