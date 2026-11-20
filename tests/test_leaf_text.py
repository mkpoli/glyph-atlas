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


def test_the_gutter_is_the_widest_gap_in_the_middle_of_the_frame() -> None:
    left = [100.0 + k * PITCH for k in range(10)]
    right = [1100.0 + k * PITCH for k in range(10)]
    assert leaf_text.gutter(left + right, 2000) == (left[-1] + right[0]) / 2
    assert leaf_text.gutter(left[:2], 2000) is None


def test_every_headword_is_placed_on_its_box() -> None:
    placed = leaf_text.place(page(TEXT, STARTS), TEXT, 10)
    assert placed is not None and sorted(placed) == list(range(1, 11))
    for line, (cells, error) in placed.items():
        assert [cell.head for cell in cells] == TEXT[line]
        assert all(cell.detected for cell in cells)
        assert [cell.box.y for cell in cells] == [STARTS[line] + int(t) for t in leaf_text.tops(TEXT[line], HEAD, ROW)]
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
    assert all(cell.box != refused for cell in cells if cell.detected)


def test_thin_writing_as_wide_as_a_headword_is_dropped() -> None:
    image = Image.new("RGB", (200, 100), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle([10, 10, 70, 70], outline="black", width=8)  # a bold headword
    for x in (110, 150):  # two gloss characters side by side, in hairlines
        draw.rectangle([x, 20, x + 26, 46], outline="black", width=1)
    headword, pair = Box(x=5, y=5, w=70, h=70), Box(x=105, y=15, w=76, h=36)
    assert leaf_text.bold(image, [headword, pair], 40.0) == [headword]
