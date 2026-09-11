"""Print a checkpoint's test measurement beside the shipped artifact's.

`compare_checkpoints.sh` runs `train.py --test` for one checkpoint into its own directory and hands
the two reports here. The point of the module is that the rendering is testable without a GPU: the
shapes it reads are the ones `train.py` writes, and `tests/test_detector_report.py` pins them against
a fixture, because the first version of this comparison read `test['overall']['score']` (the score is
`test['score']`, so it printed `nan` for every row) and formatted the recall deciles as floats when
they are dictionaries.

    .venv/bin/python models/detector/compare_report.py measured.json shipped.json label
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any

MEASURES = ("precision", "recall", "f1", "mean_iou")
PRODUCTIONS = ("woodblock", "manuscript", "unknown")


def _number(value: Any) -> float:
    """A JSON number as a float, or NaN when the field is missing."""
    return float(value) if isinstance(value, int | float) else math.nan


def recall_by_decile(report: dict[str, Any]) -> dict[str, float]:
    """The recall of each size decile, keyed by the decile's own label.

    `train.py` writes a list of dictionaries with `decile`, `max_area`, `truth`, `hit` and `recall`.
    Keying by the label is what lets a report with a missing decile line up with one that has it, so a
    value never shifts into the wrong column.
    """
    rows = report.get("recall_by_size_decile") or []
    result: dict[str, float] = {}
    for index, entry in enumerate(rows):
        if not isinstance(entry, dict):
            result[str(index)] = math.nan
            continue
        result[str(entry.get("decile", index))] = _number(entry.get("recall"))
    return result


def _decile_labels(left: dict[str, float], right: dict[str, float]) -> list[str]:
    """Every decile label either report names, in decile order.

    Sorted by number rather than by the order the labels were met, so a report missing decile 2 keeps
    3 under its own column instead of sliding left.
    """
    labels = set(left) | set(right)
    return sorted(labels, key=lambda name: (0, int(name)) if name.lstrip("-").isdigit() else (1, name))


def render(measured: dict[str, Any], shipped: dict[str, Any], label: str) -> str:
    """The comparison, as the lines a report can quote."""
    left = measured.get("test") or {}
    right = shipped.get("test") or {}
    out = [
        f"measured {label}, chosen on val, test split at IoU {left.get('iou', float('nan'))}",
        f"{'':<24}{'shipped':>12}{label:>14}",
        f"{'operating point':<24}{_number(right.get('score')):>12.4f}{_number(left.get('score')):>14.4f}",
    ]
    for key in MEASURES:
        out.append(
            f"{key:<24}{_number((right.get('overall') or {}).get(key)):>12.4f}"
            f"{_number((left.get('overall') or {}).get(key)):>14.4f}"
        )
    for production in PRODUCTIONS:
        for key in ("precision", "recall"):
            row = f"{production} {key}"
            out.append(
                f"{row:<24}{_number(((right.get('by_production') or {}).get(production) or {}).get(key)):>12.4f}"
                f"{_number(((left.get('by_production') or {}).get(production) or {}).get(key)):>14.4f}"
            )
    shipped_deciles = recall_by_decile(right)
    measured_deciles = recall_by_decile(left)
    if shipped_deciles or measured_deciles:
        labels = _decile_labels(shipped_deciles, measured_deciles)
        out.append("")
        out.append("recall by box size decile, small to large")
        out.append(f"{'decile':<10}" + "".join(f"{name:>8}" for name in labels))
        out.append(f"{'shipped':<10}"
                   + "".join(f"{shipped_deciles.get(name, math.nan):>8.3f}" for name in labels))
        out.append(f"{label:<10}"
                   + "".join(f"{measured_deciles.get(name, math.nan):>8.3f}" for name in labels))
    return "\n".join(out)


def main() -> int:
    if len(sys.argv) != 4:
        raise SystemExit(__doc__)
    measured, shipped, label = sys.argv[1], sys.argv[2], sys.argv[3]
    print(render(json.loads(Path(measured).read_text(encoding="utf-8")),
                 json.loads(Path(shipped).read_text(encoding="utf-8")), label))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
