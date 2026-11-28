"""Tests for reading 新修玉篇's table into printed headwords, and for the sources the dictionary cutter reads."""

from __future__ import annotations

import csv
import importlib
from pathlib import Path

import pytest
import yaml

from glyph_atlas import date_claims, longkan, yupian
from glyph_atlas.corpus.index import UNIT_CORPORA
from glyph_atlas.corpus.sources import ID_FAMILIES, KNOWN, UNIT_ID_PREFIXES
from glyph_atlas.registry import SOURCES

HEADER = ["校对状态", "字头", "异体", "注文", "笔画（推导）", "来源（不准）", "部首", "部首序", "部首说明", "卷序", "篇", "金刻本", "校勘记",
          "行序", "行序置信度", "行内字序", "行内字序置信度", "推导页内字序", "索引", "索引置信度"]


def table(tmp_path: Path, rows: list[dict[str, str]]) -> Path:
    path = tmp_path / "新修玉篇.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, HEADER)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in HEADER})
    return path


def row(field: str, gloss: str, place: str, page: str = "97", section: str = "人", strokes: str = "〔無標記〕", note: str = "") -> dict[str, str]:
    return {"字头": field, "注文": gloss, "推导页内字序": place, "金刻本": page, "部首": section, "卷序": "3", "笔画（推导）": strokes, "校勘记": note}


def test_the_readings_of_one_printed_headword_are_one_entry(tmp_path):
    pages = yupian.read(table(tmp_path, [
        row("", "舊五十六字，新增三十五字。", ""),
        row("佑", "于救切。助也。", "1", note="〔Kimi〕字頭見於他本。"),
        row("佑", "《韻》又云乆切。", "1", note="〔Kimi〕注文疑誤，待核。"),
        row("位", "于愧切。", "2"),
    ]))
    note, you, wei = pages[97]
    assert note.tone == "note" and note.field == "", "a section's opening note is printed before its headwords"
    assert (you.row, you.field, you.gloss) == (2, "佑", "于救切。助也。《韻》又云乆切。"), "an entry keeps its first row and every reading's gloss"
    assert (wei.row, wei.field) == (4, "位")
    assert not longkan.unsettled({97: [you, wei]}, neighbours=False), \
        "one reading's note names the headword and another's doubts its gloss: neither doubts the headword"


@pytest.mark.parametrize(("field", "printed"), [
    ("歲勾，歲欠", (None, None)),
    ("如神，𩴿，如犭中", (None, "𩴿", None)),
    ("〖𠘲〗", (None,)),
    ("㜐（㛸）", (None,)),
    ("[=此处版面残缺=]", (None,)),
    ("十一", (None,)),
    ("𠆬孑仔", None),
])
def test_a_field_names_the_headwords_printed(field, printed):
    """Headwords written as their parts and set apart by commas are printed one each and left unlabelled."""
    assert yupian.heads(field) == printed


def test_a_page_prints_its_titles_notes_and_stroke_counts(tmp_path):
    pages = yupian.read(table(tmp_path, [
        row("仁", "如鄰切。", "1", strokes="二"),
        row("仍", "如乘切。", "2", strokes=""),
        row("佃", "堂練切。", "3", strokes="二"),
        row("仗", "直兩切。", "4", strokes="三"),
        row("", "凡三字。", ""),
        row("", "舊十字。", "", section="勹"),
        row("勹", "布交切。", "5", section="勹"),
        row("𠆬孑仔", "並居列切。", "6", section="勹"),
        row("匀", "羊倫切。", "1", page="98", section="匀", strokes="四"),
    ]))
    slots = yupian.paged(pages)
    assert [(s.char, s.gloss) for s in slots[97]] == [
        (None, yupian.HEADER), ("仁", 3), ("仍", 3), ("佃", 3), (None, yupian.HEADER), ("仗", 3),
        (None, 3), (None, yupian.TITLE + 3), ("勹", 3), ("𠆬", 0), ("孑", 0), ("仔", 4)], \
        "a row with no stroke count changes none; a group's count is text, a new section's note follows its title"
    assert [(s.char, s.gloss) for s in slots[98]] == [(None, yupian.TITLE), (None, yupian.HEADER), ("匀", 3)], \
        "a section that opens a page has its title, and its stroke count its cartouche"


def test_neighbours_are_counted_in_printed_headwords(tmp_path):
    readings = [row("化", "呼霸切。", "1")] + [row("佛", f"又{k}切。", "2") for k in range(6)] + [row("化", "音支。", "3")]
    pages = yupian.read(table(tmp_path, readings))
    first, second = pages[97][0].row, pages[97][2].row
    assert second - first == 7, "the two are seven table rows apart"
    assert {(first, 0), (second, 0)} <= yupian.unsettled(pages), "and two printed headwords apart, so each makes the other doubtful"
    assert not {(first, 0), (second, 0)} & yupian.unsettled(pages, neighbours=False)
    assert yupian.kin(pages)[first, 0] == {(second, 0)}


TABLED = [path for path in sorted(SOURCES.glob("*.yaml")) if "reader" in (yaml.safe_load(path.read_text(encoding="utf-8")).get("format") or {})]


def test_a_tabled_dictionary_is_registered_for_the_cutter():
    assert {path.stem for path in TABLED} >= {"longkan-shoujing", "xinxiu-yupian"}


@pytest.mark.parametrize("path", TABLED, ids=lambda path: path.stem)
def test_a_tabled_source_names_its_reader_corpus_and_sizes(path):
    source = yaml.safe_load(path.read_text(encoding="utf-8"))
    reader = importlib.import_module(f"glyph_atlas.{source['format']['reader']}")
    assert all(callable(getattr(reader, name, None)) for name in ("read", "paged", "unsettled", "kin"))
    assert source["format"]["file"].endswith(".csv") and source["format"]["edition"]
    assert {"corpus", "prefix", "volumes", "dating"} <= source.keys()
    for copy in source["copies"]:
        assert len(copy["first"]) == 2 and copy["heads"] and copy["rows"]
        corpus = f"{source['corpus']}-{copy['id']}"
        assert UNIT_ID_PREFIXES[f"{source['prefix']}-{copy['id']}:"] == corpus, "the site finds a unit's corpus by its prefix"
        assert corpus in UNIT_CORPORA and corpus in ID_FAMILIES and corpus in {known["name"] for known in KNOWN}
        assert date_claims.CORPORA[corpus] == source["id"] and corpus in date_claims.OWN_DATING
