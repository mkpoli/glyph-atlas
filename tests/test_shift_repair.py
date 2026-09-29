import numpy as np

from glyph_atlas.review import shift_repair
from glyph_atlas.review.shift_repair import FLOOR, OFFSETS, path, propose


def shows(offset: int, p: float = 0.95) -> np.ndarray:
    row = np.full(len(OFFSETS), FLOOR)
    row[OFFSETS.index(offset)] = p
    return row


def block(labels: str):
    return {("block", "k", 1, "ocr1"): [(f"u{i}", i, label) for i, label in enumerate(labels)]}


def test_path_pays_for_each_change():
    doubtful = shows(1, 0.6)
    doubtful[OFFSETS.index(0)] = 0.3
    cost = -np.log(np.stack([shows(0), shows(0), doubtful, shows(0)]))
    assert [OFFSETS[c] for c in path(cost)] == [0, 0, 0, 0]
    cost = -np.log(np.stack([shows(0), shows(1), shows(1), shows(1)]))
    assert [OFFSETS[c] for c in path(cost)] == [0, 1, 1, 1]


def test_a_run_shifted_by_one_takes_the_next_labels():
    blocks = block("一此湖水の")
    shown = {"u0": shows(0), "u1": shows(1), "u2": shows(1), "u3": shows(1), "u4": shows(0)}
    sizes = dict.fromkeys(shown, (30, 30))
    got = {p["unit_id"]: p["character"] for p in propose(blocks, shown, sizes, set())}
    assert got == {"u1": "湖", "u2": "水", "u3": "の"}


def test_a_lone_misreading_moves_nothing():
    blocks = block("一此湖水の")
    shown = {"u0": shows(0), "u1": shows(0), "u2": shows(1), "u3": shows(0), "u4": shows(0)}
    assert propose(blocks, shown, dict.fromkeys(shown, (30, 30)), set()) == []


def test_protected_small_and_unsure_crops_keep_their_labels():
    blocks = block("一此湖水の")
    shown = {"u0": shows(0), "u1": shows(1, 0.6), "u2": shows(1), "u3": shows(1), "u4": shows(0)}
    sizes = {**dict.fromkeys(shown, (30, 30)), "u3": (5, 30)}
    got = {p["unit_id"] for p in propose(blocks, shown, sizes, {"u2"})}
    assert got == set()  # u1 unsure, u2 protected, u3 too small


def test_the_method_is_named():
    assert shift_repair.METHOD == "block-shift-v1"
