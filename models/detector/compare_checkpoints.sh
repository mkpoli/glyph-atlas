#!/usr/bin/env bash
# Measure a detector checkpoint on the test split and print it beside the shipped artifact.
#
# The card's numbers come from `train.py --test`, which reads the val and test splits, chooses the
# operating point on val and reports the whole-page measurement at IoU 0.5. This script runs it once
# for a checkpoint, keeps the JSON it wrote, and prints the four headline measures next to the shipped
# 6-epoch artifact's, so a reader sees whether more epochs moved anything.
#
#   models/detector/compare_checkpoints.sh models/detector/artifacts-24e/epoch-03.pt
#
# Run it when nothing else is training: it needs the GPU and takes about ten minutes. It writes into
# the checkpoint's own directory, which is where `train.py` keeps its per-epoch curve too.
set -euo pipefail

cd "$(dirname "$0")/../.."
checkpoint="${1:?usage: compare_checkpoints.sh <checkpoint.pt>}"
name="$(basename "$checkpoint" .pt)"
directory="$(dirname "$checkpoint")"

echo "== measuring $checkpoint on val and test"
.venv/bin/python -u models/detector/train.py --test --weights "$checkpoint" \
  --config models/detector/config.yaml 2>&1 | tail -30

if [ ! -f "$directory/metrics.json" ]; then
  echo "$directory/metrics.json was not written" >&2
  exit 1
fi
cp "$directory/metrics.json" "$directory/metrics-$name.json"

echo
.venv/bin/python - "$directory/metrics-$name.json" "$name" <<'PY'
import json
import sys
from pathlib import Path

measured = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))["test"]
shipped = json.loads(Path("models/detector/artifacts/metrics.json").read_text(encoding="utf-8"))["test"]
label = sys.argv[2]

print(f"{'whole test split':<24}{'shipped 6e':>12}{label:>14}")
for key in ("score", "precision", "recall", "f1", "mean_iou"):
    print(f"{key:<24}{shipped['overall'].get(key, float('nan')):>12.4f}"
          f"{measured['overall'].get(key, float('nan')):>14.4f}")
for production in ("woodblock", "manuscript", "unknown"):
    left = shipped["by_production"].get(production, {})
    right = measured["by_production"].get(production, {})
    for key in ("precision", "recall"):
        print(f"{production + ' ' + key:<24}{left.get(key, float('nan')):>12.4f}"
              f"{right.get(key, float('nan')):>14.4f}")
print()
print("recall by box size decile, small to large")
left = shipped["recall_by_size_decile"]
right = measured["recall_by_size_decile"]
print(f"{'decile':<8}" + "".join(f"{index:>7}" for index in range(len(right))))
print(f"{'shipped':<8}" + "".join(f"{value:>7.3f}" for value in left))
print(f"{label:<8}" + "".join(f"{value:>7.3f}" for value in right))
PY
