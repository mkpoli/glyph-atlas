#!/usr/bin/env bash
# Measure a detector checkpoint on the val and test splits, in its own output directory.
#
# `train.py --test` writes its report to `--out`, and the default is `config.artifacts.directory` —
# the shipped artifact's own `metrics.json`. Running it without `--out` therefore overwrites the
# baseline this script exists to compare against. Every write here goes to a directory named after
# the checkpoint, and the shipped artifact is checksummed before and after so a run that touches it
# fails loudly.
#
#   models/detector/compare_checkpoints.sh models/detector/artifacts-24e/epoch-01.pt
#
# The measurement runs the same protocol the shipped baseline did: the whole validation split chooses
# the operating point and the whole test split is measured at it. A `--val-limit` would confound the
# comparison, because a different subset picks a different threshold, so this script does not offer
# one. Use `train.py --test --val-limit N --out <scratch>` for a labelled smoke test instead.
set -euo pipefail

cd "$(dirname "$0")/../.."
checkpoint="${1:?usage: compare_checkpoints.sh <checkpoint.pt>}"
name="$(basename "$checkpoint" .pt)"
# The checkpoint's own hash names the output directory, so two runs that both have an `epoch-01.pt`
# cannot write over each other's measurement.
digest="$(sha256sum "$checkpoint" | cut -c1-12)"
evaluation="models/detector/eval-$name-$digest"
shipped="models/detector/artifacts/metrics.json"

if [ ! -f "$checkpoint" ]; then
  echo "$checkpoint does not exist" >&2
  exit 1
fi
if pgrep -f "models/detector/train.py" >/dev/null; then
  echo "train.py is running; stop it before measuring, or the two compete for the GPU" >&2
  exit 1
fi

before="$(sha256sum "$shipped" | cut -d' ' -f1)"
mkdir -p "$evaluation"
echo "== shipped baseline $shipped sha256 $before"
echo "== measuring $checkpoint into $evaluation (whole val and test splits)"

.venv/bin/python -u models/detector/train.py --test --weights "$checkpoint" \
  --config models/detector/config.yaml --out "$evaluation" 2>&1 | tail -30

after="$(sha256sum "$shipped" | cut -d' ' -f1)"
if [ "$before" != "$after" ]; then
  echo "the shipped baseline changed during the measurement: $before -> $after" >&2
  exit 1
fi
echo "== shipped baseline unchanged"

.venv/bin/python models/detector/compare_report.py "$evaluation/metrics.json" "$shipped" "$name"
