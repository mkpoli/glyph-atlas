"""Tests for `scripts/ainu_columns.py`, the measurement of the Ainu line-box derivation.

The rule under test groups a page's detected characters into columns: characters that step along one
line by less than half the median character width belong to one column, and two runs whose centres are
closer than 0.9 of that width are one column, because a line can pause without ending. A page's text
block is separated from cataloguing marks on the other leaf by a gap of a share of the page width.

Everything here is synthetic boxes: no detector, no page image, no network.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from kuzushiji_atlas.schema import Box

ROOT = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location("ainu_columns", ROOT / "scripts" / "ainu_columns.py")
assert _spec is not None and _spec.loader is not None
ainu_columns = importlib.util.module_from_spec(_spec)
sys.modules["ainu_columns"] = ainu_columns
_spec.loader.exec_module(ainu_columns)


def column(x: int, ys: tuple[int, ...] = (100, 140, 180), w: int = 20, h: int = 30) -> list[Box]:
    """One vertical line of three characters, centred on `x`."""
    return [Box(x=x - w // 2, y=y, w=w, h=h) for y in ys]


def test_three_columns_come_back_right_to_left() -> None:
    """A vertical Japanese page is read from the right, so the first column is the rightmost."""
    boxes = column(100) + column(150) + column(200)
    grouped = ainu_columns.columns_of(boxes)
    assert len(grouped) == 3
    centres = [sum(boxes[index].x + boxes[index].w / 2 for index in group) / len(group) for group in grouped]
    assert centres == [200.0, 150.0, 100.0]
    assert all(len(group) == 3 for group in grouped)


def test_the_step_and_the_merge_are_two_different_thresholds() -> None:
    """A step inside a line is cut by the gap threshold and put back by the merge threshold.

    Characters are 30 px wide, so the gap threshold is 15 px and the merge threshold 27 px. A step of
    20 px is therefore cut and put back, which is the case the two rules exist for; a step of 55 px is
    cut and stays cut, which is the next line.
    """
    drifted = [Box(x=100, y=100, w=30, h=40), Box(x=100, y=160, w=30, h=40),
               Box(x=120, y=220, w=30, h=40)]
    assert len(ainu_columns.columns_of(drifted)) == 1, "the 20 px step is put back by the merge"
    assert len(ainu_columns.columns_of(drifted, merge_ratio=0.5)) == 2, "a 15 px merge leaves the cut"

    pause = [Box(x=100, y=100, w=30, h=40), Box(x=155, y=160, w=30, h=40)]
    assert len(ainu_columns.columns_of(pause)) == 2, "a 55 px step is over the merge threshold"
    assert len(ainu_columns.columns_of(pause, merge_ratio=2.0)) == 1, "a 60 px merge joins it"


def test_a_gap_wider_than_the_step_ends_the_column() -> None:
    """Two lines of a page are more than a step apart, which is what makes them two columns."""
    boxes = column(100) + column(200)
    assert len(ainu_columns.columns_of(boxes)) == 2
    assert len(ainu_columns.columns_of(boxes, gap_ratio=5.0, merge_ratio=1.5)) == 1, "a wider step joins them"


def test_a_mark_beside_a_line_belongs_to_it() -> None:
    """A small mark a hand put beside a line is not a line of its own."""
    boxes = column(100) + [Box(x=112, y=300, w=8, h=8)]
    grouped = ainu_columns.columns_of(boxes)
    assert len(grouped) == 1 and len(grouped[0]) == 4
    assert len(ainu_columns.columns_of(boxes, merge_ratio=0.1)) == 2, "the mark is a detection of its own"


def test_regions_split_a_spread_at_a_wide_gap() -> None:
    """A text block on one leaf and cataloguing marks on the other are counted apart."""
    boxes = column(100) + column(150) + [Box(x=900, y=80, w=20, h=20)]
    regions = ainu_columns.regions_of(boxes, 0.5, 0.05)
    assert len(regions) == 2
    assert [len(ainu_columns.columns_of(region)) for region in regions] == [1, 2], "right leaf first"


def test_regions_leave_one_text_block_whole() -> None:
    """No gap inside a line reaches the region cut, so a dense page stays one region."""
    boxes = column(100) + column(150) + column(200) + column(250)
    regions = ainu_columns.regions_of(boxes, 0.5, 0.05)
    assert len(regions) == 1 and len(ainu_columns.columns_of(regions[0])) == 4


def test_empty_input_is_not_an_error() -> None:
    """A page with no detection has no column, which the census records rather than crashes on."""
    assert ainu_columns.columns_of([]) == []
    assert ainu_columns.regions_of([], 0.5, 0.05) == []
    assert ainu_columns.histogram([]) == "(no detections)"


def test_histogram_marks_where_the_ink_is() -> None:
    """The profile is for eyeballing a grouping: a line at x=150 shows right of one at x=100."""
    boxes = column(100) + column(150) + [Box(x=600, y=100, w=20, h=30)]
    profile = ainu_columns.histogram(boxes, width=40)
    assert len(profile) == 40
    used = [index for index, mark in enumerate(profile) if mark != " "]
    assert used == sorted(used), "the profile reads left to right"
    assert used[0] < 20 <= used[-1], "the text block sits left of the mark at x=600"
    assert used[-1] > 35, "the mark is at the right edge of the profile"
