from glyph_atlas.schema import Box, Classification, Script, Unit, VariantRef


def test_unit_keeps_hentaigana_and_jibo_apart_from_the_modern_reading():
    unit = Unit(
        id="u1", page_id="p1", box=Box(x=10, y=20, w=30, h=40),
        text_source="あ", reading="あ", unicode="U+1B003", script=Script.HENTAIGANA, jibo="愛",
    )
    assert unit.box.iiif_region() == "10,20,30,40"
    assert unit.unicode == "U+1B003" and unit.jibo == "愛" and unit.reading == "あ"


def test_a_standalone_crop_needs_no_page():
    unit = Unit(id="hi:34010096", crop="all/characters/U+755B/34010096.jpg", unicode="U+755B", script=Script.KANJI,
                classification=Classification.IDENTIFIED)
    assert unit.page_id is None and unit.box is None


def test_a_split_pair_keeps_a_local_shape_id_beside_the_code_point():
    unit = Unit(id="u2", page_id="p1", box=Box(x=0, y=0, w=1, h=1), reading="か", unicode="U+1B019", jibo="可",
                script=Script.HENTAIGANA, variants=[VariantRef(scheme="mj", id="MJ090024"), VariantRef(scheme="local", id="ka-ka-a")])
    assert [v.scheme for v in unit.variants] == ["mj", "local"]
