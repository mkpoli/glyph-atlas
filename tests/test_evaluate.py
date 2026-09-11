"""Tests for the alignment evaluator: one split, one merge, one miss, one false positive."""

from __future__ import annotations

from kuzushiji_atlas.evaluate import compare, iou, wilson
from kuzushiji_atlas.schema import Box, Unit


def unit(unit_id: str, box: tuple[int, int, int, int], label: str, page: str = "p1") -> Unit:
    x, y, w, h = box
    return Unit(id=unit_id, document_id="d1", page_id=page, box=Box(x=x, y=y, w=w, h=h),
                text_source=label, reading=label, unicode=label)


def test_iou_of_disjoint_and_identical_boxes():
    a = Box(x=0, y=0, w=10, h=10)
    assert iou(a, Box(x=20, y=0, w=10, h=10)) == 0.0
    assert iou(a, a) == 1.0
    assert round(iou(a, Box(x=5, y=0, w=10, h=10)), 4) == round(50 / 150, 4)


def test_wilson_interval_brackets_the_rate():
    low, high = wilson(95, 100)
    assert low < 0.95 < high
    assert (wilson(0, 0)) == (0.0, 1.0)


def test_one_merge_one_miss_one_split_one_false_positive():
    # Four characters on one page, ten pixels wide with six-pixel gaps. The prediction cuts the
    # first into two boxes, takes the second with a box twice as wide that reaches halfway into the
    # third, leaves the fourth alone, and adds a box on blank paper.
    truth = [
        unit("t1", (0, 0, 10, 10), "U+4E00"),
        unit("t2", (20, 0, 10, 10), "U+4E8C"),
        unit("t3", (36, 0, 10, 10), "U+4E09"),
        unit("t4", (52, 0, 10, 10), "U+56DB"),
    ]
    predicted = [
        unit("p1a", (0, 0, 6, 10), "U+4E00"),
        unit("p1b", (6, 0, 4, 10), "U+4E00"),
        unit("p2", (20, 0, 20, 10), "U+4E8C"),
        unit("p4", (200, 0, 10, 10), "U+4E94"),
    ]
    report = compare(truth, predicted)
    totals = report.totals
    assert totals["truth"] == 4
    assert totals["predicted"] == 4
    # t1 takes p1a; t2 takes the wide box; t3 and t4 stay unmatched.
    assert totals["matched"] == 2
    assert totals["joint"] == 2
    assert totals["box"] == 2
    assert totals["label"] == 2
    assert totals["false_positives"] == 2
    assert totals["unmatched_truth"] == 2
    # p1b overlaps only t1, so nothing was merged there. The wide box swallowed the third unit,
    # which is counted once.
    assert totals["splits"] == 0
    assert totals["merges"] == 1
    # t3 and t4 have no prediction within 0.3 IoU.
    assert totals["missed"] == 2
    rates = report.rates()
    assert rates["joint precision"][0] == 0.5
    assert rates["coverage"][0] == 0.5
    assert "joint precision" in report.markdown()


def test_a_label_difference_keeps_the_box_match():
    truth = [unit("t1", (0, 0, 10, 10), "U+4E00")]
    predicted = [unit("p1", (0, 0, 10, 10), "U+4E8C")]
    report = compare(truth, predicted)
    assert report.totals["box"] == 1
    assert report.totals["joint"] == 0
    assert report.pairs[0].label is False


def test_two_halves_of_one_truth_unit_are_a_split_error():
    truth = [unit("t1", (0, 0, 20, 10), "U+4E00")]
    predicted = [unit("p1", (0, 0, 10, 10), "U+4E00"), unit("p2", (10, 0, 10, 10), "U+4E00")]
    report = compare(truth, predicted)
    totals = report.totals
    assert totals["matched"] == 1 and totals["false_positives"] == 1
    # The second half overlaps only the same truth unit, so it is not a split of two truth units.
    assert totals["splits"] == 0
    assert totals["missed"] == 0
    assert totals["unmatched_truth"] == 0


def test_one_prediction_over_two_cut_characters_is_a_split_error():
    # Two neighbouring characters cut at overlapping boxes: one prediction takes its truth unit on
    # IoU, the other overlaps that unit and the one beside it, and reads as a split.
    truth = [unit("t1", (0, 0, 10, 10), "U+4E00"), unit("t2", (10, 0, 10, 10), "U+4E8C")]
    predicted = [unit("p1", (0, 0, 10, 10), "U+4E00"), unit("p2", (5, 0, 10, 10), "U+4E00")]
    report = compare(truth, predicted)
    totals = report.totals
    assert totals["matched"] == 1 and totals["false_positives"] == 1
    assert totals["splits"] == 1
    assert totals["missed"] == 0


def test_a_wide_prediction_over_two_truth_units_is_one_merge_error():
    truth = [unit("t1", (0, 0, 10, 10), "U+4E00"), unit("t2", (10, 0, 10, 10), "U+4E8C")]
    predicted = [unit("p1", (0, 0, 20, 10), "U+4E00")]
    report = compare(truth, predicted)
    totals = report.totals
    assert totals["matched"] == 1 and totals["unmatched_truth"] == 1
    # The swallowed unit is counted from the truth side: one unmatched truth unit that a matched
    # prediction still overlaps well past the lower threshold.
    assert totals["merges"] == 1
    assert totals["splits"] == 0
    assert totals["missed"] == 0
