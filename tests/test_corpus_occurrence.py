"""The occurrence layer keeps text hits and located crops apart."""

from __future__ import annotations

import json

from glyph_atlas.corpus.occurrence import (
    TOMO,
    Occurrence,
    Rect,
    Source,
    advisory_char_rect,
    classify,
    codepoint,
    codepoint_of_occurrence_id,
    deduplicate,
    find_occurrences,
    occurrence_id,
)


def source(corpus="honkoku-lines", doc="hl:D", page="hl:D:4", line="hl:D_4_000"):
    return Source(corpus=corpus, document_id=doc, page_id=page, line_id=line)


def make(text, start, char=TOMO, cls="literal_text", **kwargs):
    src = kwargs.pop("source", None) or source()
    return Occurrence(
        occurrence_id=occurrence_id(src, char, start, cls),
        char=char,
        codepoint=codepoint(char),
        char_class=cls,
        tier=kwargs.pop("tier", "line_rect"),
        source=src,
        span_start=start,
        span_end=start + 1,
        text_raw=text,
        context=text,
        **kwargs,
    )


class TestClassification:
    def test_a_literal_occurrence_is_the_character_itself(self):
        text = "\u3000\u3000\u30c8\u751f\u30b9\u30ec" + TOMO + "\u8766\u5937"
        (start, _, cls, _) = find_occurrences(text, TOMO)[0]
        assert text[start] == TOMO
        assert cls == "literal_text"

    def test_an_expansion_annotated_as_a_ligature_is_its_own_class(self):
        text = "\u5175\u30c8\u30e2\u3016" + TOMO + "\uff1a\u5408\u5b57\u3017\u601d"
        assert classify(text, text.index(TOMO), TOMO) == "annotated_ligature"

    def test_a_literal_glyph_and_an_annotation_in_one_line_are_told_apart(self):
        """``然𪜈【𪜈：合字】`` writes the glyph *and* annotates it.

        The occurrence outside the bracket is the text; the one inside it is the
        annotation's own copy naming the glyph. They are different evidence and must
        not both be counted as text occurrences.
        """
        text = "\u7136" + TOMO + "\u3010" + TOMO + "\uff1a\u5408\u5b57\u3011"
        literal_at = text.index(TOMO)
        annotated_at = text.index(TOMO, literal_at + 1)
        assert classify(text, literal_at, TOMO) == "literal_text"
        assert classify(text, annotated_at, TOMO) == "annotated_ligature"

    def test_a_variant_note_bracket_is_not_a_ligature_annotation(self):
        """``𪜈【モヵ】`` annotates a *variant reading*, not the glyph's identity."""
        text = "\u5206\u30eb" + TOMO + "\u3010\u30e2\u30f5\u3011"
        assert classify(text, text.index(TOMO), TOMO) == "literal_text"

    def test_a_separate_to_followed_by_mo_is_not_an_occurrence(self):
        """The whole point: two kana are not the ligature character."""
        assert find_occurrences("\u30c8\u30e2", TOMO) == []


class TestIdentity:
    def test_the_same_glyph_imported_twice_collapses(self):
        a = make("\u5c71" + TOMO + "\u4e91", 1, source=source(corpus="honkoku-lines"))
        b = make("\u5c71" + TOMO + "\u4e91", 1, source=source(corpus="honkoku-data"))
        merged = deduplicate([a, b])
        assert len(merged) == 1
        assert merged[0].meta["also_seen_in"] == ["honkoku-data"]

    def test_different_offsets_in_one_page_stay_distinct(self):
        a = make(TOMO + "\u4e91", 0)
        b = make("\u5c71" + TOMO, 1)
        assert len(deduplicate([a, b])) == 2

    def test_the_occurrence_id_carries_the_code_point(self):
        ident = occurrence_id(source(), TOMO, 3, "literal_text")
        assert codepoint_of_occurrence_id(ident) == "U+2A708"


class TestGeometry:
    def test_a_page_text_hit_has_no_crop(self):
        o = make("x" + TOMO, 1, tier="page_text")
        assert o.has_crop is False
        assert o.has_glyph_rect is False

    def test_a_line_rect_is_not_a_glyph_rect(self):
        o = make("x" + TOMO, 1, rects=[Rect(10, 20, 30, 900, "line", "upstream_bbox")])
        assert o.has_crop is True
        assert o.has_glyph_rect is False

    def test_an_arithmetic_rect_is_returned_but_labelled_advisory(self):
        line = Rect(0, 0, 100, 1000, "line", "upstream_bbox")
        text = "abcdefghij"
        rect = advisory_char_rect(line, text, 4)
        assert rect is not None
        assert rect.basis == "derived_char_index"
        assert rect.role == "glyph"
        # it is offered, and it must be refusable
        assert rect.basis not in ("upstream_bbox", "iiif_region", "machine_projection")

    def test_a_rect_carries_its_iiif_region(self):
        assert Rect(1, 2, 3, 4, "glyph", "machine_projection").iiif_region() == "1,2,3,4"


class TestSerialisation:
    def test_the_dict_states_what_is_known(self):
        o = make("x" + TOMO, 1, rects=[Rect(1, 2, 3, 4, "line", "upstream_bbox")])
        payload = o.as_dict()
        assert payload["tier"] == "line_rect"
        assert payload["has_crop"] is True
        assert payload["has_glyph_rect"] is False
        assert payload["review_state"] == "machine"
        json.dumps(payload, ensure_ascii=False)
