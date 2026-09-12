"""Derive line boxes for the アイヌ関連資料 transcriptions, and measure the derivation.

The Ainu records arrive as page transcriptions: 8,212 lines of text and no boxes, because the platform
transcribes a page and the atlas has nothing to align to. The plan's step 2 is to give those lines
boxes. This script is the measurement that decides whether that is possible, and it is the one the
plan's card status quotes.

The rule: a vertical line of a woodblock print is a column of ink. The detector finds the characters,
the characters group into columns that share a horizontal band, and the columns are read right to left.
A page is then compared with its own transcription, which the platform splits into lines. The
comparison is only meaningful for a page whose transcription covers the page; some pages carry a title
and nothing else, and those are separated out rather than counted as failures.

Run from the repository root:

    .venv/bin/python scripts/ainu_columns.py --all --out work/ainu-records/columns.tsv
    .venv/bin/python scripts/ainu_columns.py --pages 18 --histogram
    .venv/bin/python scripts/ainu_columns.py --witness hk:0916dafb80cdc48ca7687afcad4a4f35 --pages 8

The script is a report: it writes no table and changes no import. Nothing here is a test, and the
tests never reach the network or the detector.
"""

from __future__ import annotations

import argparse
import csv
import statistics
import sys
import time
from collections import Counter
from pathlib import Path

from kuzushiji_atlas import detect, images, tables
from kuzushiji_atlas.schema import Box, Line, Page

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT / "work" / "ainu-records"
DEFAULT_ONNX = ROOT / "models" / "detector" / "artifacts" / "detector.onnx"
#: The operating point of the pilot run: the detector's score cutoff.
SCORE = 0.02
#: A column is a run of detections whose centres step by no more than this share of the median width.
GAP_RATIO = 0.5
#: Two such runs are one column when their centres are closer than this share of the median width.
MERGE_RATIO = 0.9
#: A gap of this share of the page's width or more separates two blocks of text, such as a spread's
#: two leaves. Only the widest gaps are cut, so a crowded text block stays one region.
REGION_SHARE = 0.05
#: A transcription of fewer than this many lines names a title or a caption rather than the page.
BODY_LINES = 4


def columns_of(boxes: list[Box], gap_ratio: float = GAP_RATIO,
               merge_ratio: float = MERGE_RATIO) -> list[list[int]]:
    """The detection indices grouped into columns, right to left.

    A column is a run of detections whose centres follow one another by no more than `gap_ratio` of
    the page's median character width, which is the step from one character to the next inside a
    line. Two runs are then merged when their centres are closer than `merge_ratio` of that width,
    because a line whose characters lean or thin out can pause by more than the step without ending.
    The distance is a share of the width rather than a count of pixels because the witnesses are
    scanned at 1,000 to 6,500 pixels across. Runs come back in reading order: rightmost first.
    """
    if not boxes:
        return []
    width = statistics.median(box.w for box in boxes)
    order = sorted(range(len(boxes)), key=lambda index: boxes[index].x + boxes[index].w / 2)
    runs: list[list[int]] = [[order[0]]]
    for index in order[1:]:
        centre = boxes[index].x + boxes[index].w / 2
        previous = boxes[runs[-1][-1]]
        if centre - (previous.x + previous.w / 2) <= width * gap_ratio:
            runs[-1].append(index)
        else:
            runs.append([index])
    merged: list[list[int]] = [runs[0]]
    for run in runs[1:]:
        here = statistics.mean(boxes[index].x + boxes[index].w / 2 for index in run)
        there = statistics.mean(boxes[index].x + boxes[index].w / 2 for index in merged[-1])
        if here - there < width * merge_ratio:
            merged[-1].extend(run)
        else:
            merged.append(run)
    return merged


