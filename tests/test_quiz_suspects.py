"""Quick review suspects: crops the classifier gives almost no probability under their own label."""
import json

import numpy as np
import pytest

from glyph_atlas.review import quiz_suspects

CLASSES = ["U+30A2", "U+30A4", "U+4EEE", "U+5047", "other"]


def row(**weights):
    """One softmax row over `CLASSES`, from weights named ア, イ, 仮, 假 and other."""
    order = ["ア", "イ", "仮", "假", "other"]
    values = np.array([weights.get(name, 0.0) for name in order])
    return values / values.sum()


@pytest.fixture(scope="module")
def labels():
    return quiz_suspects.Labels(CLASSES)


def test_a_crop_read_as_another_character_names_it(labels):
    assert labels.judge(np.stack([row(ア=.97, イ=.03)]), ["イ"]) == [{"p": 0.03, "reads_as": "ア"}]


def test_enough_probability_on_the_label_is_not_a_suspect(labels):
    assert labels.judge(np.stack([row(ア=.9, イ=.1)]), ["イ"]) == [None]


def test_a_merged_family_counts_as_the_label(labels):
    # 仮 and 假 are one family to the classifier: a 仮 read as 假 is no suspect.
    assert labels.judge(np.stack([row(假=.99, イ=.01)]), ["仮"]) == [None]


def test_a_suspect_without_one_clear_reading_names_none(labels):
    assert labels.judge(np.stack([row(ア=.45, other=.54, イ=.01)]), ["イ"]) == [{"p": 0.01, "reads_as": None}]
    assert labels.judge(np.stack([row(other=.99, イ=.01)]), ["イ"]) == [{"p": 0.01, "reads_as": None}]


def test_an_unfamiliar_kanji_is_a_suspect_only_when_it_reads_as_another_character(labels):
    assert labels.judge(np.stack([row(other=.99, 仮=.01)]), ["仮"]) == [None]
    assert labels.judge(np.stack([row(ア=.99, 仮=.01)]), ["仮"]) == [{"p": 0.01, "reads_as": "ア"}]


def test_a_label_the_classifier_never_learnt_is_never_a_suspect(labels):
    assert labels.judge(np.stack([row(ア=1.)]), ["ヰ"]) == [None]


def test_marks_are_read_from_the_dataset_and_follow_a_new_file(tmp_path):
    assert quiz_suspects.load(tmp_path) == {}
    (tmp_path / "quiz-suspects.json").write_text(json.dumps({"suspects": {"a": {"p": 0.01, "reads_as": "ア"}}}))
    assert quiz_suspects.load(tmp_path) == {"a": {"p": 0.01, "reads_as": "ア"}}
    (tmp_path / "quiz-suspects.json").write_text(json.dumps({"suspects": {"b": {"p": 0.02, "reads_as": None}}}))
    assert list(quiz_suspects.load(tmp_path)) == ["b"]
