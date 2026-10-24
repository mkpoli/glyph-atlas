"""The split metrics, worked in pieces, equal the same metrics over a whole softmax."""
import importlib
import sys
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("torch")
sys.path.insert(0, str(Path("models/classifier").resolve()))
train = importlib.import_module("train")


def whole(logits, temperature):
    values = np.asarray(logits, dtype=np.float64) / temperature
    exponent = np.exp(values - values.max(axis=-1, keepdims=True))
    return exponent / exponent.sum(axis=-1, keepdims=True)


@pytest.mark.parametrize("rows, classes, temperature", [(5000, 40, 1.0), (4100, 7, 1.7), (3, 4, 1.0)])
def test_metrics_in_pieces_match_a_whole_softmax(monkeypatch, rows, classes, temperature):
    monkeypatch.setattr(train, "CHUNK", 1000)
    rng = np.random.default_rng(0)
    logits = (rng.normal(size=(rows, classes)) * 3).astype(np.float32)
    labels = rng.integers(0, classes, rows)
    probabilities = whole(logits, temperature)
    report = train.metrics_for(logits, labels, classes=classes, top_k=5, temperature=temperature)
    order = np.argsort(-probabilities, axis=-1, kind="stable")
    assert report["top1"] == pytest.approx(np.mean(order[:, 0] == labels))
    assert report["top5"] == pytest.approx(np.mean((order[:, :5] == labels[:, None]).any(axis=1)))
    assert report["mean_confidence"] == pytest.approx(probabilities.max(axis=-1).mean())
    picked = probabilities[np.arange(rows), labels]
    assert train.negative_log_likelihood(logits, labels, temperature) == pytest.approx(-np.log(picked).mean())


def test_an_empty_split_reports_zeros():
    report = train.metrics_for(np.zeros((0, 3), dtype=np.float32), np.zeros(0, dtype=np.int64), classes=3)
    assert report["top1"] == 0.0 and report["ece"] == 0.0 and report["crops"] == 0


def test_temperature_fitted_in_pieces_matches_a_whole_fit(monkeypatch):
    import torch

    rng = np.random.default_rng(1)
    labels = rng.integers(0, 30, 3000)
    logits = (np.eye(30)[labels] * 4 + rng.normal(size=(3000, 30)) * 2).astype(np.float32)
    temperature = torch.zeros((), dtype=torch.float64, requires_grad=True)
    optimizer = torch.optim.LBFGS([temperature], lr=0.1, max_iter=200, line_search_fn="strong_wolfe")
    values, truth = torch.from_numpy(logits.astype(np.float64)), torch.from_numpy(labels)

    def closure():
        optimizer.zero_grad()
        loss = torch.nn.functional.cross_entropy(values / torch.exp(temperature), truth)
        loss.backward()
        return loss

    optimizer.step(closure)
    monkeypatch.setattr(train, "CHUNK", 700)
    fitted, _ = train.fit_temperature(logits, labels)
    assert fitted == pytest.approx(float(torch.exp(temperature)), rel=1e-6)
