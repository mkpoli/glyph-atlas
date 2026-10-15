"""The training sampler balances classes, and keeps font-rendered crops to their configured share."""
import importlib
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path("models/classifier").resolve()))
train = importlib.import_module("train")


def data(labels, rendered):
    return train.Data(
        classes=[f"c{n}" for n in range(max(labels) + 1)],
        labels=np.asarray(labels, dtype=np.int32),
        rendered=np.asarray(rendered, dtype=bool),
    )


def draw_shares(sampler, split):
    weights = sampler.weights.numpy()
    total = weights.sum()
    return {label: weights[split.labels == label].sum() / total for label in np.unique(split.labels)}


def test_without_a_share_every_class_is_drawn_alike():
    split = data([0, 0, 0, 1, 2, 2], [False, False, False, False, True, True])
    shares = draw_shares(train.balanced_sampler(split, seed=0), split)
    assert shares == pytest.approx({0: 1 / 3, 1: 1 / 3, 2: 1 / 3})


def test_rendered_crops_take_their_share_and_balance_among_themselves():
    # two scanned classes and three rendered ones: the rendered classes would take 3/5 unweighted
    split = data([0, 0, 1, 2, 3, 3, 3, 4], [False, False, False, True, True, True, True, True])
    sampler = train.balanced_sampler(split, seed=0, rendered_share=0.1)
    shares = draw_shares(sampler, split)
    assert shares[0] == pytest.approx(0.45) and shares[1] == pytest.approx(0.45)
    assert shares[2] == pytest.approx(0.1 / 3) and shares[3] == pytest.approx(0.1 / 3)
    assert sampler.num_samples == len(split)


def test_a_split_with_no_rendered_crops_ignores_the_share():
    split = data([0, 1, 1], [False, False, False])
    shares = draw_shares(train.balanced_sampler(split, seed=0, rendered_share=0.1), split)
    assert shares == pytest.approx({0: 0.5, 1: 0.5})


def test_take_and_concat_keep_the_rendered_flags():
    split = data([0, 1, 2], [False, True, False])
    split.paths, split.offsets = b"abc", np.arange(4, dtype=np.int64)
    assert split.take(np.asarray([1, 2])).rendered.tolist() == [True, False]
    assert split.concat(split).rendered.tolist() == [False, True, False, False, True, False]


def test_a_manifest_marks_crops_rendered_from_fonts(tmp_path):
    from build_manifests import Crop

    from glyph_atlas import tables

    def crop(unit, document):
        return Crop(unit_id=unit, crop=f"{unit}.png", label="c0", code_point="c0", document_id=document,
                    page_id="p", split="train")

    path = tmp_path / "train.parquet"
    tables.write(path, [crop("scan", "codh:200003076"), crop("font", "syn:hangul:Batang")], Crop)
    assert train.read_manifest(path, ["c0"]).rendered.tolist() == [False, True]
