"""Tests of the page-level line assignment: each detection belongs to one line."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from glyph_atlas import align, line_assignment
from glyph_atlas.schema import Box, Line

FIXTURES = Path(__file__).parent / "fixtures" / "line-assignment"


def page(name: str) -> tuple[list[Line], list[Box]]:
    data = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    return [Line(**line) for line in data["lines"]], [Box(**box) for box in data["detections"]]


def owner(lines: list[Line], boxes: list[Box], x: int, y: int) -> str | None:
    found = line_assignment.assign(lines, boxes)
    index = next(index for index, box in enumerate(boxes) if (box.x, box.y) == (x, y))
    return next((line for line, held in found.lines.items() if index in held), None)


def test_the_first_character_of_a_line_goes_to_its_own_line_on_gansho_hikaecho_38():
    # 上 of 一上田屋兼三郎 sits inside the boxes of its line and of 岸町村田屋…, whose alignment called it 岸.
    lines, boxes = page("gansho-hikaecho-38")
    assert owner(lines, boxes, 2524, 833) == "hl:db756a349241476df2db53a0cfcd6cd6_38_022"


def test_a_character_of_the_next_column_goes_to_its_own_line_on_osakitegumi_20():
    # 大 of 引揚御鍵者此節大目付 sits inside the box of 差出昼九ツ時…, whose alignment called it 夕.
    lines, boxes = page("osakitegumi-20")
    assert owner(lines, boxes, 3409, 1536) == "hl:5B7E3F3297D1937C6677C28010C1C482_20_004"


@pytest.mark.parametrize("name", ["gansho-hikaecho-38", "osakitegumi-20", "kamosha-kiroku-12-25"])
def test_no_detection_belongs_to_two_lines(name):
    lines, boxes = page(name)
    held = Counter(index for found in line_assignment.assign(lines, boxes).lines.values() for index in found)
    assert held and max(held.values()) == 1


def test_every_line_of_osakitegumi_20_takes_ink_inside_its_own_box():
    lines, boxes = page("osakitegumi-20")
    found = line_assignment.assign(lines, boxes)
    for line in lines:
        held = found.lines[line.id]
        assert held, line.id
        assert all(line_assignment.inside(line.box, line_assignment.centre(boxes[index])) for index in held)


def test_the_columns_of_side_by_side_lines_are_not_shared_on_osakitegumi_20():
    # Without the assignment, the boxes are wide enough that most detections fall in two of them.
    lines, boxes = page("osakitegumi-20")
    inside = Counter(index for index, box in enumerate(boxes) for line in lines
                     if line_assignment.inside(line.box, line_assignment.centre(box)))
    assert sum(1 for count in inside.values() if count > 1) > 100


def test_repeated_records_of_one_line_are_collapsed_into_the_tightest():
    lines, boxes = page("kamosha-kiroku-12-25")
    found = line_assignment.assign(lines, boxes)
    prefix = "hl:68C7BEFABCCE5DD6A2327799118C7CD1_25_"
    assert found.duplicates == {prefix + "012": prefix + "013", prefix + "015": prefix + "013",
                                prefix + "019": prefix + "021", prefix + "020": prefix + "022"}
    for dropped in found.duplicates:
        assert found.lines[dropped] == []
    assert found.lines[prefix + "013"]


def vertical(line_id: str, x: int, y: int, w: int, h: int, text: str, seq: int = 0) -> Line:
    return Line(id=line_id, page_id="p:0", seq=seq, box=Box(x=x, y=y, w=w, h=h), vertical=True, text_raw=text,
                text=text)


def column(x: int, ys: tuple[int, ...], w: int = 40) -> list[Box]:
    return [Box(x=x, y=y, w=w, h=36) for y in ys]


def test_wide_boxes_over_three_columns_take_one_column_each():
    ys = (10, 60, 110, 160)
    boxes = column(300, ys) + column(200, ys) + column(100, ys)
    lines = [vertical("right", 240, 0, 160, 210, "一二三四", 0), vertical("middle", 140, 0, 160, 210, "五六七八", 1),
             vertical("left", 40, 0, 160, 210, "九十百千", 2)]
    found = line_assignment.assign(lines, boxes).lines
    assert found == {"right": [0, 1, 2, 3], "middle": [4, 5, 6, 7], "left": [8, 9, 10, 11]}


def test_two_lines_one_above_the_other_share_their_column():
    boxes = column(100, (10, 60, 300, 350))
    lines = [vertical("upper", 80, 0, 80, 110, "一二", 0), vertical("lower", 80, 290, 80, 110, "三四", 1),
             vertical("beside", 20, 0, 80, 400, "", 2)]
    found = line_assignment.assign(lines, boxes).lines
    assert found["upper"] == [0, 1] and found["lower"] == [2, 3]


def test_a_column_the_ink_grouping_chained_across_two_lines_is_split_between_them():
    # Characters that wander sideways chain two columns 50 px apart into one.
    boxes = [Box(x=x, y=y, w=40, h=36) for x, y in ((200, 10), (175, 60), (150, 110), (200, 160),
                                                     (150, 10), (125, 60), (150, 160))]
    lines = [vertical("right", 160, 0, 100, 210, "一二三四", 0), vertical("left", 110, 0, 100, 210, "五六七", 1)]
    found = line_assignment.assign(lines, boxes).lines
    assert set(found["right"]) | set(found["left"]) == set(range(7))
    assert not set(found["right"]) & set(found["left"])
    assert {0, 3} <= set(found["right"]) and {4, 6} <= set(found["left"])


def test_a_horizontal_line_takes_only_what_no_vertical_line_took():
    boxes = column(100, (10, 60)) + [Box(x=10, y=300, w=30, h=30)]
    lines = [vertical("column", 80, 0, 80, 110, "一二", 0),
             Line(id="caption", page_id="p:0", seq=1, box=Box(x=0, y=40, w=200, h=300), vertical=False,
                  text_raw="三四", text="三四")]
    found = line_assignment.assign(lines, boxes).lines
    assert found == {"column": [0, 1], "caption": [2]}


def test_a_page_aligned_as_a_whole_places_no_box_in_two_lines():
    lines, boxes = page("gansho-hikaecho-38")
    detections = [align.Detection(box=box, score=1.0) for box in boxes]
    aligned = align.align_page(lines, detections, run=align.Run(name="test", accept=0.5, margin=0.0))
    holders = Counter((unit.box.x, unit.box.y, unit.box.w, unit.box.h) for line in lines
                      for unit in {unit.box.model_dump_json(): unit for unit in aligned[line.id][0]
                                   if unit.box is not None}.values())
    assert holders and max(holders.values()) == 1


def test_an_upper_line_does_not_stack_under_a_lower_line_of_the_next_column():
    # Boxes 230 px wide, columns 100 px apart: an upper line and the lower line one column to its right
    # overlap by more than half a box, yet stand in different columns.
    def wide(line_id, cx, y, h, n, seq):
        return vertical(line_id, cx - 115, y, 230, h, "一" * n, seq)

    upper, lower = tuple(range(20, 380, 50)), tuple(range(520, 880, 50))
    lines = [wide("L1", 1000, 500, 400, 8, 0), wide("U2", 900, 0, 400, 8, 1), wide("L2", 900, 500, 400, 8, 2),
             wide("U3", 800, 0, 400, 8, 3), wide("L3", 800, 500, 400, 8, 4), wide("T", 700, 0, 900, 16, 5)]
    boxes = (column(980, lower) + column(880, upper) + column(880, lower) + column(780, upper) + column(780, lower)
             + column(680, upper + lower))
    assert [[line.id for line in slot] for slot in line_assignment.slots_of(lines)] == [
        ["L1"], ["U2", "L2"], ["U3", "L3"], ["T"]]
    found = line_assignment.assign(lines, boxes).lines
    assert {line: {boxes[index].x for index in held} for line, held in found.items()} == {
        "L1": {980}, "U2": {880}, "L2": {880}, "U3": {780}, "L3": {780}, "T": {680}}
    assert all(boxes[index].y < 400 for index in found["U2"] + found["U3"])


def test_a_line_alone_in_its_tier_keeps_ink_off_its_box_centre():
    lone = vertical("lone", 875, 0, 250, 400, "一二三四五六七")
    boxes = column(1090, tuple(range(20, 370, 50)))
    assert line_assignment.assign([lone], boxes).lines["lone"] == list(range(7))
