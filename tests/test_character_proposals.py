"""Cutting a detector box that holds two stacked characters: the page is drawn as a column grid."""

from __future__ import annotations

import pytest
from PIL import Image, ImageDraw

# Splitting reads the page with OpenCV, which only the `ocr` and `marks` extras install.
pytest.importorskip("cv2")

from glyph_atlas.character_proposals import Proposal, proposer, split_boxes
from glyph_atlas.schema import Box

COLUMNS = (100, 200, 300, 400)
ROWS = 10
TOP = 100
PITCH = 60
SIDE = 40


def cell(column: int, row: int) -> Box:
    return Box(x=COLUMNS[column], y=TOP + row * PITCH, w=SIDE, h=SIDE)


def page(cells) -> Image.Image:
    """A page of paper with a character drawn in each of `cells`."""
    image = Image.new("RGB", (560, 900), (225, 215, 190))
    draw = ImageDraw.Draw(image)
    for box in cells:
        draw.rectangle([box.x, box.y + 4, box.x + box.w, box.y + 10], fill=(40, 40, 40))
        draw.rectangle([box.x, box.y + box.h - 10, box.x + box.w, box.y + box.h - 4], fill=(40, 40, 40))
        draw.rectangle([box.x + box.w // 2 - 3, box.y, box.x + box.w // 2 + 3, box.y + box.h], fill=(40, 40, 40))
    return image


def proposals(boxes) -> list[Proposal]:
    return [Proposal(x=box.x, y=box.y, w=box.w, h=box.h, score=0.5) for box in boxes]


def test_a_box_over_two_stacked_characters_is_cut_at_the_gap() -> None:
    grid = [cell(column, row) for column in range(len(COLUMNS)) for row in range(ROWS)]
    merged = Box(x=COLUMNS[1], y=TOP + 3 * PITCH, w=SIDE, h=2 * PITCH)
    boxes = [box for box in grid if not (box.x == COLUMNS[1] and round((box.y - TOP) / PITCH) in (3, 4))]
    boxes.append(merged)
    out = split_boxes(page(grid), proposals(boxes))
    assert len(out) == len(boxes) + 1
    parts = sorted((p for p in out if p.x == COLUMNS[1] and merged.y <= p.y < merged.y + merged.h),
                   key=lambda p: p.y)
    assert len(parts) == 2
    assert parts[0].y == merged.y and parts[1].y + parts[1].h == merged.y + merged.h
    # The cut lands in the paper between the two characters, not through either of them.
    assert merged.y + SIDE <= parts[1].y <= merged.y + PITCH


def test_boxes_that_stand_on_one_character_are_left_alone() -> None:
    grid = [cell(column, row) for column in range(len(COLUMNS)) for row in range(ROWS)]
    original = proposals(grid)
    out = split_boxes(page(grid), original)
    assert out == original


def test_boxes_off_the_columns_are_left_alone() -> None:
    grid = [cell(column, row) for column in range(len(COLUMNS)) for row in range(ROWS)]
    lone = Proposal(x=480, y=120, w=SIDE, h=2 * PITCH, score=0.5)
    out = split_boxes(page(grid), [*proposals(grid), lone])
    assert out[-1] == lone


def test_the_split_is_part_of_the_proposer_version(tmp_path) -> None:
    model = tmp_path / "model.onnx"
    model.write_bytes(b"detector")
    assert proposer(model) == proposer(model, split=False)
    assert proposer(model, split=True) != proposer(model)


def test_a_box_at_the_top_edge_is_cut_without_leaving_the_image() -> None:
    grid = [Box(x=COLUMNS[column], y=row * PITCH, w=SIDE, h=SIDE)
            for column in range(len(COLUMNS)) for row in range(ROWS)]
    merged = Box(x=COLUMNS[1], y=0, w=SIDE, h=2 * PITCH)
    boxes = [box for box in grid if not (box.x == COLUMNS[1] and box.y in (0, PITCH))] + [merged]
    out = split_boxes(page(grid), proposals(boxes))
    parts = sorted((p for p in out if p.x == COLUMNS[1] and p.y < 2 * PITCH), key=lambda p: p.y)
    assert len(parts) == 2
    assert parts[0].y == 0 and parts[1].y >= SIDE


def test_a_box_inside_a_character_box_is_dropped() -> None:
    grid = [cell(column, row) for column in range(len(COLUMNS)) for row in range(ROWS)]
    inner = Box(x=COLUMNS[0] + 8, y=TOP + 2 * PITCH + 8, w=12, h=12)
    out = split_boxes(page(grid), proposals([*grid, inner]))
    assert len(out) == len(grid)
    assert not any((p.x, p.y, p.w, p.h) == (inner.x, inner.y, inner.w, inner.h) for p in out)


def test_two_boxes_over_one_character_are_joined() -> None:
    grid = [cell(column, row) for column in range(len(COLUMNS)) for row in range(ROWS)]
    split = [Box(x=COLUMNS[2], y=TOP + 5 * PITCH, w=SIDE, h=SIDE // 2),
             Box(x=COLUMNS[2], y=TOP + 5 * PITCH + SIDE // 2, w=SIDE, h=SIDE // 2)]
    boxes = [box for box in grid if not (box.x == COLUMNS[2] and box.y == TOP + 5 * PITCH)] + split
    out = split_boxes(page(grid), proposals(boxes))
    parts = [p for p in out if p.x == COLUMNS[2] and TOP + 4 * PITCH < p.y < TOP + 6 * PITCH]
    assert len(parts) == 1
    assert parts[0].h >= SIDE - 1


def test_boxes_chained_at_one_height_do_not_break_the_pitch() -> None:
    # Six boxes on one row, each within the chain's reach of the next: every step of that chain is
    # zero, so the column has no pitch to take a cut from.
    grid = [cell(column, row) for column in range(len(COLUMNS)) for row in range(ROWS)]
    row = [Box(x=COLUMNS[2] + n * 10, y=TOP + 4 * PITCH, w=SIDE, h=SIDE) for n in range(6)]
    out = split_boxes(page(grid), proposals([*grid, *row]))
    kept = {(p.x, p.y, p.w, p.h) for p in out}
    # The first box of `row` sits where the grid's own box does; the others must survive.
    assert all((box.x, box.y, box.w, box.h) in kept for box in row[1:])


def test_the_pass_can_run_again_on_its_own_output() -> None:
    grid = [cell(column, row) for column in range(len(COLUMNS)) for row in range(ROWS)]
    merged = Box(x=COLUMNS[1], y=TOP + 3 * PITCH, w=SIDE, h=2 * PITCH)
    boxes = [box for box in grid if not (box.x == COLUMNS[1] and round((box.y - TOP) / PITCH) in (3, 4))]
    boxes.append(merged)
    first = split_boxes(page(grid), proposals(boxes))
    second = split_boxes(page(grid), first)
    assert len(second) >= len(first)
