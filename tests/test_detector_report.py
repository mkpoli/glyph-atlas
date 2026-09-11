"""Tests for `models/detector/compare_report.py`, the checkpoint comparison's renderer.

The renderer is what turns two `train.py --test` reports into the table a reader quotes, and its first
version was wrong in three ways at once: it read the operating point from `test['overall']['score']`
(the score is `test['score']`, so every row printed `nan`), it formatted the recall deciles as floats
when the report holds dictionaries, and it was never run before being committed. These tests pin the
shapes against a fixture, so the rendering is exercised without a GPU or a checkpoint.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location(
    "compare_report", ROOT / "models" / "detector" / "compare_report.py"
)
assert _spec is not None and _spec.loader is not None
compare_report = importlib.util.module_from_spec(_spec)
sys.modules["compare_report"] = compare_report
_spec.loader.exec_module(compare_report)


def report(*, score: float, precision: float, deciles: int = 3) -> dict:
    """A report in the shape `train.py --test` writes."""
    return {
        "test": {
            "score": score,
            "iou": 0.5,
            "overall": {
                "precision": precision, "recall": 0.9, "f1": 0.8, "mean_iou": 0.87,
                "tp": 1, "fp": 2, "fn": 3,
            },
            "by_production": {
                "woodblock": {"precision": precision, "recall": 0.95, "f1": 0.9},
                "manuscript": {"precision": 0.5, "recall": 0.6, "f1": 0.55},
                "unknown": {"precision": 0.4, "recall": 0.3, "f1": 0.34},
            },
            "recall_by_size_decile": [
                {"decile": index + 1, "max_area": 100.0 * (index + 1), "truth": 10, "hit": index,
                 "recall": 0.8 - 0.1 * index}
                for index in range(deciles)
            ],
        }
    }


def test_the_operating_point_comes_from_the_test_block() -> None:
    """`test['score']` is the chosen threshold; `test['overall']['score']` does not exist."""
    rendered = compare_report.render(report(score=0.02, precision=0.7),
                                     report(score=0.03, precision=0.6), "epoch-00")
    assert "nan" not in rendered, "no measure may fall back to NaN when every field is present"
    assert "0.0300" in rendered and "0.0200" in rendered
    assert "operating point" in rendered


def test_the_deciles_are_read_as_dictionaries() -> None:
    """The recall deciles are dictionaries; formatting one as a float used to raise TypeError."""
    rendered = compare_report.render(report(score=0.02, precision=0.7),
                                     report(score=0.02, precision=0.9), "epoch-03")
    assert "recall by box size decile" in rendered
    assert "0.800" in rendered and "0.600" in rendered


def test_the_deciles_align_by_label_not_by_position() -> None:
    """A report with fewer deciles keeps its numbers under their own labels."""
    left = report(score=0.02, precision=0.7, deciles=3)
    right = report(score=0.02, precision=0.9, deciles=3)
    del right["test"]["recall_by_size_decile"][1]
    rendered = compare_report.render(left, right, "short")
    header = next(line for line in rendered.splitlines() if line.startswith("decile"))
    assert header.split() == ["decile", "1", "2", "3"]


def test_a_missing_measure_renders_as_nan_rather_than_raising() -> None:
    """A report from an interrupted run is readable, so a gap shows instead of a traceback."""
    rendered = compare_report.render({"test": {"score": 0.02, "overall": {"precision": 0.7}}},
                                     report(score=0.02, precision=0.6), "partial")
    assert "nan" in rendered
    assert "0.7000" in rendered


def test_the_arguments_are_a_path_a_path_and_a_label() -> None:
    """`main` takes the two files and the label, and prints the table."""
    assert compare_report.__doc__ is not None
    assert callable(compare_report.main)


@pytest.mark.skipif(
    not (ROOT / "models" / "detector" / "artifacts" / "metrics.json").exists(),
    reason="needs the trained detector's report",
)
def test_the_shipped_report_renders() -> None:
    """The real artifact's report goes through the renderer unchanged."""
    shipped = json.loads(
        (ROOT / "models" / "detector" / "artifacts" / "metrics.json").read_text(encoding="utf-8")
    )
    rendered = compare_report.render(shipped, shipped, "shipped")
    assert "0.7000" in rendered or "0.6998" in rendered
    assert "woodblock recall" in rendered
    assert "recall by box size decile" in rendered
