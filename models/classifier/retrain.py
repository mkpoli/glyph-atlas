"""Retrain the classifier, and install it only where it reads every held-out set at least as well.

One run does what a retrain needs, in order:

1. `build_combined.py` writes the manifests and the class list (skip with `--skip-manifests`).
2. `train.py` trains into `work/classifier-runs/<stamp>/`.
3. `export_onnx.py` exports it with a parity check, beside its class list.
4. `models/benchmark/bench.py` reads `codh-test`, `hilab-test` and `atlas-reviewed` with the
   candidate and with the served export.
5. The gate compares them: on every set, and on the crops people corrected, the candidate's top-1
   and top-5 may fall below the served model's by at most one crop of that set (or 0.2 points,
   whichever is more). `gate.json` beside the run holds every number and the verdict.

With `--install` and a passing gate, the served files in `models/classifier/artifacts/` move to a
folder named after their backbone and the day, and the candidate takes their place. The look-alike
pairs (`atlas review lookalikes`) and the published crops' suggestions are measured per checkpoint,
so the run ends by naming those steps; it does not publish anything.

    python models/classifier/retrain.py                    # train, measure, report
    python models/classifier/retrain.py --install          # and install when the gate passes
    python models/classifier/retrain.py --candidate work/classifier-runs/<stamp> --install
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = ROOT / "models/classifier/artifacts"
CONFIG = ROOT / "models/classifier/config.yaml"
SETS = ("codh-test", "hilab-test", "atlas-reviewed")
#: The files a served classifier consists of; the rest of `artifacts/` belongs to older runs.
SERVED = ("classifier.onnx", "classes.json", "best.pt", "metrics.json", "train.log", "export.log",
          "lookalikes.json")
#: The least a comparison allows a candidate to lose, in top-1 or top-5 share.
TOLERANCE = 0.002
#: `bench.score` rounds shares to four decimals; two rounded shares differ by up to this much more.
ROUNDING = 1e-4


def step(*arguments: str | Path, log: Path | None = None) -> None:
    """Run one script of the pipeline; with `log`, its output goes to that file."""
    print("$", " ".join(str(a) for a in arguments), *(["  >", str(log)] if log else []), flush=True)
    if log is None:
        subprocess.run([sys.executable, *map(str, arguments)], cwd=ROOT, check=True)
        return
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("w") as handle:
        subprocess.run([sys.executable, *map(str, arguments)], cwd=ROOT, check=True,
                       stdout=handle, stderr=subprocess.STDOUT)


def train(stamp: str, *, skip_manifests: bool, epochs: int | None) -> Path:
    run = ROOT / "work/classifier-runs" / stamp
    if not skip_manifests:
        step("models/classifier/build_combined.py")
    step("models/classifier/train.py", "--config", CONFIG, "--out", run,
         *(["--epochs", str(epochs)] if epochs else []), log=run / "train.log")
    step("models/classifier/export_onnx.py", "--config", CONFIG, "--checkpoint", run / "best.pt",
         "--out", run / "classifier.onnx", "--parity", "200", log=run / "export.log")
    shutil.copy2(ROOT / "models/classifier/classes.json", run / "classes.json")
    return run


def measure(candidate: Path) -> dict:
    sys.path.insert(0, str(ROOT / "models/benchmark"))
    import bench

    name = f"onnx:{candidate / 'classifier.onnx'}"
    reports = {}
    for set_name in SETS:
        report = bench.run(set_name, ["atlas", name], quiet=True)
        reports[set_name] = {"served": report["models"]["atlas"]["scores"],
                             "candidate": report["models"][name]["scores"]}
    return reports


def gate(reports: dict) -> dict:
    """Every set and the corrected crops: the candidate loses no more than the tolerance allows.

    A group measured for one model and not the other fails, since nothing compared it. The shares
    are rounded to four decimals, so the allowance carries the rounding of both sides.
    """
    checks, passed = [], True
    for set_name, report in reports.items():
        for group in ("all", "corrected"):
            served, candidate = report["served"].get(group), report["candidate"].get(group)
            if served is None and candidate is None:
                continue
            if served is None or candidate is None or served["n"] != candidate["n"]:
                passed = False
                checks.append({"set": set_name, "group": group, "passed": False,
                               "reason": "measured on different crops"})
                continue
            allowed = max(TOLERANCE, 1 / served["n"]) + ROUNDING
            for metric in ("top1", "top5"):
                ok = candidate[metric] >= served[metric] - allowed
                passed &= ok
                checks.append({"set": set_name, "group": group, "metric": metric, "crops": served["n"],
                               "served": served[metric], "candidate": candidate[metric],
                               "allowed_loss": round(allowed, 5), "passed": ok})
    return {"passed": passed, "checks": checks}


def backbone_of(checkpoint: Path) -> str:
    import torch

    return torch.load(checkpoint, map_location="cpu", weights_only=False)["config"]["model"]["checkpoint"]


def install(candidate: Path, artifacts: Path, retired: str) -> Path:
    """Move the served files to `artifacts/<retired>/` and put the candidate's in their place.

    The candidate is copied into the artifacts folder first, so the swap itself is renames on one
    filesystem, and an error in it puts the served files back.
    """
    missing = [name for name in ("classifier.onnx", "classes.json", "best.pt") if not (candidate / name).exists()]
    if missing:
        raise SystemExit(f"{candidate} lacks {', '.join(missing)}; nothing was installed")
    folder, staging = artifacts / retired, artifacts / f".{retired}.incoming"
    if folder.exists():
        raise SystemExit(f"{folder} already exists; nothing was installed")
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    for name in SERVED:
        if (candidate / name).exists():
            shutil.copy2(candidate / name, staging / name)
    folder.mkdir()
    moved, placed = [], []
    try:
        for name in SERVED:
            if (artifacts / name).exists():
                (artifacts / name).rename(folder / name)
                moved.append(name)
        for name in SERVED:
            if (staging / name).exists():
                (staging / name).rename(artifacts / name)
                placed.append(name)
    except BaseException:
        for name in placed:
            (artifacts / name).rename(staging / name)
        for name in moved:
            (folder / name).rename(artifacts / name)
        folder.rmdir()
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return folder


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--candidate", type=Path, help="a finished run to measure instead of training one")
    parser.add_argument("--skip-manifests", action="store_true")
    parser.add_argument("--epochs", type=int)
    parser.add_argument("--install", action="store_true", help="install the candidate when the gate passes")
    args = parser.parse_args()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    candidate = args.candidate or train(stamp, skip_manifests=args.skip_manifests, epochs=args.epochs)
    if not (candidate / "classes.json").exists():
        raise SystemExit(f"{candidate} has no classes.json beside its export; the served list would be read instead")
    result = gate(measure(candidate))
    (candidate / "gate.json").write_text(json.dumps(result, indent=1) + "\n")
    for check in result["checks"]:
        mark = "ok  " if check["passed"] else "LOSS"
        print(f"{mark} {check['set']:<15}{check['group']:<10}{check['metric']:<5} "
              f"served {check['served']:.4f}  candidate {check['candidate']:.4f}")
    if not result["passed"]:
        raise SystemExit(f"the gate failed; nothing installed ({candidate / 'gate.json'})")
    if args.install:
        retired = f"{backbone_of(ARTIFACTS / 'best.pt')}-{datetime.now(UTC):%Y-%m-%d}"
        folder = install(candidate, ARTIFACTS, retired)
        print(f"installed {candidate}; the previous files are in {folder}")
        print("next: `atlas review lookalikes`, then re-read the published crops' suggestions. Alignment runs\n"
              "that pin the previous export by `classifier_sha256` refuse the new one; aligning with it takes a new run.")


if __name__ == "__main__":
    main()
