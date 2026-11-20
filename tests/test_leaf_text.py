"""Tests for placing a printed half-leaf's headwords by the text. No model runs: boxes and verdicts are given."""

from __future__ import annotations

import math

from PIL import Image, ImageDraw

from glyph_atlas import leaf_text
from glyph_atlas.leaf_text import Head
from glyph_atlas.schema import Box

HEAD, ROW, PITCH, TOP = 68, 40, 82, 300


def column(x: int, heads: list[Head], offset: int, skip: frozenset[int] = frozenset()) -> list[Box]:
    """A column as the detector sees it: a box on each headword but those in `skip`, and its gloss in small boxes."""
    boxes, y = [], offset
    for index, head in enumerate(heads):
        if index not in skip:
            boxes.append(Box(x=x - 30, y=y, w=60, h=60))
        rows = math.ceil(head.gloss / 2)
        for row in range(rows):
            for side in (-1, 1):
                boxes.append(Box(x=x + side * 19 - 17, y=y + HEAD + row * ROW, w=34, h=34))
        y += HEAD + rows * ROW
    return boxes


def page(columns: dict[int, list[Head]], offsets: dict[int, int], skip: dict[int, frozenset[int]] | None = None) -> list[Box]:
    """A half-leaf of ten columns, column 1 on the right."""
    boxes = []
    for line in range(1, 11):
        heads = columns.get(line, [])
        boxes += column(1000 - (line - 1) * PITCH, heads, offsets.get(line, TOP), (skip or {}).get(line, frozenset()))
    return boxes


TEXT = {line: [Head(chr(0x4E00 + 16 * line + k), 2 + (line + 3 * k) % 9) for k in range(5)] for line in range(1, 11)}
STARTS = {line: TOP + 40 * (line % 4) for line in range(1, 11)}


def test_tops_follow_the_gloss_lengths() -> None:
    heads = [Head("東", 0), Head("菄", 3), Head("鶇", 8)]
    assert list(leaf_text.tops(heads, 68.0, 40.0)) == [0.0, 68.0, 68.0 + 68 + 2 * 40]


def test_columns_are_found_from_the_headword_boxes() -> None:
    boxes = page(TEXT, STARTS)
    unit = leaf_text.side(boxes)
    found = leaf_text.axes(leaf_text.big(boxes, unit), unit)
    assert [round(x) for x in found] == [1000 - k * PITCH for k in range(9, -1, -1)]
    assert leaf_text.complete(found, 10) == found[::-1]


def test_a_column_of_gloss_alone_is_put_in_where_the_gap_is_double() -> None:
    found = [float(1000 - k * PITCH) for k in range(9, -1, -1) if k != 4]
    whole = leaf_text.complete(found, 10)
    assert whole is not None and [round(x) for x in whole] == [1000 - k * PITCH for k in range(10)]
    assert leaf_text.complete(found[:3], 10) is None


def test_a_photographed_opening_divides_at_its_widest_middle_gap() -> None:
    left = [100.0 + k * PITCH for k in range(10)]
    right = [1100.0 + k * PITCH for k in range(10)]
    assert leaf_text.gutter(left + right, 2000, 10) == (left[-1] + right[0]) / 2
    assert leaf_text.gutter(left[:1], 2000, 10) is None


def test_a_flat_scan_divides_in_the_middle_of_its_twenty_columns() -> None:
    columns = [100.0 + k * PITCH for k in range(20)]
    del columns[6]  # a column of gloss alone leaves a double gap that is not the gutter
    assert leaf_text.gutter(columns, 2000, 10) == 100.0 + 9.5 * PITCH


def test_a_flat_scan_gives_its_columns_from_the_middle_outward() -> None:
    columns = [100.0 + k * PITCH for k in range(20)]
    del columns[6]
    middle, pitch = leaf_text.flat([30.0, *columns], 10, 1840)  # with a stray note in the margin
    assert abs(middle - (100 + 9.5 * PITCH)) < 3 and abs(pitch - PITCH) < 0.5
    right = leaf_text.ruled(100 + 9.5 * PITCH, PITCH, 10, right=True)
    left = leaf_text.ruled(100 + 9.5 * PITCH, PITCH, 10, right=False)
    assert [round(x) for x in right] == [100 + k * PITCH for k in range(19, 9, -1)]
    assert [round(x) for x in left] == [100 + k * PITCH for k in range(9, -1, -1)]
    assert leaf_text.flat(columns[:8], 10, 1840) is None


