"""Measure whether the transcribed lines of an アイヌ関連資料 page can be recovered as ink columns.

The rule, its thresholds and the reasons they are what they are live in `kuzushiji_atlas.ainu`; this
script is the report over a dataset — how many pages' column counts match their transcribed line
counts, witness by witness — and it is the measurement `docs/reports/card-status.md` quotes.

Run from the repository root:

    .venv/bin/python scripts/ainu_columns.py --all --out work/ainu-records/columns.tsv
    .venv/bin/python scripts/ainu_columns.py --pages 18 --histogram
    .venv/bin/python scripts/ainu_columns.py --witness hk:0916dafb80cdc48ca7687afcad4a4f35 --pages 8

It writes no table and changes no import; `atlas ainu derive` is the command that writes line boxes.
Nothing here is a test, and the tests never reach the network or the detector.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from collections import Counter
from pathlib import Path

from kuzushiji_atlas import ainu, detect, images, tables
from kuzushiji_atlas.schema import Box, Line, Page

SCORE = ainu.SCORE
GAP_RATIO = ainu.GAP_RATIO
MERGE_RATIO = ainu.MERGE_RATIO
REGION_SHARE = ainu.REGION_SHARE
BODY_LINES = ainu.BODY_LINES
columns_of = ainu.columns_of
regions_of = ainu.regions_of
histogram = ainu.histogram


def measure_page(page: Page, lines: list[Line], detector: detect.Detector,
                 args: argparse.Namespace, boxes: list[Box] | None = None) -> tuple[dict, list[Box]]:
    """One measured page: what the transcription holds, what the columns say, and the detections.

    `boxes` are detections already in hand, from a cache or from the caller, and the detector is left
    alone when they are given: comparing two grouping rules should not run the detector twice.
    """
    if boxes is None:
        found = detector.boxes(images.path_for(page.image))
        boxes = [box for box, _ in found if box.w > 0 and box.h > 0]
    derivation = ainu.derive_page(page, lines, boxes, gap_ratio=args.gap_ratio,
                                  merge_ratio=args.merge_ratio, body_lines=args.body_lines,
                                  min_per_character=args.min_per_character)
    row = ainu.page_row(derivation, page, lines, body_lines=args.body_lines,
                        min_per_character=args.min_per_character, gap_ratio=args.gap_ratio,
                        merge_ratio=args.merge_ratio, region_share=args.region_share)
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
    out.append(f"pages the derivation would pair {sum(row['paired'] for row in rows)} "
               f"(count match {sum(1 for row in rows if row['columns'] == row['lines'] and row['body'])}, "
               f"evidence gate {args.min_per_character} detections a character)")
    paired = [row for row in rows if row["paired"]]
    if paired:
        evidence = sorted(row["detections_per_character"] for row in paired)
        out.append(f"paired pages: median evidence {statistics.median(evidence):.2f} detections a "
                   f"character, from {evidence[0]:.2f} to {evidence[-1]:.2f}")
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
    parser.add_argument("--dataset", type=Path, default=ainu.DEFAULT_DATASET)
    parser.add_argument("--onnx", type=Path, default=ainu.DEFAULT_ONNX)
    parser.add_argument("--pages", type=int, default=18, help="how many pages to measure")
    parser.add_argument("--all", action="store_true", help="measure every page the cache holds")
    parser.add_argument("--witness", action="append", default=[], help="only this document id")
    parser.add_argument("--gap-ratio", type=float, default=GAP_RATIO)
    parser.add_argument("--merge-ratio", type=float, default=MERGE_RATIO)
    parser.add_argument("--region-share", type=float, default=REGION_SHARE)
    parser.add_argument("--body-lines", type=int, default=BODY_LINES,
                        help="a transcription of at least this many lines counts as a body")
    parser.add_argument("--min-per-character", type=float, default=ainu.MIN_DETECTIONS_PER_CHARACTER,
                        help="detections a transcribed character needs before a page is paired")
    parser.add_argument("--score", type=float, default=SCORE)
    parser.add_argument("--histogram", action="store_true", help="print the ink profile of each page")
    parser.add_argument("--out", type=Path, default=None, help="write the per-page rows here as TSV")
    parser.add_argument("--providers", default=None, help="comma-separated onnxruntime providers")
    parser.add_argument("--cache", type=Path, default=None,
                        help="read and write the page detections here, so rules can be compared "
                             "without running the detector again")
    args = parser.parse_args()

    dataset = tables.Dataset(args.dataset)
    pages: list[Page] = [page for page in dataset.read("pages") if images.path_for(page.image)]
    if args.witness:
        wanted = set(args.witness)
        pages = [page for page in pages if page.document_id in wanted]
    lines_by_page: dict[str, list[Line]] = {}
    for line in dataset.read("lines"):
        lines_by_page.setdefault(line.page_id, []).append(line)
    for found in lines_by_page.values():
        found.sort(key=lambda line: (line.seq if line.seq is not None else 0))

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

    settings = ainu.detector_settings(args.onnx, score=args.score)
    found_map = ainu._read_cache(args.cache, settings=settings)
    stated = ainu.settings_of(args.cache)
    if args.cache is not None and args.cache.exists():
        if stated is None and found_map:
            # A cache written before headers existed states nothing. It is used, because refusing it
            # would mean re-running the detector over a dataset that already paid for it, and the run
            # records the settings it is now filed under so the next check is a real one.
            print(f"{args.cache} states no settings; filed under score {args.score}", file=sys.stderr)
        elif stated != settings:
            print(f"{args.cache} states {stated}, not {settings}; not read", file=sys.stderr)
            found_map = {}
    if found_map:
        print(f"{len(found_map)} pages of detections read from {args.cache}", file=sys.stderr)
    detector: detect.Detector | None = None
    missing = [page for page in pages if found_map.get(page.id) is None]
    if missing:
        detector = detect.Detector(
            args.onnx,
            score=args.score,
            providers=args.providers.split(",") if args.providers else None,
        )
    # Every parameter the counts depend on is printed, because two censuses that differ in one of
    # them are not comparable and a TSV alone cannot say which rule wrote it.
    parameters = (f"score {args.score} gap {args.gap_ratio} merge {args.merge_ratio} "
                  f"body {args.body_lines} min-per-character {args.min_per_character} "
                  f"region-share {args.region_share} onnx {settings['sha256'][:12] or 'missing'} "
                  f"providers {detector.providers() if detector else 'cache covers every page'}")
    print(f"{len(pages)} pages from {len({p.document_id for p in pages})} witnesses, {parameters}")
    print(f"parameters: {parameters}")
    if found_map:
        print(f"detections: {len(found_map)} cached of {len(pages)} requested, "
              f"{len(missing)} to detect")

    rows: list[dict] = []
    started = time.monotonic()
    for index, page in enumerate(pages, start=1):
        fresh = found_map.get(page.id) is None
        row, boxes = measure_page(page, lines_by_page.get(page.id, []), detector, args,
                                  boxes=found_map.get(page.id))
        if fresh and args.cache is not None:
            # Persisted as it is computed, so an interrupted census is still worth something.
            if not args.cache.exists():
                ainu._write_cache(args.cache, {}, settings=settings)
            ainu.append_cache(args.cache, page.id, boxes)
        found_map[page.id] = boxes
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
              f"body {row['body']} paired {row['paired']}")
        if args.histogram:
            print("   " + histogram(boxes))
    if args.all:
        done = time.monotonic() - started
        print(f"measured {len(rows)} pages in {done:.0f} s, {done / max(1, len(rows)):.2f} s a page",
              file=sys.stderr, flush=True)

    print()
    print(summarize(rows, args))
    if args.out:
        written = ainu.write_columns(args.out, rows)
        print(f"-> {args.out} ({written} rows)")
    if args.cache is not None:
        ainu._write_cache(args.cache, found_map, [page.id for page in pages], settings=settings)
        print(f"-> {args.cache} ({len(found_map)} pages of detections)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
