from glyph_atlas import refs
from glyph_atlas.clusters import clusters, is_conjoining_jamo_syllable, is_one_character, shape_key
from glyph_atlas.corpus.api import _resolve
from glyph_atlas.corpus.identity import family_of
from glyph_atlas.corpus.occurrence import find_occurrences
from glyph_atlas.unit_scope import character_count


def test_a_syllable_spelt_with_jamo_is_one_character():
    assert clusters("ᄒᆞᆯ가") == ["ᄒᆞᆯ", "가"]
    assert character_count("ᄃᆞᆯ") == 1
    assert character_count("가나") == 2


def test_a_jamo_after_a_character_that_is_not_hangul_stands_alone():
    assert clusters("漢ᅡ") == ["漢", "ᅡ"]


def test_a_leading_mark_joins_the_character_after_it():
    assert clusters("゚リ") == ["゚リ"]
    assert character_count("゚リ") == 1
    assert is_one_character("゚リ")
    assert character_count("゙") == 0


def test_the_nfc_spelling_of_an_old_syllable_is_one_syllable():
    # U+1109 U+1168 U+11F0 composes to 셰 + ᇰ.
    assert is_conjoining_jamo_syllable("셰ᇰ")
    row = refs.character("U+C170 U+11F0")
    assert row is not None and row.code_point == "U+C170 U+11F0"
    assert is_conjoining_jamo_syllable("나〮")


def test_a_spelling_nfc_would_change_has_no_row_of_its_own():
    assert not is_conjoining_jamo_syllable("가")  # NFD 가, which is U+AC00
    assert refs.character("U+1100 U+1161") is None
    assert not is_conjoining_jamo_syllable("가")
    assert not is_conjoining_jamo_syllable("ᅟᅠ")


def test_a_kana_sequence_keeps_no_family_while_a_jamo_syllable_is_its_own():
    assert family_of("U+1B00A U+3099") is None
    assert family_of("U+1112 U+119E")["code_point"] == "U+1112 U+119E"


def test_an_occurrence_is_a_whole_written_character():
    assert [start for start, *_ in find_occurrences("ᄒᆞᆯᄒᆞ", "ᄒᆞ")] == [3]
    assert [start for start, *_ in find_occurrences("葛\U000E0100葛", "葛")] == [2]


def test_a_code_point_query_names_one_character():
    assert _resolve("U+1112 U+119E") == "ᄒᆞ"
    assert _resolve("U+3042 U+3044") is None


def test_one_printed_shape_has_one_classifier_key():
    assert shape_key("ㅣ") == shape_key("ᅵ") == shape_key("ᅟᅵ") == "ᅵ"
    assert shape_key("ㅅ") == shape_key("ᄉ") == shape_key("ᄉᅠ") == "ᄉ"
    assert shape_key("ㆍ") == "ᆞ"
    assert shape_key("ᆨ") == "ᄀ"  # a lone final is printed as the consonant


def test_a_syllable_keeps_its_own_key():
    assert shape_key("가") == "가"
    assert shape_key("ᄒᆞᆯ") == "ᄒᆞᆯ"
    assert shape_key("ᅟᅵᆫ") == "ᅵᆫ"  # printed with no initial
    assert shape_key("ᅟᅠ") == "ᅟᅠ"
