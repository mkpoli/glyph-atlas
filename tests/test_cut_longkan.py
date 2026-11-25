"""Tests for how the 龍龕手鏡 cutter keeps its half-leaves and boxes."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from glyph_atlas import longkan
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


def test_a_headword_the_table_makes_doubtful_is_named():
    def entry(row, field, note=""):
        return longkan.Entry(row, field, "", "1", "車", "去聲", 1, note)

    pages = {1: [entry(1, "轎"), entry(2, "𨏥"), entry(3, "𨏥"), entry(5, "軌"), entry(6, "菕菕"), entry(9, "軌"),
                 entry(12, "惘", "①〔Kimi〕字頭原空，據參考本《龍龕手鏡》補「惘」。"), entry(15, "鏒", "①〔Kimi〕「令」為孤立")]}
    assert longkan.unsettled(pages) == {(2, 0), (3, 0), (6, 0), (6, 1), (12, 0)}, "a repeat within two rows or one field, and a filled headword"


def test_a_headword_without_its_sections_radical_is_named():
    def entry(row, field, section):
        return longkan.Entry(row, field, "", "1", section, "去聲", 1)

    rows = [(1, "呻"), (5, "唷"), (9, "聴"), (13, "彊𠼢"), (17, "咩"), (21, "哞")]
    pages = {1: [entry(row, field, "口") for row, field in rows] + [entry(30, "菕", "雜"), entry(34, "坳", "雜"), entry(38, "荃", "草")]}
    assert longkan.unsettled(pages) == {(9, 0), (13, 0)}, "聴 and 彊 hold no 口; 雜 names no radical; 草 is written 艹"
