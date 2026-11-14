import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "models/benchmark"))

import counts


def test_a_box_is_right_when_it_is_cut_into_its_characters():
    pages = {"a": {"sample": "first", "boxes": {"long-or-large": 10, "other": 90}}}
    rows = [
        {"page": "a", "group": "long-or-large", "characters": 2, "made": 2},
        {"page": "a", "group": "long-or-large", "characters": 2, "made": 1},
        {"page": "a", "group": "other", "characters": 1, "made": 1},
        {"page": "a", "group": "other", "characters": 1, "made": 2},
        {"page": "a", "group": "other", "characters": 1, "made": 0},
        {"page": "a", "group": "other", "characters": None, "made": 3},
        {"page": "a", "group": "other", "characters": 1, "made": None},
    ]
    report = counts.score(rows, pages)
    assert report["boxes"] == 5 and report["gone"] == 1, "a box with no count or that the detector no longer draws is left out"
    assert (report["one_character"], report["one_character_cut"], report["one_character_lost"]) == (3, 1, 1)
    assert (report["several_right"], report["several_too_few"], report["several_too_many"]) == (1, 1, 0)
    # Half of the page's ten long boxes and a third of its ninety others are right.
    assert report["per_100_boxes"] == {"right": 35.0, "one_character_cut": 30.0, "one_character_lost": 30.0,
                                       "several_wrong": 5.0}
    assert sum(report["per_100_boxes"].values()) == 100


def test_a_group_with_no_judged_box_is_left_out_of_the_hundred():
    pages = {"a": {"sample": "first", "boxes": {"long-or-large": 10, "other": 90}}}
    rows = [{"page": "a", "group": "long-or-large", "characters": 2, "made": None},
            {"page": "a", "group": "other", "characters": 1, "made": 1}]
    assert counts.score(rows, pages)["per_100_boxes"]["right"] == 100.0
    assert counts.score([], {})["per_100_boxes"]["right"] == 0.0


def test_a_joined_box_is_part_of_a_box_for_each_detection_it_joins():
    boxes = [(0, 0, 40, 40), (0, 50, 40, 40), (5, 5, 20, 20), (100, 0, 40, 80)]
    made = counts.shares([(0, 1), (0, 2), (3,), (3,)], boxes)
    assert made[1] == 0.5, "two detections lying apart share the box that joins them"
    assert made[0] == 1.5, "the box of a detection and one inside it is a whole box of the outer one"
    assert made[2] == 0 and made[3] == 2


def test_boxes_are_matched_by_overlap():
    assert counts.iou((0, 0, 10, 10), (0, 0, 10, 10)) == 1.0
    assert counts.iou((0, 0, 10, 10), (20, 0, 10, 10)) == 0.0
    assert counts.inside((0, 0, 10, 10), (0, 0, 5, 10)) == 0.5


def test_every_listed_box_names_a_listed_page():
    import json

    labels = json.loads(counts.LABELS.read_text(encoding="utf-8"))
    assert {row["page"] for row in labels["boxes"]} == set(labels["pages"])
    assert all(row["characters"] is None or row["characters"] >= 0 for row in labels["boxes"])
    assert all(row["group"] in page["boxes"] for row in labels["boxes"] for page in [labels["pages"][row["page"]]])
