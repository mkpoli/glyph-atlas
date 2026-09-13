"""Are the atlas's character boxes attached to the characters they name?

The aligner matches a line's tokens to the line's detections with a monotone dynamic program: it walks
both sequences forward together, so the *order* of the detections decides which character gets which
box. `containers_of` orders them with

    sorted(inside, key=lambda d: (-d.centre[0], d.centre[1]))

which is meant to be columns right to left, and inside a column top to bottom. It sorts on the raw
float x centre, so two detections whose x centres differ by a pixel are ordered by that difference
rather than by y. On these manuscripts most of a line's detections sit within a few pixels of the same
x, so the tie is the common case and the second key almost never decides anything.

The detector's own output is not in reading order either, so the detections reach the DP in an order
that is neither. The DP is faithful to what it is given, and the result is that the characters of a line
are paired with that line's detections in an essentially arbitrary order.

Read-only, and it reads the cached detections rather than running the detector:

    .venv/bin/python scripts/ainu_reading_order.py [--dataset work/ainu-records]
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from itertools import pairwise
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from kuzushiji_atlas import tables
from kuzushiji_atlas.schema import Box, Line, Page, Unit

#: How wide a column is, as a share of the page's median character width, when x is bucketed.
BUCKET_SHARES = (0.5, 1.0, 2.0)


def load(dataset: Path) -> tuple[dict[str, Line], list[Unit], dict[str, list[Box]], dict[str, Page]]:
    lines = {line.id: line for line in tables.read(dataset / "lines.parquet", Line)}
    units = [unit for unit in tables.read(dataset / "units.parquet", Unit) if unit.active]
    pages = {page.id: page for page in tables.read(dataset / "pages.parquet", Page)}
    detections: dict[str, list[Box]] = {}
    with open(dataset / "detections.jsonl", encoding="utf-8") as handle:
        for row in handle:
            record = json.loads(row)
            if "page_id" in record:
                detections[record["page_id"]] = [Box(**box) for box in record["boxes"]]
    return lines, units, detections, pages


def monotone_fraction(units: list[Unit]) -> tuple[int, int, list[tuple[str, int, int]]]:
    """How many lines have their units' boxes in the order the units claim to be read in."""
    by_line: dict[str, list[Unit]] = defaultdict(list)
    for unit in units:
        if unit.box is not None and unit.line_id:
            by_line[unit.line_id].append(unit)
    good = bad = 0
    worst: list[tuple[str, int, int]] = []
    for line_id, group in by_line.items():
        if len(group) < 2:
            continue
        ordered = sorted(group, key=lambda unit: (unit.seq is None, unit.seq))
        centres = [unit.box.y + unit.box.h / 2 for unit in ordered]
        steps = [b - a for a, b in pairwise(centres)]
        backtracks = sum(1 for step in steps if step < 0)
        if backtracks:
            bad += 1
            worst.append((line_id, backtracks, len(steps)))
        else:
            good += 1
    worst.sort(key=lambda item: -item[1])
    return good, bad, worst


def column_bucketed(boxes: list[Box], width: float, share: float) -> list[Box]:
    """The same detections ordered by column, then by y inside the column."""
    buckets: dict[int, list[Box]] = defaultdict(list)
    for box in boxes:
        buckets[round((box.x + box.w / 2) / (width * share))].append(box)
    return [box for key in sorted(buckets, reverse=True)
            for box in sorted(buckets[key], key=lambda b: b.y + b.h / 2)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=Path("work/ainu-records"))
    parser.add_argument("--page", default=None, help="a page to show in detail")
    args = parser.parse_args()

    lines, units, detections, _pages = load(args.dataset)
    boxed = [unit for unit in units if unit.box is not None]
    good, bad, worst = monotone_fraction(units)
    total = good + bad
    print(f"{len(units)} active units, {len(boxed)} of them with a box")
    print()
    print("along each line, do the boxes advance the way the units claim to be read?")
    print(f"  strictly advancing: {good} of {total} lines")
    print(f"  with a backtrack:   {bad} of {total} lines")
    print()
    print("the worst lines:")
    for line_id, backtracks, steps in worst[:5]:
        print(f"  {line_id[-24:]}  {backtracks}/{steps} steps go back up")
    print()

    page_id = args.page
    if page_id is None:
        page_id = next(iter(sorted(detections)), None)
    if page_id is None:
        return 0
    boxes = detections.get(page_id, [])
    if not boxes:
        print(f"no cached detections for {page_id}")
        return 0
    width = statistics.median(box.w for box in boxes)
    print(f"{page_id}: {len(boxes)} detections, median character width {width:.0f} px")
    print("  the detector's own order, first ten x and y centres:")
    print(f"    x {[round(box.x + box.w / 2) for box in boxes[:10]]}")
    print(f"    y {[round(box.y + box.h / 2) for box in boxes[:10]]}")
    print()
    print("  on one line of that page, the order the detections arrive in:")
    for line in sorted((l for l in lines.values() if l.page_id == page_id), key=lambda l: l.seq or 0):
        if line.box is None:
            continue
        inside = [box for box in boxes
                  if line.box.x <= box.x + box.w / 2 <= line.box.x + line.box.w
                  and line.box.y <= box.y + box.h / 2 <= line.box.y + line.box.h]
        if len(inside) < 4:
            continue
        current = sorted(inside, key=lambda b: (-(b.x + b.w / 2), b.y + b.h / 2))
        ys = [box.y + box.h / 2 for box in current]
        print(f"    line {line.id[-6:]}: {len(inside)} detections")
        print(f"      today, sorted by (-x, y): increasing? {ys == sorted(ys)}")
        print(f"        y centres {[round(v) for v in ys[:12]]}")
        best = None
        for share in BUCKET_SHARES:
            ordered = column_bucketed(inside, width, share)
            ordered_ys = [box.y + box.h / 2 for box in ordered]
            if ordered_ys == sorted(ordered_ys):
                best = (share, ordered_ys)
                break
        if best:
            print(f"      ordered by column then y: increasing? True "
                  f"(any bucket from {best[0]} character widths up)")
            print(f"        y centres {[round(v) for v in best[1][:12]]}")
        else:
            print("      ordered by column then y: still not increasing")
        break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
