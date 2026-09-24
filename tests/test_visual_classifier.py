import json

import numpy as np
import pytest

from glyph_atlas.visual_classifier import VisualClassifier


def classifier(tmp_path, *, named=True):
    (tmp_path / "classifier.json").write_text(json.dumps({
        "version": 1, "model_revision": "one", "centroids": [[1, 0, 0], [0, 1, 0]],
        "radii": [.15, .15], "groups": [
            {"id": "a", "family": "U+4EEE", "written_character": "假" if named else None},
            {"id": "b", "family": "U+4EEE", "written_character": "仮"}]}))
    return VisualClassifier(tmp_path)


def test_supported_visual_name_is_a_proposal(tmp_path):
    result = classifier(tmp_path).predict(np.array([4, .1, 0]), "U+4EEE")
    assert result["written_character"] == "假"
    assert result["verified"] is False
    assert result["status"] == "proposed"
    assert "probability" not in result


def test_far_or_ambiguous_shapes_remain_unassigned(tmp_path):
    model = classifier(tmp_path)
    for vector in ([0, 0, 1], [1, 1, 0]):
        result = model.predict(np.array(vector), "U+4EEE")
        assert result["written_character"] is None
        assert result["within_support"] is False
    assert model.predict(np.array([1, 0, 0]), "U+56FD")["status"] == "not_analyzed"


def test_unanchored_cluster_is_not_a_character_label(tmp_path):
    result = classifier(tmp_path, named=False).predict(np.array([1, 0, 0]), "U+4EEE")
    assert result["group_id"] == "a"
    assert result["within_support"] is True
    assert result["written_character"] is None


def test_invalid_vectors_are_rejected(tmp_path):
    model = classifier(tmp_path)
    for vector in ([0, 0, 0], [1, float("nan"), 0], [1, 0]):
        with pytest.raises(ValueError):
            model.predict(np.array(vector), "U+4EEE")
