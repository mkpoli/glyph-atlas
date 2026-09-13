"""How the derivation's evidence gate decides, measured against every page it decides on.

`derive_page` pairs a page only when its columns match its lines *and* every column holds at least
`MIN_DETECTIONS_PER_CHARACTER` detections for each character its line names. That gate is the second
place the derivation refuses work, and unlike the count mismatch it can be checked against every page
it decides rather than only the ones it refuses.

Three questions, in the order they matter:

* **Where do the refusals actually sit?** The saved census cannot say: `derive_page` clears
  `Derivation.evidence` when the gate refuses and `page_row` reads that list, so `weakest_column` is
  empty for exactly the 64 pages a reader would want. This recomputes them from the cached detections.
* **Is the floor a separator or a policy?** Counting how many pages pair at each threshold answers it,
  and a smooth ramp means the value was chosen and not found.
* **Would a floor relative to the page decide differently?** The absolute gate punishes a column for
  the condition of the page around it; a relative one compares the weakest column against the page's own
  middle column. What matters is how many pages the two rules disagree about, and in which direction.

Read-only, and fast, because it reads the cached detections rather than running the detector:

    .venv/bin/python scripts/ainu_evidence.py [--dataset work/ainu-records]
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from glyph_atlas import ainu, tables
from glyph_atlas.schema import Box, Document, Line, Page

#: The relative rules to compare against the absolute floor, as a share of the page's middle column.
SHARES = (0.9, 0.75, 0.6, 0.5, 0.4, 0.3)
#: The thresholds the ramp is counted at, ending at the floor the derivation actually uses.
RAMPS = (0.05, 0.10, 0.20, 0.30, 0.40, 0.50)


def measured(dataset: Path) -> list[tuple[str, float, float]]:
    """Every count-matched page: its id, its weakest column's evidence, and its middle column's."""
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

    rows: list[tuple[str, float, float]] = []
    for page_id, boxes in found.items():
        if page_id not in pages or not boxes:
            continue
        text_lines = ainu.transcribed_lines(lines.get(page_id, []))
        if len(text_lines) < ainu.BODY_LINES:
            continue
        columns = ainu.columns_of(boxes)
        if len(columns) != len(text_lines):
            continue
        derivation = ainu.Derivation(page_id=page_id, columns=columns, boxes=list(boxes))
        derivation.pairing = text_lines
        evidence = ainu.evidence_per_column(derivation)
        rows.append((page_id, min(evidence), statistics.median(evidence)))
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=Path("work/ainu-records"))
    args = parser.parse_args()

    rows = measured(args.dataset)
    floor = ainu.MIN_DETECTIONS_PER_CHARACTER
    titles = {document.id.split(":")[1]: document.title
              for document in tables.read(args.dataset / "documents.parquet", Document)}
    weakest = {page: weak for page, weak, _ in rows}
    middle = {page: median for page, _, median in rows}
    values = sorted(weakest.values())
    refused = [value for value in values if value < floor]
    paired = [value for value in values if value >= floor]

    print(f"{len(rows)} pages whose column count matches their line count")
    print(f"  the gate pairs {len(paired)} and refuses {len(refused)}")
    if refused:
        print(f"  refused weakest columns run {min(refused):.2f} to {max(refused):.2f}")
    if paired:
        print(f"  paired weakest columns run {min(paired):.2f} to {max(paired):.2f}")
    print(f"  the two sets {'overlap' if refused and paired and max(refused) >= min(paired) else 'do not overlap'}")
    print()

    print("pages that would pair at each floor, against the one in use:")
    for threshold in RAMPS:
        count = sum(1 for value in values if value >= threshold)
        mark = "  <- in use" if abs(threshold - floor) < 1e-9 else ""
        print(f"  weakest >= {threshold:.2f}: {count:>4}{mark}")
    print()

    absolute = {page for page, weak in weakest.items() if weak >= floor}
    print(f"a floor relative to the page, against the absolute floor of {floor}")
    print(f"  {'weakest >= ... of the page median':<38}{'pairs':>7}{'agrees':>8}")
    for share in SHARES:
        relative = {page for page, weak in weakest.items() if weak >= middle[page] * share}
        print(f"  {share:>5.0%}{'':<32}{len(relative):>7}{len(relative & absolute):>8}")
    print()

    share = 0.5
    relative = {page for page, weak in weakest.items() if weak >= middle[page] * share}
    only_relative = relative - absolute
    only_absolute = absolute - relative
    print(f"at half the page median the two rules differ on "
          f"{len(only_relative) + len(only_absolute)} pages")
    for label, subset in (("relative rule pairs it, the floor refuses", only_relative),
                          ("the floor pairs it, the relative rule refuses", only_absolute)):
        if not subset:
            continue
        print(f"  {label}:")
        for page in sorted(subset, key=lambda p: weakest[p]):
            document = page.rsplit(":", 1)[0].split(":", 1)[1]
            print(f"    {titles.get(document, '?')[:22]:<24}{page[-16:]}  "
                  f"weakest {weakest[page]:.2f}, median {middle[page]:.2f}")
    witnesses = Counter(page.rsplit(":", 1)[0] for page in only_relative)
    if witnesses:
        print(f"  the relative-only pages come from {len(witnesses)} documents")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
