from __future__ import annotations

import numpy as np
import pytest

from glyph_atlas import compose
from glyph_atlas.compose import Contour, Part, Placed


def square(x0: float, y0: float, x1: float, y1: float, clockwise: bool = False) -> Contour:
    points = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    if clockwise:
        points = points[::-1]
    light = np.array(points, dtype=float)
    ops = [("moveTo", 1, False), ("lineTo", 1, False), ("lineTo", 1, False), ("lineTo", 1, False)]
    return Contour(ops, light, light.copy())


def test_a_sequence_parses_into_its_operators_and_operands() -> None:
    assert compose.parse("⿰亻⿱亠女") == ("⿰", "亻", ("⿱", "亠", "女"))
    assert compose.parse("⿲木木木") == ("⿲", "木", "木", "木")
    assert compose.parse("⿰葛\U000e0100木".replace("\U000e0100", "︀")) == ("⿰", "葛︀", "木")
    assert compose.key(compose.parse("⿰亻⿱亠女")) == "⿰亻⿱亠女"
    for broken in ("⿰亻", "⿰亻哥人"):
        with pytest.raises(ValueError):
            compose.parse(broken)


def test_a_host_is_cut_where_no_outline_crosses() -> None:
    left, right = square(0, 0, 300, 800), square(400, 0, 900, 800)
    pieces = compose.split(Part([right, left]), 0, 2)
    assert [p.contours for p in pieces] == [[left], [right]]
    # Stacked operands come top first.
    top, bottom = square(0, 500, 900, 800), square(0, 0, 900, 400)
    assert [p.contours for p in compose.split(Part([bottom, top]), 1, 2)] == [[top], [bottom]]
    # A stroke running through both halves leaves no clean cut; one reaching a little under the
    # neighbour goes with the side that holds most of it.
    across = square(0, 350, 900, 450)
    assert compose.split(Part([left, right, across]), 0, 2) is None
    reach = square(0, 0, 500, 60)
    pieces = compose.split(Part([left, right, reach]), 0, 2)
    assert pieces is not None and any(c is reach for c in pieces[0].contours)


def test_a_counter_stays_with_the_outline_around_it() -> None:
    box, counter, other = square(0, 0, 300, 300), square(100, 100, 200, 200, clockwise=True), square(400, 0, 700, 300)
    pieces = compose.split(Part([counter, box, other]), 0, 2)
    assert [sorted(map(id, p.contours)) for p in pieces] == [sorted([id(box), id(counter)]), [id(other)]]


def test_contours_with_the_same_box_are_one_unit() -> None:
    a, b = square(0, 0, 300, 300), square(0, 0, 300, 300, clockwise=True)
    other = square(400, 0, 700, 300)
    pieces = compose.split(Part([a, b, other]), 0, 2)
    assert [len(p.contours) for p in pieces] == [2, 1]


def test_a_stroke_inside_another_strokes_box_is_a_shape_of_its_own() -> None:
    # The strokes inside 図's 冂 wind as 冂 does: they lie in its box without being its counter.
    frame, stroke = square(0, 0, 900, 900), square(300, 300, 600, 600)
    assert sorted(len(u) for u in compose._units([frame, stroke])) == [1, 1]


def test_an_outline_inside_a_counter_is_a_shape_of_its_own() -> None:
    # 回: the inner 口 sits in the outer one's counter and keeps its own counter.
    outer, hole = square(0, 0, 900, 900), square(100, 100, 800, 800, clockwise=True)
    inner, inner_hole = square(300, 300, 600, 600), square(350, 350, 550, 550, clockwise=True)
    units = compose._units([outer, hole, inner, inner_hole])
    assert sorted(sorted(map(id, u)) for u in units) == sorted([sorted([id(outer), id(hole)]), sorted([id(inner), id(inner_hole)])])


def test_a_region_tag_of_any_form_is_read_off_a_sequence() -> None:
    for tagged in ("⿻臼丨(G[B])", "⿰亻可(GHTJKPV)", "⿱艹化(UTC2003)"):
        assert compose.REGIONS.match(tagged).group(1) == tagged.split("(")[0]


