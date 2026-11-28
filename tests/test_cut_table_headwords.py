"""Tests for how the dictionary cutter keeps its half-leaves and boxes, and for 龍龕手鏡's rules of doubt."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from glyph_atlas import longkan
from glyph_atlas.schema import Box


@pytest.fixture(scope="module")
def cut():
    spec = importlib.util.spec_from_file_location("cut_table_headwords", Path(__file__).parents[1] / "scripts" / "cut_table_headwords.py")
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
                 entry(12, "惘", "①〔Kimi〕字頭原空，據參考本《龍龕手鏡》補「惘」。"), entry(15, "鏒", "①〔Kimi〕「令」為孤立"),
                 entry(18, "鈞", "①〔Kimi〕「决」當作「決」，據參考本該字頭引文正作「決」改。②〔Kimi〕注文疑誤，待核。")]}
    assert longkan.unsettled(pages) == {(2, 0), (3, 0), (6, 0), (6, 1), (12, 0)}, "a repeat within two rows or one field, and a filled headword"


def test_a_headword_without_its_sections_radical_is_named():
    def entry(row, field, section):
        return longkan.Entry(row, field, "", "1", section, "去聲", 1)

    rows = [(1, "呻"), (5, "唷"), (9, "聴"), (13, "彊𠼢"), (17, "咩"), (21, "哞")]
    grass = [(40, "荃"), (44, "荷"), (48, "葉"), (52, "姜")]
    pages = {1: [entry(row, field, "口") for row, field in rows] + [entry(30, "菕", "雜"), entry(34, "坳", "雜")] + [entry(row, field, "草") for row, field in grass]}
    assert longkan.unsettled(pages) == {(9, 0), (13, 0), (52, 0)}, "聴 and 彊 hold no 口; 雜 names no radical; 草 is written 艹, which 姜 lacks"


def test_a_neighbour_alone_can_be_left_to_the_run_test():
    def entry(row, field, note=""):
        return longkan.Entry(row, field, "", "1", "車", "去聲", 1, note)

    pages = {1: [entry(2, "𨏥"), entry(3, "𨏥"), entry(6, "菕菕"), entry(12, "惘", "①〔Kimi〕字頭原空，據參考本《龍龕手鏡》補「惘」。")]}
    assert longkan.unsettled(pages, neighbours=False) == {(12, 0)}, "only the filled headword stays named"


def test_a_run_of_one_character_joins_headwords_within_two_rows_of_the_last():
    def entry(row, field):
        return longkan.Entry(row, field, "", "1", "金", "入聲", 1)

    pages = {1: [entry(3, "鉀"), entry(5, "鉀"), entry(7, "鉀")], 2: [entry(10, "鉀"), entry(20, "鐵鐵"), entry(30, "鈞")]}
    kin = longkan.kin(pages)
    assert kin[(3, 0)] == {(5, 0), (7, 0)} and kin[(7, 0)] == {(3, 0), (5, 0)}, "a chain is one run"
    assert (10, 0) not in kin, "three rows after the last is another run, here of one"
    assert kin[(20, 0)] == {(20, 1)}, "one field written twice is a run"
    assert (30, 0) not in kin


def test_the_crop_that_alone_looks_like_its_character_leads_its_run():
    kin = {(1, 0): {(2, 0), (3, 0)}, (2, 0): {(1, 0), (3, 0)}, (3, 0): {(1, 0), (2, 0)}}
    alike = {(1, 0): 0.9, (2, 0): 0.7, (3, 0): 0.6}
    assert longkan.leads((1, 0), kin, alike, 0.7, 0.15)
    assert not longkan.leads((2, 0), kin, alike, 0.7, 0.15), "behind another crop of its run"
    assert not longkan.leads((1, 0), kin, {(1, 0): 0.9, (2, 0): 0.8}, 0.7, 0.15), "ahead by less than the lead"
    assert not longkan.leads((1, 0), kin, {(1, 0): 0.68, (3, 0): 0.4}, 0.7, 0.15), "too little like its character"
    assert not longkan.leads((1, 0), kin, {(1, 0): 0.95}, 0.7, 0.15), "no other crop of its run to set it against"
    assert not longkan.leads((4, 0), kin, {(4, 0): 0.95}, 0.7, 0.15), "no run"