def regions_of(boxes: list[Box], gap_ratio: float, share: float) -> list[list[Box]]:
    """The page's detections split where the columns stop, so a spread's leaves are counted apart.

    A double-page scan holds a text block on one leaf and often cataloguing marks on the other, and a
    capture of two facing pages holds two text blocks. A gap of `share` of the page width or more
    between neighbouring columns separates them, which no space inside a line reaches.
    """
    if not boxes:
        return []
    width = statistics.median(box.w for box in boxes)
    right = max(box.x + box.w for box in boxes)
    cut = max(width * 3.0, right * share)
    order = sorted(boxes, key=lambda box: -(box.x + box.w / 2))
    chunks: list[list[Box]] = [[order[0]]]
    edge = order[0].x + order[0].w / 2
    for box in order[1:]:
        centre = box.x + box.w / 2
        if centre < edge - cut:
            chunks.append([])
        chunks[-1].append(box)
        edge = centre
    return chunks


def histogram(boxes: list[Box], width: int = 100) -> str:
    """A one-line picture of where the ink sits across the page, for eyeballing a grouping."""
    if not boxes:
        return "(no detections)"
    right = max(box.x + box.w for box in boxes)
    bins = [0] * width
    for box in boxes:
        bins[min(width - 1, int((box.x + box.w / 2) / max(1, right) * width))] += 1
    peak = max(bins) or 1
    return "".join(" .:-=+*#@"[min(8, round(value / peak * 8))] for value in bins)


def page_rows(page: Page, lines: list[Line], detector: detect.Detector,
              args: argparse.Namespace) -> tuple[dict, list[Box]]:
    """One measured page: what the transcription holds, what the columns say, and the detections."""
    text_lines = [line for line in lines if (line.text or "").strip()]
    found = detector.boxes(images.path_for(page.image))
    boxes = [box for box, _ in found if box.w > 0 and box.h > 0]
    grouped = columns_of(boxes, args.gap_ratio, args.merge_ratio)
    regions = regions_of(boxes, args.gap_ratio, args.region_share)
    region_columns = [len(columns_of(region, args.gap_ratio, args.merge_ratio)) for region in regions]
    row = {
        "page_id": page.id,
        "document_id": page.document_id,
        "width": page.width,
        "height": page.height,
        "characters": len(boxes),
        "columns": len(grouped),
        "regions": len(regions),
        "region_columns": ",".join(str(value) for value in region_columns),
        "largest_region_columns": max(region_columns) if region_columns else 0,
        "lines": len(text_lines),
        "text_lines": sum(len(line.text or "") for line in text_lines),
        "text_characters": sum(len(line.text or "") for line in text_lines),
        "body": int(len(text_lines) >= args.body_lines),
        "title": (text_lines[0].text or "")[:40] if text_lines else "",
    }
    return row, boxes