def test_an_enclosed_operand_is_the_outline_deepest_inside_the_closed_sides() -> None:
    # ⿸: a 厂-like frame closing the left and the top, and a block inside it.
    frame = [square(0, 0, 100, 900), square(0, 800, 900, 900)]
    inner = square(300, 0, 800, 600)
    ways = compose.enclosures(Part([*frame, inner]), "⿸")
    outer, enclosed = ways[0]
    assert enclosed.contours == [inner]
    assert sorted(map(id, outer.contours)) == sorted(map(id, frame))


def test_a_box_is_carried_into_a_region_in_proportion() -> None:
    assert compose.mapped((25, 25, 75, 50), (0, 0, 100, 100), (100, 100, 300, 500)) == (150, 200, 250, 300)


def test_overlapping_outlines_fill_by_the_nonzero_rule() -> None:
    a, b = square(0, 0, 600, 600), square(400, 400, 1000, 1000)
    rings = compose._rings([a, b], [a.light, b.light])
    mask = compose.fill(rings, 10, (0, 0, 1000, 1000))
    assert mask[5, 5] and mask[4, 4]  # where the squares overlap, still ink
    assert not mask[0, 0] and not mask[9, 9]
    # A counter wound the other way is a hole.
    ring = square(0, 0, 1000, 1000)
    hole = square(300, 300, 700, 700, clockwise=True)
    mask = compose.fill(compose._rings([ring, hole], [ring.light, hole.light]), 10, (0, 0, 1000, 1000))
    assert not mask[5, 5] and mask[1, 1]


def test_a_contour_of_only_off_curve_points_starts_between_its_last_and_first() -> None:
    points = np.array([(0, 0), (100, 0), (100, 100), (0, 100)], dtype=float)
    segments = compose._segments([("qCurveTo", 4, True)], points)
    assert segments[0][0] == "M" and tuple(segments[0][1][0]) == (0, 50)
    assert segments[-1][1][1].tolist() == [0, 50]


def test_a_part_is_thickened_along_an_axis_by_its_weight() -> None:
    heavy = np.array([(-10, 0), (110, 0), (110, 100), (-10, 100)], dtype=float)
    part = Part([Contour(square(0, 0, 100, 100).ops, square(0, 0, 100, 100).light, heavy)])
    (points,) = Placed(part, part.box, part.box, (0.5, 0.0)).contours()
    assert points[:, 0].min() == -5 and points[:, 0].max() == 105
    assert points[:, 1].min() == 0 and points[:, 1].max() == 100


def test_stroke_width_is_the_usual_short_run() -> None:
    mask = np.zeros((100, 100), bool)
    mask[10:90, 20:28] = True  # a stem 8 px wide
    mask[40:46, 10:90] = True  # a bar 6 px thick
    across_x, across_y = compose.stems(mask, 1.0)
    assert across_x == pytest.approx(8, abs=0.5) and across_y == pytest.approx(6, abs=0.5)


def test_stroke_width_falls_as_a_character_fills_up() -> None:
    for axis in (0, 1):
        assert compose.stem(5000, axis) > compose.stem(20000, axis)
    # Horizontal strokes thin faster than vertical ones.
    assert compose.stem(20000, 1) < compose.stem(20000, 0)


def test_a_part_keeps_its_proportions_within_the_stretch_allowed() -> None:
    assert compose.fitted((0, 0, 100, 100), (0, 0, 400, 100)) == (120.0, 0, 280.0, 100)
    assert compose.fitted((0, 0, 100, 100), (0, 0, 150, 100)) == (0, 0, 150, 100)


@pytest.fixture(scope="module")
def composer() -> compose.Composer:
    from glyph_atlas.images import cache_root

    path = cache_root() / "fonts" / compose.FONT_FILE
    if not path.exists():
        pytest.skip(f"{compose.FONT_FILE} is not cached; `scripts/compose_ids.py draw` fetches it.")
    return compose.Composer(compose.Font(path), compose.japanese_sequences())


