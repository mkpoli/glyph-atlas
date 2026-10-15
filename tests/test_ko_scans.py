from __future__ import annotations

import numpy as np

from glyph_atlas import eonhae, ko_scans
from glyph_atlas.schema import Box


def box(x: int, y: int, w: int = 40, h: int = 40) -> Box:
    return Box(x=x, y=y, w=w, h=h)


def models(atlas_rows, hangul_rows, atlas_classes, hangul_classes):
    return ko_scans.Models(
        np.asarray(atlas_rows, dtype=float), atlas_classes,
        np.asarray(hangul_rows, dtype=float), eonhae.hangul_shape_index(hangul_classes), hangul_classes,
    )


def test_page_tokens_keep_hanja_readings_and_old_hangul_in_print_order() -> None:
    text = "::右우第뎨六륙章쟝\n\n子ᄌᆞㅣ 曰왈{{nop}}"
    assert ko_scans.page_tokens(text) == ["右", "우", "第", "뎨", "六", "륙", "章", "쟝", "子", "ᄌᆞ", "ㅣ", "曰", "왈"]


def test_page_tokens_keep_a_lone_vowel_apart_from_the_hanja_before_it() -> None:
    assert ko_scans.page_tokens("天大\u1175地") == ["天", "大", "\u1175", "地"]


def test_page_tokens_drop_private_use_characters() -> None:
    assert ko_scans.page_tokens("大대") == ["大", "대"]


def test_reading_order_reads_columns_right_to_left_and_each_top_to_bottom() -> None:
    boxes = [box(0, 100), box(200, 50), box(0, 0), box(205, 0), box(100, 0)]
    assert ko_scans.reading_order(boxes) == [[3, 1], [4], [2, 0]]


def test_reading_order_keeps_a_narrow_reading_in_its_hanja_column() -> None:
    boxes = [box(100, 0, 60, 60), box(115, 70, 30, 25), box(0, 0, 60, 60)]
    assert ko_scans.reading_order(boxes) == [[0, 1], [2]]


def test_align_skips_a_box_the_transcription_leaves_out() -> None:
    # three tokens, four boxes: box 1 is a stain and every token fits its own box
    costs = np.full((3, 4), 9.0)
    costs[0, 0] = costs[1, 2] = costs[2, 3] = 0.1
    steps = ko_scans.align(costs)
    assert [(s.move, s.token, s.box) for s in steps] == [
        ("match", 0, 0), ("skip_box", None, 1), ("match", 1, 2), ("match", 2, 3)]


def test_align_leaves_a_token_without_a_box_when_the_detector_missed_it() -> None:
    costs = np.full((3, 2), 9.0)
    costs[0, 0] = costs[2, 1] = 0.1
    steps = ko_scans.align(costs)
    assert [(s.move, s.token, s.box) for s in steps] == [
        ("match", 0, 0), ("skip_token", 1, None), ("match", 2, 1)]


def test_costs_score_hanja_with_the_atlas_and_hangul_by_shape_and_unknowns_flat() -> None:
    found = models(
        atlas_rows=[[0.9, 0.1], [0.2, 0.8]],
        hangul_rows=[[0.3, 0.7], [0.6, 0.4]],
        atlas_classes=["U+5927", "other"],
        hangul_classes=["U+B300", "other"],
    )
    costs = found.costs(["大", "대", "鬱"], [0, 1])
    assert np.allclose(costs[0], -np.log([0.9, 0.2]))
    assert np.allclose(costs[1], -np.log([0.3, 0.6]))
    assert np.allclose(costs[2], [ko_scans.UNMODELLED, ko_scans.UNMODELLED])


def test_verdicts_use_each_model_top_five() -> None:
    found = models(
        atlas_rows=[[0.9, 0.1]], hangul_rows=[[0.1, 0.9]],
        atlas_classes=["U+5927", "other"], hangul_classes=["U+B300", "other"],
    )
    assert found.verdict("大", 0)[0] is True
    assert found.verdict("대", 0)[0] is True
    assert found.verdict("鬱", 0)[0] is None


def matched(token: int, verdict: bool | None) -> ko_scans.Match:
    return ko_scans.Match(token, token, verdict, (), "atlas")


def test_an_unjudged_match_needs_agreeing_neighbours_with_nothing_skipped_between() -> None:
    steps = [ko_scans.Step("match", t, t) for t in range(3)]
    held = [matched(0, True), matched(1, None), matched(2, True)]
    assert [m.token for m in ko_scans.emitted(steps, held)] == [0, 1, 2]

    gap = [steps[0], ko_scans.Step("skip_box", None, 9), steps[1], steps[2]]
    assert [m.token for m in ko_scans.emitted(gap, held)] == [0, 2]

    weak = [matched(0, False), matched(1, None), matched(2, True)]
    assert [m.token for m in ko_scans.emitted(steps, weak)] == [2]


def test_a_page_needs_enough_agreement_and_enough_agreeing_matches() -> None:
    assert ko_scans.page_accepted([matched(t, True) for t in range(3)])
    assert not ko_scans.page_accepted([matched(t, True) for t in range(2)])
    assert not ko_scans.page_accepted([matched(0, True)] * 6 + [matched(1, False)] * 4)
    assert not ko_scans.page_accepted([matched(0, None)] * 5)


def test_unit_ids_are_stable_and_prefixed() -> None:
    first = ko_scans.unit_id("ws:ko:scan:x:1", box(1, 2, 3, 4))
    assert first == ko_scans.unit_id("ws:ko:scan:x:1", box(1, 2, 3, 4))
    assert first.startswith("ks:") and first != ko_scans.unit_id("ws:ko:scan:x:2", box(1, 2, 3, 4))


def test_a_scaled_box_stays_inside_the_page() -> None:
    import importlib.util
    import sys
    from pathlib import Path

    spec = importlib.util.spec_from_file_location("cut_ko_scans", Path("scripts/cut_ko_scans.py"))
    script = importlib.util.module_from_spec(spec)
    sys.modules["cut_ko_scans"] = script
    spec.loader.exec_module(script)
    scaled = script.scale_box(box(901, 100, 101, 60), 1.5, 1.5, 1503, 1500)
    assert scaled.x + scaled.w <= 1503 and scaled.y + scaled.h <= 1500


def test_a_toned_syllable_is_judged_by_its_shape_without_the_tone_mark() -> None:
    found = models(atlas_rows=[[0.5, 0.5]], hangul_rows=[[0.9, 0.1]],
                   atlas_classes=["U+5927", "other"], hangul_classes=["U+AC00", "other"])
    assert ko_scans.page_tokens("가〮나") == ["가〮", "나"]
    assert found.model_of("가〮") == "hangul"
    assert found.verdict("가〮", 0)[0] is True
    assert np.allclose(found.costs(["가〮"], [0]), -np.log(0.9))
    assert eonhae.script_of(ko_scans.bare("가〮")) == "hangul"


def test_a_two_line_note_reads_its_right_half_before_its_left() -> None:
    # main glyphs 40 wide on axis x=100, a note of two half columns, then main text again
    boxes = [box(80, 0), box(80, 45),
             box(101, 90, 18, 18), box(101, 112, 18, 18), box(81, 90, 18, 18), box(81, 112, 18, 18),
             box(80, 135), box(20, 0)]
    assert ko_scans.reading_order(boxes) == [[0, 1, 2, 3, 4, 5, 6], [7]]
