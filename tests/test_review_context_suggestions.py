"""Context hints remain fast, source-bound and independent of visual OCR votes."""

from glyph_atlas.review.context_suggestions import context_guesses
from glyph_atlas.schema import Line, Unit


def example(raw="川尻　ベツヲシヨロ", source=None):
    source = source or list(raw)
    line = Line(id="line", page_id="page", seq=0, text_raw=raw, text=raw.replace("　", ""))
    units = [Unit(id=f"u{i}", line_id=line.id, seq=(i + 1) * 10, text_source=text)
             for i, text in enumerate(source)]
    return line, units


def texts(result):
    return [candidate["text"] for candidate in result["candidates"]]


def test_joined_guesses_use_source_position_including_unboxed_units_and_spaces():
    line, units = example()
    result = context_guesses(units[6], line, units)
    assert "シヨロ" in texts(result)
    assert "シヨ" in texts(result)
    assert result["anchor"] == 6
    assert result["basis"] == "transcription"
    assert "votes" not in result
    assert all("score" not in candidate for candidate in result["candidates"])
    assert all("　" not in text for text in texts(result))


def test_shift_neighbors_are_offered_without_changing_identity_or_reading():
    line, units = example("手を取る")
    unit = units[0].model_copy(update={"reading": "て", "unicode": "U+624B"})
    before = unit.model_dump()
    result = context_guesses(unit, line, units)
    assert "を" in texts(result)
    assert unit.model_dump() == before


def test_ruby_is_not_joined_to_the_main_text():
    line, units = example("手（て）を取る", ["手", "を", "取", "る"])
    result = context_guesses(units[0], line, units)
    assert "手を" in texts(result)
    assert "て" not in "".join(texts(result))


def test_split_group_is_one_transcription_anchor():
    line, units = example("シヨロ")
    units[0].group_id = "split"
    duplicate = units[0].model_copy(update={"id": "other-half", "seq": 11})
    result = context_guesses(units[1], line, [*units, duplicate])
    assert result["anchor"] == 1
    assert "ヨロ" in texts(result)


def test_combining_voicing_marks_and_supplementary_characters_stay_whole():
    line, units = example("トモ𪜈が", ["ト", "モ", "𪜈", "が"])
    result = context_guesses(units[2], line, units)
    assert "𪜈" in texts(result)
    assert "が" in texts(result)
    assert "𪜈が" in texts(result)
    assert "゙" not in texts(result)


def test_unknown_source_and_missing_line_abstain():
    line, units = example("あいう")
    other = units[0].model_copy(update={"text_source": "字"})
    assert context_guesses(other, line, [other, *units[1:]])["status"] == "unavailable"
    assert context_guesses(units[0], None, units)["status"] == "unavailable"


def test_gap_and_spaces_cannot_be_crossed_by_a_join_guess():
    line, units = example("ア□イ　ウエ")
    result = context_guesses(units[0], line, units)
    assert "アイ" not in texts(result)
    assert "ア□イ" not in texts(result)
    assert "イウ" not in texts(result)


def test_long_lines_abstain_without_expensive_sequence_matching():
    line, units = example("あ")
    line.text_raw = "あ" * 9000
    assert context_guesses(units[0], line, units)["status"] == "unavailable"


def test_printed_separator_is_preserved_in_joined_character_guesses():
    line, units = example("キナ▲キナ")
    result = context_guesses(units[0], line, units)
    assert "キナ" in texts(result)
    assert "キナ▲" in texts(result)
    assert "▲キ" in texts(context_guesses(units[2], line, units))


def test_mark_after_kana_is_available_as_a_fast_joined_suggestion():
    line, units = example("シノ▲フ△○●◇◆")
    assert "シノ▲" in texts(context_guesses(units[0], line, units))
    assert "フ△" in texts(context_guesses(units[3], line, units))
    assert "○●" in texts(context_guesses(units[5], line, units))


def test_newly_encoded_kana_do_not_depend_on_python_unicode_version():
    line, units = example("雷ケ𛄥不")
    result = context_guesses(units[1], line, units)
    assert "𛄥" in texts(result)
    assert "ケ𛄥" in texts(result)
