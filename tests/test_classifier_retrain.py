"""The retrain gate installs a candidate only when it reads every held-out set at least as well."""
import importlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path("models/classifier").resolve()))
retrain = importlib.import_module("retrain")


def scores(top1, top5, n=1000, corrected=None):
    groups = {"all": {"n": n, "top1": top1, "top5": top5}}
    if corrected:
        groups["corrected"] = {"n": corrected[2], "top1": corrected[0], "top5": corrected[1]}
    return groups


def test_a_better_candidate_passes():
    reports = {"codh-test": {"served": scores(.88, .92), "candidate": scores(.92, .97)}}
    assert retrain.gate(reports)["passed"]


def test_a_loss_on_any_set_fails():
    reports = {"codh-test": {"served": scores(.88, .92), "candidate": scores(.92, .97)},
               "hilab-test": {"served": scores(.80, .90), "candidate": scores(.79, .95)}}
    result = retrain.gate(reports)
    assert not result["passed"]
    assert [c["set"] for c in result["checks"] if not c["passed"]] == ["hilab-test"]


def test_a_small_set_allows_one_crop_and_no_more():
    served = scores(.90, .95, n=100, corrected=(.60, .80, 50))
    one = scores(.90, .95, n=100, corrected=(.58, .80, 50))
    two = scores(.90, .95, n=100, corrected=(.56, .80, 50))
    assert retrain.gate({"atlas-reviewed": {"served": served, "candidate": one}})["passed"]
    assert not retrain.gate({"atlas-reviewed": {"served": served, "candidate": two}})["passed"]


def test_install_moves_the_served_files_aside(tmp_path):
    artifacts, candidate = tmp_path / "artifacts", tmp_path / "run"
    artifacts.mkdir()
    candidate.mkdir()
    for name in ("classifier.onnx", "classes.json", "best.pt", "lookalikes.json"):
        (artifacts / name).write_text("served " + name)
    for name in ("classifier.onnx", "classes.json", "best.pt"):
        (candidate / name).write_text("candidate " + name)
    folder = retrain.install(candidate, artifacts, "old-2026-09-26")
    assert (folder / "classifier.onnx").read_text() == "served classifier.onnx"
    assert (artifacts / "classifier.onnx").read_text() == "candidate classifier.onnx"
    # Look-alikes are measured per checkpoint, so the old pairs leave with the old model.
    assert not (artifacts / "lookalikes.json").exists() and (folder / "lookalikes.json").exists()
    with pytest.raises(SystemExit):
        retrain.install(candidate, artifacts, "old-2026-09-26")
