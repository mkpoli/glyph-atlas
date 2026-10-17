"""Tests for the rubies of a line and the units they stand over."""

from __future__ import annotations

from glyph_atlas import koji, rubies, tables
from glyph_atlas.schema import Document, Line, Page, Unit


def _line(text: str, ident: str = "l1") -> Line:
    return Line(id=ident, page_id="p1", seq=1, text_raw=text, text=koji.plain(text))


def _units(text: str, line_id: str = "l1") -> list[Unit]:
    """One unit per character of `text`, in order, as the aligner places a line's tokens."""
    return [Unit(id=f"{line_id}:{index}", document_id="d1", page_id="p1", line_id=line_id, seq=index,
                 text_source=char)
            for index, char in enumerate(text, start=1)]


def _marks(text: str) -> list[tuple[str, str, dict[str, str], str]]:
    return [(mark.base, mark.source, mark.readings, mark.span) for mark in rubies.marks(text)]


def test_marks_read_each_field_of_each_markup():
    assert _marks("三日 計（バカリ）") == [("計", "legacy", {"right": "バカリ"}, "mono")]
    assert _marks("斗（と｜ばかり）") == [("斗", "legacy", {"right": "と", "left": "ばかり"}, "mono")]
    assert _marks("《振り仮名：鯡漁図｜ヘロキコイキ｜ニシントリ》") == [
        ("鯡漁図", "振り仮名", {"right": "ヘロキコイキ", "left": "ニシントリ"}, "group")]
    assert _marks("《ルビ：計｜はかり》") == [("計", "ルビ", {"right": "はかり"}, "mono")]
    # A nested ruby gives the outer its whole base and keeps its own reading.
    assert _marks("《振り仮名：孿（ふた）胎｜サンタイ》") == [
        ("孿胎", "振り仮名", {"right": "サンタイ"}, "group"), ("孿", "legacy", {"right": "ふた"}, "mono")]
    assert _marks("ばかり") == []


def test_a_damage_mark_stays_in_the_field_it_stands_in():
    assert _marks("《振り仮名：■斗｜の〓み》") == [("■斗", "振り仮名", {"right": "の〓み"}, "group")]


def test_struck_text_is_not_part_of_the_base():
    assert _marks("《振り仮名：《見せ消ち：許｜計》｜ばかり》")[0][0] == "計"


def test_a_correction_inside_a_reading_stays_in_the_reading():
    assert _marks("《振り仮名：相｜ア《見せ消ち：ノ｜ヒ》》") == [("相", "振り仮名", {"right": "アヒ"}, "mono")]
    assert _marks("《振り仮名：恵｜《見せ消ち：カ｜メグ》》") == [("恵", "振り仮名", {"right": "メグ"}, "mono")]
    # A struck damage mark is not read either.
    assert _marks("《振り仮名：朝｜ア《見せ消ち：■｜サ》》") == [("朝", "振り仮名", {"right": "アサ"}, "mono")]


def test_a_bar_inside_a_nested_bracket_does_not_start_a_field():
    assert _marks("《振り仮名：【a｜b】〓字｜かな｜〓い》")[0][2] == {"right": "かな", "left": "〓い"}


def test_a_variation_selector_belongs_to_its_character():
    assert _marks("《振り仮名：葛\U000E0100｜かづら》")[0][3] == "mono"


def test_link_gives_each_reading_the_units_of_its_base():
    line = _line("西夷地の《振り仮名：鯡漁図｜ヘロキコイキ｜ニシントリ》")
    found = rubies.link(line, _units("西夷地の鯡漁図"))
    assert [(row.side, row.reading, row.base_unit_ids, row.span) for row in found] == [
        ("right", "へろきこいき", ["l1:5", "l1:6", "l1:7"], "group"),
        ("left", "にしんとり", ["l1:5", "l1:6", "l1:7"], "group"),
    ]
    assert (found[0].id, found[0].text, found[0].source, found[0].placement) == (
        "l1:ruby0:right", "ヘロキコイキ", "振り仮名", "unchecked")
    assert line.text_raw[found[0].base_start:found[0].base_end].endswith("図")


def test_link_matches_units_by_text_and_leaves_an_unplaced_base_without_units():
    line = _line("その斗（ばかり）の計（はかり）")
    units = _units("その斗の計")
    # The aligner gave 斗 no unit; seq still counts the units it did place.
    del units[2]
    found = rubies.link(line, units)
    assert [(row.base, row.base_unit_ids) for row in found] == [("斗", []), ("計", ["l1:5"])]


def test_link_counts_a_group_once_and_skips_ruby_units():
    line = _line("その計（ばかり）")
    units = _units("その計")
    split = units[2].model_copy(update={"id": "l1:4", "seq": 4, "group_id": "g1"})
    units[2] = units[2].model_copy(update={"group_id": "g1"})
    ruby_unit = Unit(id="l1:r1", document_id="d1", page_id="p1", line_id="l1", seq=5, text_source="ば",
                     upstream={"source": "detect-align", "role": "ruby"})
    found = rubies.link(line, [*units, split, ruby_unit])
    assert found[0].base_unit_ids == ["l1:3", "l1:4"]


def test_by_unit_lists_every_ruby_over_a_unit():
    found = rubies.link(_line("《振り仮名：鯡（にしん）漁｜ニシントリ》"), _units("鯡漁"))
    over = rubies.by_unit(found)
    assert [(row.reading, row.span) for row in over["l1:1"]] == [("にしんとり", "group"), ("にしん", "mono")]
    assert [row.reading for row in over["l1:2"]] == ["にしんとり"]


def test_build_dataset_writes_a_valid_rubies_table(tmp_path):
    directory = tmp_path / "work"
    directory.mkdir()
    tables.write(directory / "documents.parquet", [Document(id="d1", title="本")], Document)
    tables.write(directory / "pages.parquet", [Page(id="p1", document_id="d1", seq=1, image="p1.jpg",
                                                    width=100, height=100)], Page)
    tables.write(directory / "lines.parquet", [_line("その計（ばかり）"), _line("ばかり", "l2")], Line)
    tables.write(directory / "units.parquet", _units("その計"), Unit)
    assert rubies.build_dataset(directory) == 1
    dataset = tables.Dataset(directory)
    assert dataset.validate() == []
    assert [row.base_unit_ids for row in dataset.read("rubies")] == [["l1:3"]]
