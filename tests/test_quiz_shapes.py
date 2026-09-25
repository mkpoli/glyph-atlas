"""Quick review shape order: one integer per crop, similar crops adjacent within a character."""
import json

import numpy as np
import pytest

from glyph_atlas.review import quiz_shapes


def test_a_dataset_without_an_order_has_an_empty_order(tmp_path):
    assert quiz_shapes.load(tmp_path) == {}


def test_a_dataset_order_is_read_and_follows_a_new_file(tmp_path):
    (tmp_path / "quiz-shapes.json").write_text(json.dumps({"revision": "r", "orders": {"a": 1, "b": 0}}))
    assert quiz_shapes.load(tmp_path) == {"a": 1, "b": 0}
    (tmp_path / "quiz-shapes.json").write_text(json.dumps({"revision": "s", "orders": {"a": 0, "b": 1, "c": 2}}))
    assert quiz_shapes.load(tmp_path)["c"] == 2


def test_crops_of_one_shape_are_ordered_together():
    pytest.importorskip("torch")
    rng = np.random.default_rng(0)
    one, other = np.eye(16)[0], np.eye(16)[1]
    vectors = np.stack([one + 0.05 * rng.standard_normal(16) for _ in range(20)]
                       + [other + 0.05 * rng.standard_normal(16) for _ in range(10)])
    shuffled = rng.permutation(30)
    order = quiz_shapes.order_group(vectors[shuffled])
    assert sorted(order) == list(range(30))
    kinds = ["one" if shuffled[i] < 20 else "other" for i in order]
    assert kinds == ["one"] * 20 + ["other"] * 10
