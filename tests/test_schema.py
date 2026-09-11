from kuzushiji_atlas.schema import Box, Script, Unit


def test_unit_keeps_hentaigana_and_jibo_apart_from_the_modern_reading():
    unit = Unit(
        id="u1", page_id="p1", box=Box(x=10, y=20, w=30, h=40),
        text_source="あ", reading="あ", unicode="U+1B003", script=Script.HENTAIGANA, jibo="愛",
    )
    assert unit.box.iiif_region() == "10,20,30,40"
    assert unit.unicode == "U+1B003" and unit.jibo == "愛" and unit.reading == "あ"
