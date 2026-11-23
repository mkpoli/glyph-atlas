import gzip
import json

import numpy as np

from glyph_atlas.review import written_forms as wf
from glyph_atlas.review.written_forms import judge, nearness, pairs, whole


def test_pairs_need_both_characters_held_and_enough_tables_and_start_with_the_commoner():
    counts = {"国": 20, "國": 40, "島": 40, "嶋": 3, "禁": 50}
    found = pairs(counts)
    assert list(found) == [("國", "国")] and len(found["國", "国"]) >= wf.SOURCES


def _crops(centre, count, seed=0, spread=0.05):
    points = np.random.default_rng(seed).normal(0, spread, size=(count, 8)).astype(np.float32)
    points[:, : len(centre)] += np.array(centre, dtype=np.float32)
    return points / np.linalg.norm(points, axis=1, keepdims=True)


FIRST, SECOND = [1, 0, 0], [0, 1, 0]


def test_a_label_mostly_written_as_the_variant_still_parts():
    # 25 of the label's 40 crops are written as the variant: the label's own mean lies near the variant.
    vectors = np.concatenate([_crops(FIRST, 15, 1), _crops(SECOND, 25, 2), _crops(SECOND, 20, 3)])
    own = np.array([True] * 40 + [False] * 20)
    found = judge(vectors, own, np.zeros(60, bool))
    assert found["strays"] == 0 and found["filed"] == 20
    assert found["rows"].tolist() == list(range(15, 40))
    assert (found["near"] >= wf.NEAR).all() and (found["near"] - found["own"] >= wf.MARGIN).all()


def test_a_crop_of_neither_form_is_near_neither_side():
    vectors = np.concatenate([_crops(FIRST, 30, 1), _crops([0, 0.1, 1], 1, 4), _crops(SECOND, 20, 3)])
    own = np.array([True] * 31 + [False] * 20)
    found = judge(vectors, own, np.zeros(51, bool))
    assert found["rows"].tolist() == [30] and found["near"][0] < wf.NEAR


def test_strays_count_the_variants_crops_on_the_labels_side():
    vectors = np.concatenate([_crops(FIRST, 30, 1), _crops(FIRST, 6, 5), _crops(SECOND, 20, 3)])
    own = np.array([True] * 30 + [False] * 26)
    found = judge(vectors, own, np.zeros(56, bool))
    assert found["strays"] == 6 and found["filed"] == 26


def test_suspects_move_no_mean_and_are_judged():
    # 30 suspect crops of the label, of a third shape, would drag the label's mean away.
    vectors = np.concatenate([_crops(FIRST, 20, 1), _crops([0, 0.2, 1], 30, 6), _crops(SECOND, 1, 7), _crops(SECOND, 20, 3)])
    own = np.array([True] * 51 + [False] * 20)
    suspect = np.array([False] * 20 + [True] * 31 + [False] * 20)
    found = judge(vectors, own, suspect)
    assert 50 in found["rows"] and found["strays"] == 0
    assert found["near"][found["rows"].tolist().index(50)] >= wf.NEAR


def test_too_few_crops_on_a_side_judge_nothing():
    vectors = np.concatenate([_crops(FIRST, 30, 1), _crops(SECOND, wf.MIN_SIDE - 1, 3)])
    assert judge(vectors, np.array([True] * 30 + [False] * (wf.MIN_SIDE - 1)), np.zeros(39, bool)) is None


def test_nearness_is_the_mean_of_the_nearest():
    others = np.array([[1, 0], [0.6, 0.8], [0, 1]], dtype=np.float32)
    assert np.allclose(nearness(np.array([[1, 0]], dtype=np.float32), others, k=2), [0.8])


def test_a_box_is_judged_against_its_page(tmp_path):
    crops = [{"slot": n, "box": {"w": 40, "h": 40}} for n in range(6)] + [{"slot": "6", "box": {"w": 40, "h": 80}}, {"slot": 7}]
    with gzip.open(tmp_path / "7-1.json.gz", "wt", encoding="utf-8") as handle:
        json.dump({"crops": crops}, handle)
    page = 7 << wf.PAGE_BITS
    assert whole({"a": page | 1, "b": page | 6, "c": page | 7, "d": 8 << wf.PAGE_BITS}, tmp_path) == {"a": None, "b": "joined"}
