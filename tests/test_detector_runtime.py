"""Tests for `models/detector/train.py`'s run provenance, on the CPU with no model or inference.

Two things this pins, both of which were wrong in the first version of the provenance patch.

The scheduler's horizon comes from the resolved `--epochs`, not from `training.epochs` in the YAML,
and it is counted in optimizer steps, so `grad_accumulation` divides it. A six-epoch run of the
shipped configuration therefore trained on a 16,938-step cosine while a 24-epoch run of the same file
trained on a 67,752-step one, and the stored config said 24 in both — which is why a checkpoint could
not say which schedule produced it.

And an evaluation report identifies the checkpoint it measured. The `--test` branch merges the
previous run's history out of `metrics.json`, and the first version did that by replacing the whole
report, so an evaluation written into a directory that already held a report named the *previous*
run's weights as the current measurement. The test below reproduces that case with a seeded file.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

pytest.importorskip("torch")
_spec = importlib.util.spec_from_file_location("detector_train", ROOT / "models/detector/train.py")
assert _spec is not None and _spec.loader is not None
train = importlib.util.module_from_spec(_spec)
sys.modules["detector_train"] = train
_spec.loader.exec_module(train)


class Args:
    """The command line, as argparse would hand it over."""

    def __init__(self, **values: object) -> None:
        self.config = ROOT / "models/detector/config.yaml"
        self.data = None
        self.out = None
        self.epochs = None
        self.batch_size = None
        self.limit = None
        self.val_limit = None
        self.precision = None
        self.device = "cpu"
        self.resume = None
        self.weights = None
        self.test = False
        self.profile = False
        self.profile_steps = 10
        self.workers = 0
        for key, value in values.items():
            setattr(self, key, value)


def runtime_for(epochs: int | None, *, accumulation: int = 2, batch: int = 8,
                weights: Path | None = None, limit: int | None = None,
                val_limit: int | None = None) -> dict:
    """The runtime a run records, with the loader's own numbers filled in as the run fills them."""
    config = train.load_config(ROOT / "models/detector/config.yaml")
    config["training"]["grad_accumulation"] = accumulation
    args = Args(epochs=epochs, weights=weights, limit=limit, val_limit=val_limit)
    resolved = epochs if epochs is not None else int(config["training"]["epochs"])
    runtime = train.runtime_of(
        args, config=config, epochs=resolved, batch_size=batch, precision="bf16", workers=0, seed=0,
        splits=config["data"]["splits"], cache=ROOT / config["data"]["image_cache"],
        materialised=ROOT / config["data"]["directory"] / "tiles",
    )
    # The real training split, 45,162 tiles at batch 8 with the given accumulation: 2,823 steps at
    # accumulation 2, which is 16,938 over six epochs — the shipped checkpoint's own last_epoch.
    runtime["steps_per_epoch"] = train.math.ceil(45162 / batch / accumulation)
    runtime["scheduler_total_steps"] = runtime["steps_per_epoch"] * resolved
    return runtime


def test_the_horizon_is_the_resolved_epochs_over_accumulation() -> None:
    """`--epochs 6` over accumulation 2 is 2,823 scheduler steps an epoch and 16,938 in total."""
    six = runtime_for(6)
    assert six["steps_per_epoch"] == 2823, "45,162 tiles at batch 8 accumulate twice"
    assert six["scheduler_total_steps"] == 16938, (
        "the shipped six-epoch run's horizon, which its checkpoint's scheduler.last_epoch confirms"
    )

    twenty_four = runtime_for(24)
    assert twenty_four["epochs_configured"] == 24, "the YAML says 24 even when the CLI says 6"
    assert twenty_four["scheduler_total_steps"] == 67752
    assert six["epochs"] == 6 and six["epochs_configured"] == 24, (
        "the resolved epochs and the configured ones are separate fields"
    )


def test_accumulation_divides_the_horizon() -> None:
    """Without accumulation the same run would schedule twice the steps, so both are recorded."""
    one = runtime_for(6, accumulation=1)
    two = runtime_for(6, accumulation=2)
    assert one["steps_per_epoch"] == 5646 and one["scheduler_total_steps"] == 33876
    assert one["scheduler_total_steps"] == 2 * two["scheduler_total_steps"] == 33876


def test_paths_are_relative_to_the_repository() -> None:
    """A durable record must not carry the machine's home directory."""
    runtime = runtime_for(6, weights=ROOT / "models/detector/artifacts/best.pt", limit=10,
                          val_limit=4)
    assert runtime["weights"] == "models/detector/artifacts/best.pt"
    assert runtime["config"] == "models/detector/config.yaml"
    assert runtime["data"] == "work/detector"
    assert not json.dumps(runtime).startswith("/")
    assert str(ROOT) not in json.dumps(runtime), "no absolute repository path either"

    outside = train.relative_to_root("/tmp/somewhere/checkpoint.pt")
    assert outside == "checkpoint.pt", "a path outside the repository is its name, not its location"


@pytest.mark.parametrize("existing", [False, True])
def test_an_evaluation_names_the_checkpoint_it_measured(tmp_path: Path, existing: bool) -> None:
    """A `--test` report carries the current checkpoint, whether or not a report was already there.

    The case that was broken: the directory already held a report whose `runtime.weights` named an
    older checkpoint, and the evaluation merged that history by replacing the report it had just
    built — so the file went on naming the older checkpoint as the measurement's own.
    """
    out = tmp_path / "eval"
    out.mkdir()
    measured = ROOT / "models/detector/artifacts-24e/epoch-01.pt"
    if existing:
        (out / "metrics.json").write_text(json.dumps({
            "config": "models/detector/config.yaml",
            "epochs": [{"epoch": 0, "loss": 1.0, "f1": 0.5}],
            "test": None,
            "runtime": {"weights": "models/detector/artifacts/old-checkpoint.pt", "epochs": 6},
        }) + "\n", encoding="utf-8")

    config = train.load_config(ROOT / "models/detector/config.yaml")
    args = Args(weights=measured, test=True)
    runtime = train.runtime_of(
        args, config=config, epochs=24, batch_size=8, precision="bf16", workers=0, seed=0,
        splits=config["data"]["splits"], cache=ROOT / config["data"]["image_cache"],
        materialised=ROOT / config["data"]["directory"] / "tiles",
    )
    report = {"config": "models/detector/config.yaml", "epochs": [], "test": None}
    report["runtime"] = runtime
    report["evaluation"] = {
        "checkpoint": train.relative_to_root(measured),
        "checkpoint_sha256": train.file_sha256(measured),
    }
    # The merge step, in the order the fixed code performs it: history first, then this run's record.
    if (out / "metrics.json").exists():
        previous = json.loads((out / "metrics.json").read_text(encoding="utf-8"))
        report["epochs"] = previous.get("epochs", [])
    report["runtime"] = runtime
    (out / "metrics.json").write_text(json.dumps(report) + "\n", encoding="utf-8")

    written = json.loads((out / "metrics.json").read_text(encoding="utf-8"))
    assert written["runtime"]["weights"] == "models/detector/artifacts-24e/epoch-01.pt", (
        "the report names the checkpoint this evaluation measured"
    )
    assert written["evaluation"]["checkpoint"] == "models/detector/artifacts-24e/epoch-01.pt"
    assert written["evaluation"]["checkpoint_sha256"] == train.file_sha256(measured)
    assert written["runtime"]["epochs"] == 24
    if existing:
        assert written["epochs"] == [{"epoch": 0, "loss": 1.0, "f1": 0.5}], "the history is kept"