def summarize(rows: list[dict], args: argparse.Namespace) -> str:
    """The measured agreement, as the lines a report can quote."""
    body = [row for row in rows if row["body"]]
    out: list[str] = []
    out.append(f"pages {len(rows)}  witnesses {len({row['document_id'] for row in rows})}")
    out.append(f"pages with a body transcription (>= {args.body_lines} lines) {len(body)}, "
               f"title-only or empty {len(rows) - len(body)}")
    for label, subset in (("all pages", rows), ("body pages", body)):
        if not subset:
            continue
        exact = sum(1 for row in subset if row["columns"] == row["lines"])
        within = sum(1 for row in subset if row["lines"]
                     and abs(row["columns"] - row["lines"]) <= max(1, row["lines"] * 0.25))
        ratios = [row["columns"] / row["lines"] for row in subset if row["lines"]]
        out.append(f"{label}: exact {exact}/{len(subset)}, within 25% {within}/{len(subset)}, "
                   f"median ratio {statistics.median(ratios):.2f}" if ratios else f"{label}: no lines")
    counters = Counter((row["columns"] == row["lines"], row["body"]) for row in rows)
    out.append(f"exact on body pages {counters[(True, 1)]}/{counters[(True, 1)] + counters[(False, 1)]}, "
               f"exact on title-only pages {counters[(True, 0)]}/{counters[(True, 0)] + counters[(False, 0)]}")
    out.append("")
    out.append("per witness, body pages: exact / within 25% / pages, median ratio")
    for document in sorted({row["document_id"] for row in rows}):
        subset = [row for row in body if row["document_id"] == document]
        if not subset:
            out.append(f"  {document}  no body pages")
            continue
        exact = sum(1 for row in subset if row["columns"] == row["lines"])
        within = sum(1 for row in subset if row["lines"]
                     and abs(row["columns"] - row["lines"]) <= max(1, row["lines"] * 0.25))
        ratios = [row["columns"] / row["lines"] for row in subset if row["lines"]]
        median = statistics.median(ratios) if ratios else float("nan")
        out.append(f"  {document}  {exact}/{within}/{len(subset)}  median {median:.2f}")
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--onnx", type=Path, default=DEFAULT_ONNX)
    parser.add_argument("--pages", type=int, default=18, help="how many pages to measure")
    parser.add_argument("--all", action="store_true", help="measure every page the cache holds")
    parser.add_argument("--witness", action="append", default=[], help="only this document id")
    parser.add_argument("--gap-ratio", type=float, default=GAP_RATIO)
    parser.add_argument("--merge-ratio", type=float, default=MERGE_RATIO)
    parser.add_argument("--region-share", type=float, default=REGION_SHARE)
    parser.add_argument("--body-lines", type=int, default=BODY_LINES,
                        help="a transcription of at least this many lines counts as a body")
    parser.add_argument("--score", type=float, default=SCORE)
    parser.add_argument("--histogram", action="store_true", help="print the ink profile of each page")
    parser.add_argument("--out", type=Path, default=None, help="write the per-page rows here as TSV")
    parser.add_argument("--providers", default=None, help="comma-separated onnxruntime providers")
    args = parser.parse_args()

    dataset = tables.Dataset(args.dataset)
    pages: list[Page] = [page for page in dataset.read("pages") if images.path_for(page.image)]
    if args.witness:
        wanted = set(args.witness)
        pages = [page for page in pages if page.document_id in wanted]
    lines_by_page: dict[str, list[Line]] = {}
    for line in dataset.read("lines"):
        lines_by_page.setdefault(line.page_id, []).append(line)

    if not args.all:
        # One page per document first, so a sample of n pages covers as many witnesses as it can.
        by_document: dict[str, list[Page]] = {}
        for page in pages:
            by_document.setdefault(page.document_id, []).append(page)
        chosen: list[Page] = [by_document[document][0] for document in sorted(by_document)]
        for page in pages:
            if len(chosen) >= args.pages:
                break
            if page not in chosen:
                chosen.append(page)
        pages = chosen[: args.pages]

    detector = detect.Detector(
        args.onnx,
        score=args.score,
        providers=args.providers.split(",") if args.providers else None,
    )
    print(f"{len(pages)} pages from {len({p.document_id for p in pages})} witnesses, score {args.score}, "
          f"gap {args.gap_ratio} x median width, merge {args.merge_ratio}, providers {detector.providers()}")

    rows: list[dict] = []
    started = time.monotonic()
    for index, page in enumerate(pages, start=1):
        row, boxes = page_rows(page, lines_by_page.get(page.id, []), detector, args)
        rows.append(row)
        if args.all:
            if index % 25 == 0 or index == len(pages):
                done = time.monotonic() - started
                print(f"  {index}/{len(pages)} pages, {done:.0f} s, {done / index:.2f} s a page",
                      file=sys.stderr, flush=True)
            continue
        print(f"{row['page_id']} {row['width']}x{row['height']} chars {row['characters']:>4} "
              f"columns {row['columns']:>3} lines {row['lines']:>3} "
              f"ratio {row['columns'] / row['lines'] if row['lines'] else float('nan'):5.2f} "
              f"body {row['body']}")
        if args.histogram:
            print("   " + histogram(boxes))
    if args.all:
        done = time.monotonic() - started
        print(f"measured {len(rows)} pages in {done:.0f} s, {done / max(1, len(rows)):.2f} s a page",
              file=sys.stderr, flush=True)

    print()
    print(summarize(rows, args))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]), delimiter="\t", lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
        print(f"-> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