def test_an_encoded_character_is_redrawn_close_to_its_glyph_without_itself(composer: compose.Composer) -> None:
    glyph = composer.font.glyph("何")
    composer.exclude = {"何"}
    try:
        drawn = compose.raster(composer.compose("⿰亻可"), 96)
    finally:
        composer.exclude = set()
    real = compose.raster([Placed(glyph, glyph.box, glyph.box)], 96)
    assert (drawn & real).sum() / (drawn | real).sum() > 0.5


def test_a_variation_sequence_draws_the_glyph_the_font_maps_it_to(composer: compose.Composer) -> None:
    font = composer.font
    assert font.has("葛\U000e0100") and font.glyph("葛\U000e0100").box != font.glyph("葛").box
    assert not font.has("葛\U000e01ef")
    assert len(composer.compose("⿰葛\U000e0100木")) == 2


def test_characters_whose_sequences_name_each_other_end_in_lookup_error(composer: compose.Composer) -> None:
    looped = compose.Composer(composer.font, {"\U000f0000": "⿰\U000f0001木", "\U000f0001": "⿱\U000f0000口"})
    with pytest.raises(LookupError):
        looped.compose("⿰\U000f0000口")
    with pytest.raises(LookupError):
        compose.Composer(composer.font, {"\U000f0000": "⿰木"}).compose("⿱\U000f0000口")


def test_an_operand_is_found_in_its_positional_form(composer: compose.Composer) -> None:
    assert composer.alternatives("王", "L")[:2] == ["王", "𤣩"]
    assert "氵" in composer.alternatives("水", "L") and "氵" not in composer.alternatives("水", "R")
    assert "甡" in composer.alternatives(compose.parse("⿰生生"), "T")


def test_strokes_come_out_at_the_width_of_a_real_glyph(composer: compose.Composer) -> None:
    ratios = []
    for char in "何謌林話縫":
        glyph = composer.font.glyph(char)
        composer.exclude = {char}
        try:
            drawn = compose.raster(composer.compose(composer.sequences[char]), 400)
        finally:
            composer.exclude = set()
        made = compose.stems(drawn, compose.EM / 400)
        true = compose.stems(compose.raster([Placed(glyph, glyph.box, glyph.box)], 400), compose.EM / 400)
        ratios.append((made[0] / true[0], made[1] / true[1]))
    assert np.median(np.array(ratios), axis=0) == pytest.approx([1.0, 1.0], abs=0.08)


def test_every_part_comes_from_a_drawn_character(composer: compose.Composer) -> None:
    placed = composer.compose("⿰亻哥")
    assert all(p.origin.split()[0] in ("transplant", "teacher", "host", "instance", "glyph") for p in placed)
    assert any(p.origin.split()[0] in ("transplant", "teacher") for p in placed)


def test_a_character_unicode_lacks_is_drawn_from_its_parts(composer: compose.Composer) -> None:
    placed = composer.compose("⿰亻哥")
    assert len(placed) == 2
    left, right = (p.target for p in placed)
    assert left[0] + left[2] < right[0] + right[2]  # 亻 stands left of 哥
    assert right[2] - right[0] > 1.5 * (left[2] - left[0])
    assert compose.svg_path(placed).startswith("M")
    # A sequence that describes an encoded character draws that character whole (⿻木口 is 束); one
    # that does not, with operands drawn across each other, is not laid out.
    assert [p.part for p in composer.compose("⿻木口")] == [composer.font.glyph("束")]
    assert "⿻乙口" not in composer.by_sequence
    with pytest.raises(LookupError):
        composer.compose("⿻乙口")


def test_an_enclosure_is_cut_from_a_character_drawing_its_inner_part_interlocked(composer: compose.Composer) -> None:
    # 図 draws ⺀ in the arms of 㐅; a stacked sketch of ⿱⺀㐅 looks unlike it, but 囗 is unmistakable.
    tree = compose.parse("⿴囗⿱⺀㐅")
    host = composer._host_node("図", tree, ())
    assert host is not None
    assert len(host.pieces[1].contours) == composer._outlines(tree[2])
