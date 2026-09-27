"""Tests for finding a glossary's headword boxes. No model runs: boxes and labels are given."""

from __future__ import annotations

from glyph_atlas import glossary
from glyph_atlas.glossary import Entry, Glyph
from glyph_atlas.schema import Box

CIRCLE = "U+25CB"
PAGE = """;腐臭
:부-ᄎᆔ
:○구소구사이
;𬌟(牽)牛星
:견-우-셩
:○이누가이호시
;一⿰方⿱厶夫(族)
:일-족
:○이지조구
"""


def test_a_headword_is_read_as_its_printed_characters() -> None:
    assert glossary.glyphs("老人星") == [Glyph("老"), Glyph("人"), Glyph("星")]
    assert glossary.glyphs("𬌟(牽)牛星") == [Glyph("𬌟", "牽"), Glyph("牛"), Glyph("星")]
    assert glossary.glyphs("一⿰方⿱厶夫(族)") == [Glyph("一"), Glyph("⿰方⿱厶夫", "族")]
    assert glossary.glyphs("⿳下⺕心(急)風") == [Glyph("⿳下⺕心", "急"), Glyph("風")]
    assert not Glyph("⿰方⿱厶夫").encoded and Glyph("族").encoded


def test_a_page_text_gives_its_headwords_in_order() -> None:
    assert [[g.text for g in head] for head in glossary.headwords(PAGE)] == [
        ["腐", "臭"], ["𬌟", "牛", "星"], ["一", "⿰方⿱厶夫"]]


def column(x: int, *rows: tuple[str, int]) -> list[tuple[Box, str]]:
    """A column of boxes at `x`, top to bottom: `H` a headword character, `r` a reading, `O` a circle."""
    out, y = [], 0
    for kind, count in rows:
        for _ in range(count):
            if kind == "H":
                out.append((Box(x=x, y=y, w=100, h=90), "U+4E00"))
                y += 100
            elif kind == "O":
                out.append((Box(x=x + 20, y=y, w=60, h=60), CIRCLE))
                y += 70
            else:
                out.append((Box(x=x + 55, y=y, w=40, h=40), "other"))
                y += 50
    return out


def test_entries_are_the_headword_boxes_before_each_circle_right_to_left() -> None:
    right = column(1000, ("H", 2), ("r", 3), ("O", 1), ("r", 4), ("H", 1), ("r", 2), ("O", 1), ("r", 3))
    left = column(800, ("H", 3), ("r", 2), ("O", 1), ("r", 5))
    placed = right + left
    entries = glossary.page_entries([b for b, _ in placed], [label for _, label in placed])
    assert [(e.column, len(e.boxes)) for e in entries] == [(0, 2), (0, 1), (1, 3)]
    assert entries[0].boxes[0].y < entries[0].boxes[1].y


def test_a_small_box_read_as_a_circle_is_part_of_a_reading() -> None:
    placed = column(1000, ("H", 1), ("O", 1), ("r", 2))
    placed.insert(2, (Box(x=1050, y=300, w=20, h=20), CIRCLE))
    entries = glossary.page_entries([b for b, _ in placed], [label for _, label in placed])
    assert [len(e.boxes) for e in entries] == [1]


def test_a_column_without_a_circle_holds_no_entry() -> None:
    placed = column(1000, ("H", 1), ("O", 1)) + column(1300, ("H", 2), ("r", 3))
    entries = glossary.page_entries([b for b, _ in placed], [label for _, label in placed])
    assert [(e.column, len(e.boxes)) for e in entries] == [(1, 1)]


def entry(n: int) -> Entry:
    return Entry(0, tuple(Box(x=0, y=100 * i, w=100, h=90) for i in range(n)))


def test_a_page_is_used_only_when_its_entries_and_headwords_agree_in_number() -> None:
    heads = glossary.headwords(PAGE)
    kept, why = glossary.match([entry(2), entry(3)], heads)
    assert kept == [] and why == "2 entries found for 3 headwords"


def test_only_entries_with_as_many_boxes_as_printed_characters_are_kept() -> None:
    heads = glossary.headwords(PAGE)
    kept, why = glossary.match([entry(2), entry(2), entry(2)], heads)
    assert [(place, [g.text for g in head]) for place, _, head in kept] == [(0, ["腐", "臭"]), (2, ["一", "⿰方⿱厶夫"])]
    assert why == "2 of 3 entries kept"


def test_a_flat_headword_character_counts_by_its_width() -> None:
    wide = 100.0
    assert glossary.kind(Box(x=0, y=0, w=95, h=30), "U+4E09", wide) == "headword"  # 三
    assert glossary.kind(Box(x=0, y=0, w=45, h=45), "other", wide) == "reading"


def test_agreement_allows_the_equivalent_forms_and_says_when_it_cannot_judge() -> None:
    known = {"U+6765", "U+53F0", "U+25CB"}  # 来 台 ○
    assert glossary.agrees(Glyph("來"), ["U+6765"], known) is True
    assert glossary.agrees(Glyph("臺"), ["U+6765"], known) is False
    assert glossary.agrees(Glyph("臺"), ["U+53F0", "U+6765"], known) is True
    assert glossary.agrees(Glyph("鰥"), ["U+6765"], known) is None
    assert glossary.agrees(Glyph("⿰方⿱厶夫", "族"), ["U+6765"], known) is None


def test_markup_in_a_headword_line_is_not_text() -> None:
    assert glossary.glyphs("邉<!--自→白-->(邊)") == [Glyph("邉", "邊")]
    assert glossary.glyphs("别(別)𭈹<!--⿰⿱口了𠂰-->(號)") == [Glyph("别", "別"), Glyph("𭈹", "號")]
    assert glossary.glyphs("(牛") == [Glyph("牛")] and glossary.glyphs("星)") == [Glyph("星")]


def test_an_unencoded_variant_or_unread_character_holds_its_place_without_a_name() -> None:
    (variant,) = glossary.glyphs("{{이체자|脊}}")
    assert variant.standard == "脊" and not variant.readable and not variant.encoded
    unread, *rest = glossary.glyphs("？鼻涕")
    assert not unread.readable and [g.text for g in rest] == ["鼻", "涕"]
    assert glossary.agrees(unread, ["U+9F3B"], {"U+9F3B"}) is None


def test_a_section_heading_is_not_a_headword() -> None:
    text = ';<section begin="公式" /><h2>公式</h2>\n;天\n:하ᄂᆞᆯ텬\n'
    assert [[g.text for g in head] for head in glossary.headwords(text)] == [["天"]]
