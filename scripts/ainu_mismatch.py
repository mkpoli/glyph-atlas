"""Why the アイヌ関連資料 derivation finds more ink columns than a page has transcribed lines.

The census (`ainu_columns.py`) reports that of the 360 body pages whose counts differ, 347 show more
ink columns than transcribed lines. That is the derivation's largest single reason to refuse a page,
so this asks what the extra columns are, before any rule is written to remove them. Candidate answers
that this script measures, and what each would be worth:

* **stray detections** — a detector run at a score of 0.02 may fire on the page edge, the cradle, the
  colour patch or a spot of dirt, and a lone box is its own "column" by construction. Measured by
  where a thin column sits relative to the block the rest of the page forms.
* **a spread** — the image holds two leaves, and the transcription covers one of them, so no
  column-level rule can make the count agree. Measured by the module's own region split.
* **a repaired count** — the line count is known, so a threshold could be chosen per page as the value
  at which the column count agrees. That always "succeeds" somewhere, so it is measured together with
  how often it needs an implausible threshold and how many pages that already pair it disturbs.

Nothing here writes to the dataset. It reads the cached detections and the tables, so it is fast and
repeatable:

    .venv/bin/python scripts/ainu_mismatch.py [--dataset work/ainu-records]
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from glyph_atlas import ainu, tables
from glyph_atlas.schema import Box, Line, Page

#: A column this size or smaller is a candidate for being a stray rather than a line of text.
THIN = 2
#: How far a thin column has to stand from the block, in median character widths, to be a stray.
STRAY_GAP = 1.0
#: The gaps a page's count is searched over, from strictest to most permissive.
GAPS = (0.5, 0.75, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0, 6.0, 8.0)


def load(dataset: Path) -> tuple[dict[str, Page], dict[str, list[Line]], dict[str, list[Box]]]:
    pages = {page.id: page for page in tables.read(dataset / "pages.parquet", Page)}
    lines: dict[str, list[Line]] = {}
    for line in tables.read(dataset / "lines.parquet", Line):
        lines.setdefault(line.page_id, []).append(line)
    found: dict[str, list[Box]] = {}
    with open(dataset / "detections.jsonl", encoding="utf-8") as handle:
        for row in handle:
            record = json.loads(row)
            if "page_id" in record:
                found[record["page_id"]] = [Box(**box) for box in record["boxes"]]
    return pages, lines, found


def body_pages(pages: dict[str, Page], lines: dict[str, list[Line]],
               found: dict[str, list[Box]]) -> list[tuple[str, list[Box], list[Line]]]:
    """The pages that carry a body transcription, which is the population every count is about."""
    out = []
    for page_id, boxes in found.items():
        page = pages.get(page_id)
        if page is None or not boxes:
            continue
        text_lines = ainu.transcribed_lines(lines.get(page_id, []))
        if len(text_lines) < ainu.BODY_LINES:
            continue
        out.append((page_id, boxes, text_lines))
    return out


def drop_thin_far(boxes: list[Box], columns: list[list[int]], gap: float) -> tuple[list[list[int]], int]:
    """The candidate repair: remove columns of `THIN` detections or fewer that stand clear of the block.

    The block is the heavier half of the page's columns, so it is defined by the page rather than by a
    fixed position. Nothing here is part of the derivation; this is the rule being measured.
    """
    if len(columns) < 3:
        return columns, 0
    width = statistics.median(box.w for box in boxes)
    ranked = sorted(columns, key=len, reverse=True)
    centres = [statistics.mean(boxes[index].x + boxes[index].w / 2 for index in column)
               for column in ranked[: max(3, len(ranked) // 2)]]
    kept: list[list[int]] = []
    dropped = 0
    for column in columns:
        middle = statistics.mean(boxes[index].x + boxes[index].w / 2 for index in column)
        far = min(abs(middle - centre) for centre in centres) > width * gap
        if len(column) <= THIN and far:
            dropped += 1
            continue
        kept.append(column)
    return kept, dropped


def paired_with(boxes: list[Box], columns: list[list[int]], text_lines: list[Line]) -> float | None:
    """The weakest per-column evidence if these columns would pair, else None."""
    if len(columns) != len(text_lines):
        return None
    derivation = ainu.Derivation(page_id="", columns=columns, boxes=list(boxes))
    derivation.pairing = text_lines
    derivation.spans = [[index] for index in range(len(columns))]
    derivation.evidence = ainu.evidence_per_column(derivation)
    weakest = min(derivation.evidence)
    return weakest if weakest >= ainu.MIN_DETECTIONS_PER_CHARACTER else None


def stray_shape(population: list[tuple[str, list[Box], list[Line]]]) -> None:
    """Where thin columns sit, which is what decides whether they are strays or missed text."""
    distances, heights, offsets = [], [], Counter()
    for _page_id, boxes, text_lines in population:
        columns = ainu.columns_of(boxes)
        if len(columns) <= len(text_lines):
            continue
        width = statistics.median(box.w for box in boxes)
        heavy = sorted(columns, key=len, reverse=True)[: len(text_lines)]
        centres = [statistics.mean(boxes[i].x + boxes[i].w / 2 for i in column) for column in heavy]
        top = min(boxes[i].y for column in heavy for i in column)
        bottom = max(boxes[i].y + boxes[i].h for column in heavy for i in column)
        span = (bottom - top) or 1
        for column in columns:
            if column in heavy or len(column) > THIN:
                continue
            middle = statistics.mean(boxes[i].x + boxes[i].w / 2 for i in column)
            distances.append(min(abs(middle - centre) for centre in centres) / width)
            column_top = min(boxes[i].y for i in column)
            column_bottom = max(boxes[i].y + boxes[i].h for i in column)
            heights.append((column_bottom - column_top) / span)
            centre = (column_top + column_bottom) / 2
            if centre < top:
                offsets["above the text block"] += 1
            elif centre > bottom:
                offsets["below the text block"] += 1
            elif 0 <= (centre - top) / span <= 1:
                offsets["within the block's span"] += 1
            else:
                offsets["beside it"] += 1

    print(f"thin columns on over-counting pages: {len(distances)}")
    if not distances:
        return
    bins = Counter(_bucket(d, ((1, "under 1"), (2, "1 to 2"), (5, "2 to 5")))
                   for d in distances)
    print("  distance to the nearest body column, in median character widths")
    for label in ("under 1", "1 to 2", "2 to 5", "5 or more"):
        count = bins.get(label, 0)
        print(f"    {label:<12}{count:>7}  ({count / len(distances):>4.0%})")
    print(f"    median {statistics.median(distances):.2f}")
    print(f"  height against the text block: median {statistics.median(heights):.2f}, "
          f"under a tenth of it {sum(1 for h in heights if h < 0.1) / len(heights):.0%}")
    print("  where it sits vertically")
    total = sum(offsets.values())
    for label in ("within the block's span", "below the text block", "above the text block",
                  "beside it"):
        count = offsets.get(label, 0)
        print(f"    {label:<26}{count:>7}  ({count / total:>4.0%})")


def _bucket(value: float, edges: tuple[tuple[float, str], ...]) -> str:
    for edge, label in edges:
        if value < edge:
            return label
    return f"{edges[-1][0]} or more"


def repair(population: list[tuple[str, list[Box], list[Line]]]) -> None:
    """What the candidate repair is worth, and what it costs the pages that already work.

    A page pairs today when its count matches *and* every column passes the evidence gate, so the
    cost is counted against those pages rather than against every page whose count happens to match:
    a page that matches but is refused by the gate has no line boxes to disturb.
    """
    working = [(page_id, boxes, text_lines) for page_id, boxes, text_lines in population
               if paired_with(boxes, ainu.columns_of(boxes), text_lines) is not None]
    print(f"pages the derivation pairs today: {len(working)}")
    print("the candidate repair, applied to every page at a fixed gap")
    for gap in (0.5, 1.0, 2.0, 3.0):
        fixed = broke = 0
        for _page_id, boxes, text_lines in population:
            before = ainu.columns_of(boxes)
            after, dropped = drop_thin_far(boxes, before, gap)
            if not dropped:
                continue
            if len(before) != len(text_lines) and paired_with(boxes, after, text_lines) is not None:
                fixed += 1
        for _page_id, boxes, _text_lines in working:
            _after, dropped = drop_thin_far(boxes, ainu.columns_of(boxes), gap)
            broke += 1 if dropped else 0
        print(f"  gap {gap:>3}: {fixed:>4} pages would newly pair, {broke:>3} of the {len(working)} "
              f"that pair today lose a column")

    print()
    print("the candidate repair, applied only where the count is already wrong")
    chosen = Counter()
    newly = refused = 0
    for _page_id, boxes, text_lines in population:
        before = ainu.columns_of(boxes)
        if len(before) == len(text_lines):
            continue
        for gap in GAPS:
            after, _dropped = drop_thin_far(boxes, before, gap)
            if len(after) != len(text_lines):
                continue
            chosen[gap] += 1
            if paired_with(boxes, after, text_lines) is not None:
                newly += 1
            else:
                refused += 1
            break
    print(f"  {newly} would pair, {refused} reach a count match but fail the evidence gate")
    print("  pages that pair today disturbed: 0, because a page whose count is right is not searched")
    print("  the gap each page needed, which is what shows whether the rule is a rule:")
    for gap, count in sorted(chosen.items()):
        print(f"    {gap:>4} character widths{count:>6}")


def spreads(population: list[tuple[str, list[Box], list[Line]]]) -> None:
    """Whether the extra columns are a second leaf the transcription does not cover."""
    table = Counter()
    for _page_id, boxes, text_lines in population:
        columns = ainu.columns_of(boxes)
        regions = [region for region in ainu.regions_of(boxes) if len(region) >= 40]
        verdict = ("more columns than lines" if len(columns) > len(text_lines)
                   else "fewer" if len(columns) < len(text_lines) else "exact")
        table[(verdict, len(regions) >= 2)] += 1
    print("pages against the number of heavy ink regions the page splits into")
    print(f"  {'':<26}{'one block':>12}{'two or more':>14}")
    for verdict in ("more columns than lines", "fewer", "exact"):
        print(f"  {verdict:<26}{table[(verdict, False)]:>12}{table[(verdict, True)]:>14}")

    matched = 0
    mismatched = 0
    for _page_id, boxes, text_lines in population:
        columns = ainu.columns_of(boxes)
        if len(columns) == len(text_lines):
            continue
        mismatched += 1
        if any(paired_with(region, ainu.columns_of(region), text_lines) is not None
               for region in ainu.regions_of(boxes)):
            matched += 1
    print(f"  of {mismatched} mismatched pages, {matched} have a region whose own columns match "
          f"the line count and pass the gate")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=Path("work/ainu-records"))
    args = parser.parse_args()

    pages, lines, found = load(args.dataset)
    population = body_pages(pages, lines, found)
    differing = sum(1 for _p, boxes, text_lines in population
                    if len(ainu.columns_of(boxes)) != len(text_lines))
    print(f"{len(population)} body pages, {differing} whose column count differs from its line count")
    print()
    stray_shape(population)
    print()
    repair(population)
    print()
    spreads(population)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
