"""Tests for how the 龍龕手鏡 cutter keeps its half-leaves and boxes."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from glyph_atlas.schema import Box


@pytest.fixture(scope="module")
def cut():
    spec = importlib.util.spec_from_file_location("cut_longkan_headwords", Path(__file__).parents[1] / "scripts" / "cut_longkan_headwords.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_box_keeps_every_pixel_it_touches(cut):
    assert cut.native(Box(x=100, y=60, w=101, h=80), 2) == Box(x=50, y=30, w=51, h=40)
    assert cut.native(Box(x=101, y=61, w=2, h=2), 2) == Box(x=50, y=30, w=2, h=2)


def test_the_half_leaves_kept_run_in_the_books_order(cut):
    found = [(0, 9, True), (1, 10, True), (2, 40, True), (3, 11, True), (4, 12, False), (5, 13, True)]
    assert cut.ordered(found) == {0, 1, 3, 5}, "a page far out of order is left out, and one not borne out is none"
    assert cut.ordered([]) == set()
    assert cut.ordered([(0, 9, True), (1, 9, True)]) in ({0}, {1}), "two half-leaves do not show one table page"
