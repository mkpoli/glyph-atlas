from glyph_atlas.unit_scope import unit_scope


def test_multichar_block_is_not_a_ligature_character():
    result = unit_scope({"unicode": "U+3068 U+308A U+3044", "text_source": "とりい",
                         "kind": "ligature", "granularity": "block"})
    assert result == {"granularity": "block", "character_count": 3, "needs_segmentation": True}


def test_encoded_ligature_is_one_character_despite_long_reading():
    result = unit_scope({"unicode": "U+2A708", "text_source": "トモ", "reading": "トモ", "kind": "ligature"})
    assert result["character_count"] == 1 and not result["needs_segmentation"]


def test_combining_voicing_and_ivs_remain_one_character():
    for value in ["U+304B U+3099", "U+845B U+E0100"]:
        assert not unit_scope({"unicode": value})["needs_segmentation"]


def test_explicit_sequence_is_not_admitted_by_one_character_label():
    assert unit_scope({"unicode": "U+4E00", "granularity": "sequence"})["needs_segmentation"]
    assert unit_scope({"unicode": "U+4E00", "granularity": "block"})["needs_segmentation"]


def test_missing_identity_and_whitespace_are_not_characters():
    assert unit_scope({})["needs_segmentation"]
    assert unit_scope({"text_source": "　"})["needs_segmentation"]


def test_old_sample_rows_are_checked_by_codepoint_or_label():
    assert unit_scope({"char": "とりい"})["needs_segmentation"]
    assert not unit_scope({"char": "トモ", "codepoint": "U+2A708"})["needs_segmentation"]


def test_an_ideographic_description_sequence_is_one_character() -> None:
    from glyph_atlas.unit_scope import character_count, unit_scope

    assert character_count("⿰亻胃") == 1
    assert character_count("⿰亻⿱亠女") == 1
    assert character_count("⿱⿱一丷兀") == 1
    assert character_count("⿾木") == 1
    assert character_count("⿰亻？") == 1
    # Anything else is counted character by character: a description with text around it, one short
    # of a component, or one naming a part no description may name.
    assert character_count("人⿰亻胃") == 4
    assert character_count("⿲木木") == 3
    assert character_count("⿰AB") == 3
    assert unit_scope({"unicode": None, "text_source": "⿰亻胃"})["needs_segmentation"] is False
