"""Tests for reading zh.wikisource page texts as the glyphs printed on the page."""

from __future__ import annotations

from glyph_atlas.wikitext import Glyph, glyphs


def text(column: list[Glyph]) -> str:
    return "".join(glyph.text for glyph in column)


def test_each_line_is_a_column_and_punctuation_is_dropped():
    columns = glyphs("元年癸丑，鄭城邢丘。\n\n　　藍君\n\n")
    assert [text(c) for c in columns] == ["元年癸丑鄭城邢丘", "藍君"]
    assert {g.role for c in columns for g in c} == {"main"}


def test_a_split_annotation_gives_its_right_and_left_columns():
    [column] = glyphs("十六年{{雙行註文|晉出公二|十二年}}")
    assert text(column) == "十六年晉出公二十二年"
    assert [(g.role, g.column) for g in column[3:]] == [("warigaki", 1)] * 4 + [("warigaki", 2)] * 3


def test_a_correction_keeps_the_printed_character():
    [column] = glyphs("{{校|巳|己}}丑")
    assert text(column) == "巳丑"


def test_rare_characters_and_gaps():
    [column] = glyphs("萬{{SKchar|3771|邦}}且{{SKchar2|32|&#194880;}}{{SKchar|4001}}{{?|⿰亻鞋}}")
    assert [g.text for g in column] == ["萬", "邦", "且", "\U0002f940", "", ""]
    assert [g.role for g in column][-2:] == ["unreadable", "unreadable"]


def test_links_conversion_guards_and_rare_character_templates_keep_their_text():
    [column] = glyphs("聞-{于}-[[w:周宣王|宣王]]{{!|𫉬|⿱艹⿰犭⿱隹夊}}{{YL|天明七年|1787年}}")
    assert text(column) == "聞于宣王𫉬天明七年"


def test_notes_and_substituted_forms_keep_their_role():
    [column] = glyphs("臣深田{{*|正純}}臣{{original character|踏|A04024-008}}")
    assert [(g.text, g.role) for g in column] == [
        ("臣", "main"), ("深", "main"), ("田", "main"), ("正", "note"), ("純", "note"), ("臣", "main"),
        ("踏", "substituted")]


def test_editorial_templates_files_comments_and_spacing_are_not_text():
    columns = glyphs("{{ia|：}}從来<!--\n-->帝王{{nop}}{{gap|4em}}[[File:A.jpg|frameless|center]]<small>治</small>")
    assert [text(c) for c in columns] == ["從来帝王治"]
    assert [text(c) for c in glyphs("{{gap|4em}}{{letter-spacing|1em|錚錚其烈}}")] == ["錚錚其烈"]


def test_a_top_of_column_template_and_br_start_a_new_column():
    columns = glyphs("未𡮢不以敬{{DG|天}}法<br/>祖")
    assert [text(c) for c in columns] == ["未𡮢不以敬", "天法", "祖"]


def test_a_variation_selector_stays_with_its_character():
    [column] = glyphs("葛\U000e0100城")
    assert [g.text for g in column] == ["葛\U000e0100", "城"]


def test_a_template_nested_in_an_annotation_leaves_the_annotation_role_after_it():
    [column] = glyphs("{{雙行註文|甲{{*|乙}}丙|丁}}")
    assert [(g.text, g.role, g.column) for g in column] == [
        ("甲", "warigaki", 1), ("乙", "note", 1), ("丙", "warigaki", 1), ("丁", "warigaki", 2)]
    [column] = glyphs("{{*|甲{{雙行註文|乙|丙}}丁}}")
    assert [(g.text, g.role) for g in column][-1] == ("丁", "note")


def test_an_annotation_spanning_a_line_break_keeps_its_role_in_the_next_column():
    columns = glyphs("{{雙行註文|神農氏|叛不用帝命\n黃帝於是}}")
    assert [(g.text, g.role, g.column) for g in columns[1]] == [
        ("黃", "warigaki", 2), ("帝", "warigaki", 2), ("於", "warigaki", 2), ("是", "warigaki", 2)]


def test_spacing_templates_keep_every_text_parameter():
    [column] = glyphs("{{lsp|.475em|二品頂戴前出使大|臣}}")
    assert text(column) == "二品頂戴前出使大臣"


def test_small_seal_and_multi_line_templates_keep_all_their_text():
    assert [text(c) for c in glyphs("{{*|賜紫金魚袋{{-|臣}}}}")] == ["賜紫金魚袋臣"]
    assert [text(c) for c in glyphs("{{Seal|外務|大臣|之印|font size=24}}")] == ["外務大臣之印"]
    assert [text(c) for c in glyphs("{{多行合一|1=光緒二十一年即|2=明治二十八年}}")] == ["光緒二十一年即明治二十八年"]


def test_named_parameters_are_not_text_and_equals_signs_in_text_are():
    [column] = glyphs("{{雙行註文|甲|乙|align=center}}")
    assert text(column) == "甲乙"


def test_conversion_rules_take_the_traditional_form_and_ids_are_one_unreadable_glyph():
    assert [text(c) for c in glyphs("-{zh-hans:汉;zh-hant:漢}-字")] == ["漢字"]
    [column] = glyphs("甲⿰阝耴乙⿱⿰木木火丙")
    assert [(g.text, g.role) for g in column] == [
        ("甲", "main"), ("", "unreadable"), ("乙", "main"), ("", "unreadable"), ("丙", "main")]


def test_annotation_is_a_note():
    [column] = glyphs("甲{{Annotation|注文}}")
    assert [(g.text, g.role) for g in column] == [("甲", "main"), ("注", "note"), ("文", "note")]


def test_a_length_parameter_is_layout():
    assert [text(c) for c in glyphs("{{right|大秦寺僧景淨述|1em}}")] == ["大秦寺僧景淨述"]


def test_remarks_and_link_targets_are_not_text():
    assert [text(c) for c in glyphs("{{參|落|此句開頭應為「落日欲沒」}}日")] == ["落日"]
    assert [text(c) for c in glyphs("{{PL|屠公本畯|屠本畯}}")] == ["屠公本畯"]


def test_an_unreadable_glyph_keeps_its_annotation_column():
    [column] = glyphs("{{雙行註文|甲{{?}}|丁⿰阝耴}}")
    assert [(g.text, g.role, g.column) for g in column] == [
        ("甲", "warigaki", 1), ("", "unreadable", 1), ("丁", "warigaki", 2), ("", "unreadable", 2)]