def test_every_headword_is_placed_on_its_box() -> None:
    placed = leaf_text.place(page(TEXT, STARTS), TEXT, 10)
    assert placed is not None and sorted(placed) == list(range(1, 11))
    for line, (cells, error) in placed.items():
        assert [cell.head for cell in cells] == TEXT[line]
        assert all(cell.detected for cell in cells)
        assert [cell.box.y for cell in cells] == [STARTS[line] + int(t) for t in leaf_text.tops(TEXT[line], HEAD, ROW)]
        assert {cell.box.x + cell.box.w // 2 for cell in cells} == {1000 - (line - 1) * PITCH}
        assert error < 0.1


def test_a_headword_the_detector_missed_takes_the_predicted_cell() -> None:
    placed = leaf_text.place(page(TEXT, STARTS, {3: frozenset({2})}), TEXT, 10)
    assert placed is not None
    cells, _ = placed[3]
    assert [cell.detected for cell in cells] == [True, True, False, True, True]
    expected = STARTS[3] + leaf_text.tops(TEXT[3], HEAD, ROW)[2]
    assert abs(cells[2].box.y - expected) < 0.2 * HEAD


def test_a_refusing_reader_moves_a_pairing_off_a_box() -> None:
    heads = TEXT[1]
    boxes = column(1000, heads, TOP)
    found = leaf_text.candidates(boxes, 1000.0, PITCH, 60.0)
    sizes = leaf_text.Fit(float(HEAD), float(ROW), {1: float(TOP)}, 0.0)
    refused = found[1]
    cells, _ = leaf_text.align(heads, found, 1000.0, PITCH, 60.0, sizes, float(TOP),
                               reads=lambda char, box: False if box == refused else None)
    assert [cell.detected for cell in cells] == [True, False, True, True, True]
    assert [cell.box for cell in cells if cell.detected] == [found[0], found[2], found[3], found[4]]


def test_a_reader_settles_which_of_two_headwords_a_lone_box_is() -> None:
    heads = [Head("甲", 4), Head("乙", 4)]
    box = Box(x=970, y=TOP, w=60, h=60)
    sizes = leaf_text.Fit(float(HEAD), float(ROW), {1: float(TOP)}, 0.0)
    cells, error = leaf_text.align(heads, [box], 1000.0, PITCH, 60.0, sizes, float(TOP), reads=lambda char, _: char == "乙")
    assert [cell.detected for cell in cells] == [False, True] and cells[1].box == box
    assert error == float("inf") and cells[1].error == float("inf")


def test_a_lone_headword_a_reader_refuses_goes_without_its_box() -> None:
    box = Box(x=970, y=TOP, w=60, h=60)
    sizes = leaf_text.Fit(float(HEAD), float(ROW), {1: float(TOP)}, 0.0)
    cells, _ = leaf_text.align([Head("甲", 4)], [box], 1000.0, PITCH, 60.0, sizes, float(TOP), reads=lambda *_: False)
    assert [cell.detected for cell in cells] == [False]


def test_a_cell_carries_the_error_of_its_own_gaps() -> None:
    heads = TEXT[2]
    found = leaf_text.candidates(column(1000, heads, TOP), 1000.0, PITCH, 60.0)
    found[-1] = Box(x=found[-1].x, y=found[-1].y + ROW, w=60, h=60)  # the last headword a row low
    sizes = leaf_text.Fit(float(HEAD), float(ROW), {1: float(TOP)}, 0.0)
    cells, mean = leaf_text.align(heads, found, 1000.0, PITCH, 60.0, sizes, float(TOP))
    assert [round(cell.error, 2) for cell in cells] == [0.0, 0.0, 0.0, 1.0, 1.0]
    assert round(mean, 2) == 0.25


def test_two_columns_of_gloss_side_by_side_are_both_put_in() -> None:
    found = [float(1000 - k * PITCH) for k in range(9, -1, -1) if k not in (4, 5)]
    whole = leaf_text.complete(found, 10)
    assert whole is not None and [round(x) for x in whole] == [1000 - k * PITCH for k in range(10)]


def test_thin_writing_as_wide_as_a_headword_is_dropped() -> None:
    image = Image.new("RGB", (200, 100), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle([10, 10, 70, 70], outline="black", width=8)  # a bold headword
    for x in (110, 150):  # two gloss characters side by side, in hairlines
        draw.rectangle([x, 20, x + 26, 46], outline="black", width=1)
    headword, pair = Box(x=5, y=5, w=70, h=70), Box(x=105, y=15, w=76, h=36)
    assert leaf_text.bold(image, [headword, pair], 40.0) == [headword]
