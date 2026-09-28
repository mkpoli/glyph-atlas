"""Tests of `scripts/build_han_components.py`, and of the component search over the tables it writes.

The readers are tested against a few synthetic lines in each upstream's format; the search is tested on
the committed tables, since what it answers depends on the real sequences.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from glyph_atlas import han_components

ROOT = Path(__file__).resolve().parents[1]


def _module():
    spec = importlib.util.spec_from_file_location(
        "build_han_components", ROOT / "scripts" / "build_han_components.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["build_han_components"] = module
    spec.loader.exec_module(module)
    return module


build = _module()


def test_ids_rows_keep_every_sequence_with_its_regions_and_leave_out_notes():
    text = (
        "﻿# File Date: 2025-06-27\r\n"
        "U+4E8C\t二\t^⿱一一$(GHTJKPV)\t*U+4E8C≠U+2011E\r\n"
        "U+9AA8\t骨\t^⿳{108}冖⺝$(GV[U][B])\t^⿳𭁟冖⺼$(T)\r\n"
    )
    assert build.ids_rows(text) == [
        ("U+4E8C", "二", "⿱一一(GHTJKPV)"),
        ("U+9AA8", "骨", "⿳{108}冖⺝(GV[U][B]) ⿳𭁟冖⺼(T)"),
    ]
    assert build.file_date(text) == "2025-06-27"


def test_a_radical_variant_that_is_itself_a_radical_keeps_its_identity():
    radical = build.radical_forms(
        "# note\n水,cjkvi/radical-variant,氵,left\n肉,cjkvi/radical-variant,月,left\n"
        "金,cjkvi/radical-variant-simplified,钅,left\n行,cjkvi/radical-split,⿲彳?亍\n"
    )
    unified = build.unified_forms("2EA1       ; 6C35  #     CJK RADICAL WATER ONE\n")
    forms = {row[0]: row[1] for row in build.component_forms(unified, radical)}
    assert forms["氵"] == "水" and forms["钅"] == "金" and forms["⺡"] == "氵" and forms["⽔"] == "水"
    assert "月" not in forms, "月 is the moon radical as well as the left-hand form of 肉"
    assert "彳" not in forms


def test_a_character_is_found_by_its_parts_under_any_name_they_go_by():
    for term in ("水骨", "氵骨", "⺡骨", "⽔骨", "氵冖月"):
        assert han_components.search(term)[0] == "滑", term


def test_components_are_counted():
    assert "林" in han_components.search("木木") and "本" not in han_components.search("木木")
    assert han_components.search("木木木")[0] == "森"
    assert han_components.search("口口口")[0] == "品"


def test_parts_named_at_the_top_level_come_first():
    assert han_components.search("日月")[0] == "明"
    assert han_components.search("言吾")[:2] == ["语", "語"]


def test_a_component_written_as_another_character_does_not_become_it():
    assert "明" not in han_components.search("肉日"), "月 in 明 is the moon, not 肉"


def test_a_character_is_not_its_own_component():
    """水's sequence leads back to 水 through 氺."""
    assert "水" not in han_components.components("水")


def test_only_two_or_more_ideographs_are_a_component_search():
    assert han_components.query("骨") is None
    assert han_components.query("とも") is None
    assert han_components.query("水骨ab") is None
    assert han_components.query("一二三四五六七八九") is None, "longer than PARTS"
    assert han_components.search("骨") == []


def test_a_subtracted_component_is_not_in_the_character():
    """乌 is ㇯鸟丶: 鸟 without its dot."""
    assert han_components.components("乌")["丶"] == 0
    assert "乌" not in han_components.search("鸟丶")


def test_two_regional_forms_of_one_component_count_once():
    """礼 is ⿰礻乚 in some regions and ⿰示乚 in others; either way it holds one 示."""
    assert han_components.components("礼")["示"] == 1
    assert "礼" not in han_components.search("示示")
