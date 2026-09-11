"""Tests of the alignment: matches, skips, a split, a merge, a gap and the acceptance rule."""

from __future__ import annotations

import math

from kuzushiji_atlas import align
from kuzushiji_atlas.schema import Box, Line, ReviewState, UnitKind


def box(x: int, y: int = 10, w: int = 18, h: int = 18) -> Box:
    return Box(x=x, y=y, w=w, h=h)


def line(text: str, *, vertical: bool = True, box_: Box | None = None) -> Line:
    from kuzushiji_atlas import koji

    return Line(
        id="hl:item:0:L1",
        page_id="hl:item:0",
        seq=0,
        box=box_ or Box(x=0, y=0, w=200, h=40),
        vertical=vertical,
        text_raw=text,
        text=koji.plain(text),
    )


def detections(*xs: int, score: float = 0.9) -> list[align.Detection]:
    return [align.Detection(box=box(x), score=score) for x in xs]


class Fixed:
    """A classifier that scores the code points of one character highly and the rest at the floor."""

    def __init__(self, high: dict[str, float] | None = None, default: float = 0.05) -> None:
        self.high = high or {}
        self.default = default

    def score_set(self, crop: Box, code_points: set[str]) -> float:
        for point, value in self.high.items():
            if point in code_points:
                return value
        return self.default


def run(**overrides) -> align.Run:
    base = {"name": "test", "accept": 0.5, "margin": 0.0}
    base.update(overrides)
    return align.Run(**base)


def test_tokens_in_reading_order_and_units_get_ids_from_the_run():
    parsed = align.tokens_of(line("漢字かな"))
    assert [token.text for token in parsed] == ["漢", "字", "か", "な"]
    units, _ = align.align_line(line("漢字"), detections(10, 40), run=run())
    assert [unit.box.x for unit in units] == [40, 10]
    assert all(unit.method == "detect-align" and unit.page_id == "hl:item:0" for unit in units)
    again, _ = align.align_line(line("漢字"), detections(10, 40), run=run())
    assert [unit.id for unit in units] == [unit.id for unit in again]
    other, _ = align.align_line(line("漢字"), detections(10, 40), run=run(accept=0.7))
    assert [unit.id for unit in units] != [unit.id for unit in other]


def test_a_match_carries_the_detector_and_text_confidence():
    units, _ = align.align_line(
        line("漢"), detections(10), run=run(), classifier=Fixed({"U+6F22": 0.8}), crop_of=lambda page, b: b
    )
    unit = units[0]
    assert unit.unicode == "U+6F22" and unit.classification.value == "identified"
    assert unit.confidence.detection == 0.9
    assert math.isclose(unit.confidence.text, 0.8, rel_tol=1e-6)
    assert unit.review is ReviewState.MACHINE
    assert unit.confidence.model == "unset"


def test_a_faint_match_is_kept_but_rejected():
    units, _ = align.align_line(
        line("漢"), detections(10), run=run(accept=0.99), classifier=Fixed({"U+6F22": 0.8}),
        crop_of=lambda page, b: b,
    )
    assert units[0].review is ReviewState.REJECTED
    assert units[0].box is not None


def test_a_split_takes_two_detections_for_one_token():
    # Two detections where the transcription has one character: the cheaper path is a match plus a
    # skipped detection only when the second detection scores poorly for the token.
    units, _ = align.align_line(
        line("漢字"), detections(10, 30), run=run(weights=_weights(split=0.5, **{"skip-detection": 40.0})),
        classifier=Fixed({"U+6F22": 0.9, "U+5B57": 0.9}), crop_of=lambda page, b: b,
    )
    assert [unit.text_source for unit in units] == ["漢", "字"]
    assert [unit.box.x for unit in units] == [30, 10]
    assert all(abs(unit.confidence.segmentation - 0.9) < 1e-9 for unit in units)


def test_a_merge_takes_one_detection_for_two_tokens():
    units, _ = align.align_line(
        line("漢字"), detections(10), run=run(weights=_weights(merge=1.0, **{"skip-token": 40.0})),
        classifier=Fixed({"U+6F22": 0.9, "U+5B57": 0.9}), crop_of=lambda page, b: b,
    )
    assert len(units) == 1
    assert units[0].box.x == 10 and units[0].seq == 1


def test_a_gap_token_takes_no_box():
    units, _ = align.align_line(line("漢□字"), detections(10, 40), run=run())
    gaps = [unit for unit in units if unit.kind is UnitKind.GAP]
    assert len(gaps) == 1 and gaps[0].box is None
    assert gaps[0].review is ReviewState.MACHINE


def test_an_extra_detection_is_skipped_and_the_token_keeps_the_matching_box():
    units, _ = align.align_line(
        line("漢"), detections(10, 100), run=run(), classifier=Fixed({"U+6F22": 0.9}),
        crop_of=lambda page, b: b,
    )
    assert len(units) == 1 and units[0].box.x == 10


def test_a_first_token_with_no_detection_is_a_skip_not_a_box():
    units, _ = align.align_line(
        line("漢字"), detections(10), run=run(weights=_weights(**{"skip-token": 0.5, "skip-detection": 40.0})),
        classifier=Fixed({"U+5B57": 0.9}), crop_of=lambda page, b: b,
    )
    skipped = [unit for unit in units if unit.box is None]
    assert len(skipped) == 1 and skipped[0].review is ReviewState.REJECTED


def test_a_horizontal_line_reads_left_to_right():
    units, _ = align.align_line(line("漢字", vertical=False), detections(10, 40), run=run())
    assert [unit.box.x for unit in units] == [10, 40]


def test_a_detection_outside_the_line_box_is_not_used():
    units, _ = align.align_line(line("漢"), [align.Detection(box=box(400), score=0.9)], run=run())
    assert units[0].box is None


def test_a_hentaigana_token_stays_unassessed_and_keeps_its_candidates():
    units, _ = align.align_line(line("か"), detections(10), run=run())
    unit = units[0]
    assert unit.script.value == "hiragana"
    assert unit.classification.value == "unassessed"
    assert unit.reading == "か" and unit.unicode == "U+304B"
    assert len(unit.candidates) >= 13


def _weights(**overrides) -> dict[str, float]:
    base = {"skip-token": 8.0, "skip-detection": 6.0, "split": 12.0, "merge": 14.0, "gap": 3.0}
    base.update(overrides)
    return base
