"""Drawn forms: the table, the names it gives, and the files built from it."""

from __future__ import annotations

import importlib.util
import io
import re
from pathlib import Path

import pytest

from glyph_atlas import drawn_forms, form_clusters, forms, refs, representation

ROOT = Path(__file__).resolve().parents[1]
RU, FANG, HOOK = "\U000f0000", "\U000f0001", "\U000f0002"


def _build():
    spec = importlib.util.spec_from_file_location("build_drawn_forms", ROOT / "scripts" / "build_drawn_forms.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    return build


def test_the_table_names_each_form_its_grapheme_and_its_drawing():
    table = drawn_forms.table()
    assert (table.namespace, table.version) == ("glyph-atlas", "1")
    assert [form.code_point for form in drawn_forms.written_for("事")] == ["U+F0000", "U+F0001", "U+F0002"]
    assert drawn_forms.written_for("也") == []
    assert drawn_forms.drawn(RU).written_for == "事" and drawn_forms.drawn("\U000f0100") is None
    for form in table.forms.values():
        assert drawn_forms.AREA[0] <= ord(form.char) <= drawn_forms.AREA[1]
        # One filled path in the em the fonts are built on, in each style.
        for style in drawn_forms.STYLES:
            assert re.fullmatch(r'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 1000">\n  <path d="[MCZ0-9, -]+"/>\n</svg>\n',
                                form.drawing(style).read_text(encoding="utf-8"))


def test_a_drawn_form_is_a_private_use_representation_in_the_tables_namespace():
    # apps/cloudflare/src/representation.test.ts holds the same values.
    typed = representation.typed(RU)
    assert typed == representation.Representation("pua", RU, "glyph-atlas", "1")
    assert (typed.id, representation.anchored_form(typed)) == ("rp:2f0d058ce9a2c8ddbfd1a3d61533d290", "fm:9b7ed9e8487cec8865fe33b08841f13b")
    with pytest.raises(ValueError, match="names nothing without the namespace"):
        representation.typed("\U000f0100")


def test_a_drawn_form_is_a_character_of_the_family_it_writes():
    info = refs.grapheme_info("U+4E8B")
    assert [member["code_point"] for member in info["members"]][-4:] == ["U+F0000", "U+F0001", "U+F0002", "U+3D8B7"]
    row = refs.character("U+F0000")
    assert (row.char, row.grapheme, str(row.script), row.name) == (RU, "U+4E8B", "han", None)
    assert refs.grapheme_info("U+F0000")["code_point"] == "U+4E8B"
    # No other family holds one.
    assert all(ord(member["char"]) < 0xF0000 for member in refs.grapheme_info("U+4E5F")["members"])


def test_a_drawn_form_is_filed_as_the_grapheme_it_writes_is():
    spec = importlib.util.spec_from_file_location("cloudflare_schema", ROOT / "scripts" / "cloudflare_schema.py")
    schema = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(schema)
    assert [schema.category_of(label) for label in (RU, "事", "\U000f0100")] == ["kanji", "kanji", "other"]
    # A kanji with other characters keeps its family in a clustering, its drawn forms among them.
    family = form_clusters.family_of("U+4E8B")
    assert [member["code_point"] for member in family["members"]][-3:] == ["U+F0000", "U+F0001", "U+F0002"] and family["relation"] != "kanji-variants"


def test_a_clustering_made_before_the_table_still_offers_the_familys_drawn_forms(monkeypatch):
    monkeypatch.setattr(forms, "family_members", lambda family: ["事", "亊"] if family == "U+4E8B" else ["也"])
    assert forms.drawn_members("U+4E8B") == [RU, FANG, HOOK]
    assert forms.drawn_members("U+4E5F") == []
    # One made after it publishes them itself.
    monkeypatch.setattr(forms, "family_members", lambda family: ["事", "亊", RU, FANG, HOOK])
    assert forms.drawn_members("U+4E8B") == []


def _outlines(data: bytes) -> dict[int, list]:
    """Each mapped code point's outline, as the pen calls that draw it."""
    from fontTools.pens.recordingPen import RecordingPen
    from fontTools.ttLib import TTFont

    font = TTFont(io.BytesIO(data))
    glyphs, drawn = font.getGlyphSet(), {}
    for code, name in font.getBestCmap().items():
        pen = RecordingPen()
        glyphs[name].draw(pen)
        drawn[code] = pen.value
    return drawn


def test_the_fonts_the_stylesheet_and_the_workers_table_are_those_of_the_table():
    build = _build()
    assert build.MODULE.read_text(encoding="utf-8") == build.module()
    names = {}
    for style in drawn_forms.STYLES:
        fonts = sorted(build.OUT.glob(f"drawn-{style}-*.woff2"))
        assert len(fonts) == 1
        names[style] = fonts[0].name
        # The font maps each form's code point and nothing else, to the drawing as it stands. The
        # outlines are compared, since the compressed bytes depend on the libraries that wrote them.
        stored = _outlines(fonts[0].read_bytes())
        assert sorted(stored) == sorted(ord(char) for char in drawn_forms.table().forms)
        assert stored == _outlines(build.font(style))
        assert all(len(outline) > 20 for outline in stored.values())
    assert len(list(build.OUT.glob("*.woff2"))) == len(drawn_forms.STYLES)
    assert build.CSS.read_text(encoding="utf-8") == build.css(names)
    # The two cuts differ.
    assert _outlines(build.font("sans")) != _outlines(build.font("serif"))


@pytest.mark.parametrize("style", drawn_forms.STYLES)
def test_looped_shi_matches_its_neighbors_stroke_width(style):
    from fontTools.pens.areaPen import AreaPen
    from fontTools.pens.perimeterPen import PerimeterPen
    from fontTools.pens.recordingPen import replayRecording

    outlines = _outlines(_build().font(style))

    def stroke_width(char):
        area, perimeter = AreaPen(), PerimeterPen()
        replayRecording(outlines[ord(char)], area)
        replayRecording(outlines[ord(char)], perimeter)
        return 2 * abs(area.value) / perimeter.value

    target = (stroke_width(FANG) + stroke_width(HOOK)) / 2
    # Allow a font unit for rounding when sampling and compiling the contours.
    assert stroke_width(RU) == pytest.approx(target, abs=1)
