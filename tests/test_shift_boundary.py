import math

import numpy as np

from glyph_atlas.review import shift_boundary
from glyph_atlas.review.shift_boundary import OFFSETS, align, claims, doubled, resolve

KEY = ("block", "k", 1, "ocr1")


def shows(offset: int | None = None, p: float = 0.95) -> np.ndarray:
    """A crop the classifier reads as the text at `offset` of its own place, and as nothing else."""
    row = np.zeros(len(OFFSETS))
    if offset is not None:
        row[OFFSETS.index(offset)] = p
    return row


def block(text: str, labels: str):
    return {KEY: [(f"u{i}", i, written, label) for i, (written, label) in enumerate(zip(text, labels, strict=True))]}


def settled(windows):
    return [w for w in windows if w["status"] == "settled"]


def test_the_extra_box_at_the_end_of_a_run_is_withheld():
    # A run shifted by +1 ends at u2; u3 is a fragment that kept its own label, which is u2's.
    blocks = block("一此湖水の山", "一湖水水の山")
    offsets = {"u1": 1, "u2": 1}
    shown = {"u0": shows(0), "u1": shows(1), "u2": shows(1), "u3": shows(), "u4": shows(0), "u5": shows(0)}
    sizes = {**dict.fromkeys(shown, (30, 30)), "u3": (8, 6)}
    [window] = settled(resolve(blocks, shown, sizes, set(), offsets))
    assert [item["unit_id"] for item in window["extra"]] == ["u3"]
    assert window["relabel"] == []


def test_a_crop_between_two_shifted_crops_follows_them():
    # u2 shows nothing the classifier can name, but both neighbours read one place further on.
    blocks = block("一此湖水の山", "一湖湖の山山")
    offsets = {"u1": 1, "u3": 1, "u4": 1}
    shown = {"u0": shows(0), "u1": shows(1), "u2": shows(1, 0.05), "u3": shows(1), "u4": shows(1), "u5": shows()}
    sizes = dict.fromkeys(shown, (30, 30))
    windows = settled(resolve(blocks, shown, sizes, set(), offsets))
    assert {item["unit_id"]: item["character"] for w in windows for item in w["relabel"]} == {"u2": "水"}


def test_a_run_extends_past_a_large_crop_to_the_fragment_beyond_it():
    # The run at −1 starts at u4; u3 is a whole character that belongs at place 2, and u2 is a dot.
    blocks = block("一此湖水の山", "一此湖水水の")
    offsets = {"u4": -1, "u5": -1}
    shown = {"u0": shows(0), "u1": shows(0), "u2": shows(), "u3": shows(-1, 0.02), "u4": shows(-1), "u5": shows(-1)}
    sizes = {**dict.fromkeys(shown, (30, 30)), "u2": (6, 5)}
    [window] = settled(resolve(blocks, shown, sizes, set(), offsets))
    assert [item["unit_id"] for item in window["extra"]] == ["u2"]
    assert {item["unit_id"]: item["character"] for item in window["relabel"]} == {"u3": "湖"}


def test_protected_crops_anchor_and_are_never_changed():
    blocks = block("一此湖水の山", "一湖水水の山")
    offsets = {"u1": 1, "u2": 1}
    shown = {"u0": shows(0), "u1": shows(1), "u2": shows(1), "u3": shows(), "u4": shows(0), "u5": shows(0)}
    sizes = {**dict.fromkeys(shown, (30, 30)), "u3": (8, 6)}
    windows = resolve(blocks, shown, sizes, {"u3"}, offsets)
    assert [w["status"] for w in windows] == ["protected"]
    assert all(not w["relabel"] and not w["extra"] for w in windows)


def test_two_crops_that_both_show_the_character_are_left_alone():
    blocks = block("一此湖水の", "一此此水の")
    offsets = {"u2": -1}
    shown = {"u0": shows(0), "u1": shows(0), "u2": shows(-1), "u3": shows(0), "u4": shows(0)}
    windows = resolve(blocks, shown, dict.fromkeys(shown, (30, 30)), set(), offsets)
    assert [w["status"] for w in windows] == ["both-shown"]


def test_a_close_call_is_left_unsure():
    # u3 and u4 are alike and read as nothing: either could be the extra box, and the other the の.
    blocks = block("一此湖水の山", "一湖水水の山")
    offsets = {"u1": 1, "u2": 1}
    shown = {"u0": shows(0), "u1": shows(1), "u2": shows(1), "u3": shows(1, 0.03), "u4": shows(0, 0.03), "u5": shows(0)}
    assert [w["status"] for w in resolve(blocks, shown, dict.fromkeys(shown, (30, 30)), set(), offsets)] == ["unsure"]


def test_a_character_the_classifier_has_no_class_for_is_neutral():
    blocks = block("一此湖水の山", "一湖湖の山山")
    offsets = {"u1": 1, "u3": 1, "u4": 1}
    unknown = shows()
    unknown[OFFSETS.index(1)] = math.nan
    shown = {"u0": shows(0), "u1": shows(1), "u2": unknown, "u3": shows(1), "u4": shows(1), "u5": shows()}
    windows = settled(resolve(blocks, shown, dict.fromkeys(shown, (30, 30)), set(), offsets))
    assert {item["unit_id"]: item["character"] for w in windows for item in w["relabel"]} == {"u2": "水"}


def test_a_genuine_doubled_character_claims_two_places():
    crops = [("u0", 0, "人", "人"), ("u1", 1, "人", "人"), ("u2", 2, "の", "の")]
    assert claims(crops, {}) == {"u0": 0, "u1": 1, "u2": 2}
    shown = {identity: shows(0) for identity, *_ in crops}
    assert resolve({KEY: crops}, shown, dict.fromkeys(shown, (30, 30)), set()) == []


def test_claims_prefer_the_recorded_offset_and_leave_a_foreign_label_without_a_place():
    crops = [("u0", 0, "水", "水"), ("u1", 1, "水", "水"), ("u2", 2, "の", "金")]
    assert claims(crops, {"u1": -1}) == {"u0": 0, "u1": 0, "u2": None}


def test_align_returns_the_two_cheapest_alignments():
    # One crop, two places: take the first, take the second, or be extra and skip both.
    top = align([[1.0, 2.0]], [10.0], 2)
    assert [round(cost, 3) for cost, _ in top] == [round(1.0 + shift_boundary.SKIP, 3), round(2.0 + shift_boundary.SKIP, 3)]
    assert [chosen for _, chosen in top] == [(0,), (1,)]


def test_doubled_counts_neighbours_across_a_withheld_box():
    blocks = block("一湖水の", "一湖湖の")
    assert doubled(blocks) == (3, 1)
    assert doubled(blocks, withheld={"u2"}) == (2, 0)
    assert doubled(blocks, relabel={"u2": "水"}) == (3, 0)


def test_the_method_is_named():
    assert shift_boundary.METHOD == "block-boundary-v1"
