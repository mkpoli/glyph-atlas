"""Measure a cutter on pages whose character boxes are known.

    python models/benchmark/cuts.py build                      # work/cut-bench from CODH's val and test books
    python models/benchmark/cuts.py run --cutter consensus --split test --pages 40

A cutter is given what the collection's extraction has: the page image, each line's box and its
transcription. CODH gives every character its own box, in reading order, so its lines are put
together from those boxes and the cutter's answer is compared with them. A character counts as
cut when the cutter's box for its token reaches IoU 0.5 with CODH's box; `yield` is the share of
characters the cutter both cut and accepted, `precision` the share of accepted boxes that are cut.

The books are the ones `data/splits/codh.tsv` holds out of the detector's and classifier's training.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import random
import shutil
import statistics
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BENCH = Path(os.environ.get("CUT_BENCH", ROOT / "work/cut-bench"))
#: The CODH import the pages and boxes are read from (`atlas import codh`).
DATASET = Path(os.environ.get("CUT_BENCH_DATASET", ROOT / "work/codh-full"))
IOU = 0.5


def build(names=("val", "test"), listing="pages.jsonl"):
    """Copy the page images of the books of splits `names` out of the shared cache and list their boxes."""
    import pyarrow.parquet as pq

    from glyph_atlas import images

    split = {}
    for row in (ROOT / "data/splits/codh.tsv").read_text(encoding="utf-8").splitlines():
        if row.startswith(("#", "bid")):
            continue
        bid, title, production, name = row.split("\t")
        split["codh:" + bid] = (name, production, title)
    want = {document for document, (name, _, _) in split.items() if name in names}
    pages = [p for p in pq.read_table(DATASET / "pages.parquet").to_pylist() if p["document_id"] in want]
    index = pq.ParquetFile(images.index_path()).read(columns=["url", "sha256", "superseded_by"])
    held = {row["url"]: row["sha256"] for row in index.to_pylist() if row["superseded_by"] is None}
    units = pq.read_table(DATASET / "units.parquet",
                          columns=["id", "document_id", "page_id", "box", "kind", "text_source"],
                          filters=[("document_id", "in", sorted(want))]).to_pylist()
    by_page = defaultdict(list)
    for unit in units:
        by_page[unit["page_id"]].append(unit)
    (BENCH / "images").mkdir(parents=True, exist_ok=True)
    kept = 0
    with (BENCH / listing).open("w", encoding="utf-8") as out:
        for page in pages:
            digest = held.get(page["image"])
            if not digest or page["id"] not in by_page:
                continue
            target = BENCH / "images" / digest
            if not target.exists():
                source = glob.glob(str(images.images_root() / digest[:2] / f"{digest}.*"))
                if not source:
                    continue
                shutil.copyfile(source[0], target)
            name, production, title = split[page["document_id"]]
            out.write(json.dumps({
                "id": page["id"], "document": page["document_id"], "title": title, "split": name,
                "production": production, "image": f"images/{digest}",
                "units": [{"id": u["id"], "box": u["box"], "kind": u["kind"], "text": u["text_source"]}
                          for u in by_page[page["id"]]]}, ensure_ascii=False) + "\n")
            kept += 1
    print(f"{kept} pages -> {BENCH / listing}")


def lines_of(page: dict, *, pad: float = 0.12):
    """A page's lines, from its boxes in reading order: a new line starts where the column changes.

    Returns `(Line, [truth unit, ...])` pairs. The line box is the union of its characters' boxes,
    widened by `pad` of its width on each side, as a transcriber's line box is drawn around a column
    and not around its ink.
    """
    from glyph_atlas.schema import Box, Line

    units = page["units"]
    if not units:
        return []
    width = statistics.median(u["box"]["w"] for u in units)
    groups, current = [], [units[0]]
    for unit in units[1:]:
        last = current[-1]["box"]
        box = unit["box"]
        centre = statistics.median(u["box"]["x"] + u["box"]["w"] / 2 for u in current)
        moved = abs(box["x"] + box["w"] / 2 - centre) > 0.6 * width
        up = box["y"] + box["h"] / 2 < last["y"] + last["h"] * 0.25
        if moved or up:
            groups.append(current)
            current = [unit]
        else:
            current.append(unit)
    groups.append(current)
    lines = []
    for number, group in enumerate(groups):
        x0 = min(u["box"]["x"] for u in group)
        y0 = min(u["box"]["y"] for u in group)
        x1 = max(u["box"]["x"] + u["box"]["w"] for u in group)
        y1 = max(u["box"]["y"] + u["box"]["h"] for u in group)
        margin = round((x1 - x0) * pad)
        x0, x1 = max(0, x0 - margin), x1 + margin
        text = "".join(u["text"] or "" for u in group)
        line = Line(id=f"{page['id']}:L{number:03d}", page_id=page["id"], seq=number,
                    box=Box(x=x0, y=y0, w=x1 - x0, h=y1 - y0), vertical=True, text_raw=text, text=text)
        lines.append((line, group))
    return lines


def iou(a, b) -> float:
    left, top = max(a["x"], b["x"]), max(a["y"], b["y"])
    right, bottom = min(a["x"] + a["w"], b["x"] + b["w"]), min(a["y"] + a["h"], b["y"] + b["h"])
    if right <= left or bottom <= top:
        return 0.0
    inter = (right - left) * (bottom - top)
    return inter / (a["w"] * a["h"] + b["w"] * b["h"] - inter)


class Consensus:
    """The collection's extraction as it runs: `collection-v2` alignment and the two-reader gate."""

    name = "consensus"

    def __init__(self):
        from glyph_atlas import extraction_queue

        self.queue = extraction_queue
        self.engine = extraction_queue.Engine()

    def cut(self, image, lines):
        """Per line id, one `(box or None, accepted, reason)` per token."""
        from glyph_atlas import align

        engine, queue = self.engine, self.queue

        def crop_of(_page, box):
            return image.crop((box.x, box.y, box.x + box.w, box.y + box.h))

        detections = [align.Detection(box=box, score=score) for box, score in engine.detector.boxes(image)]
        aligned = align.align_page(lines, detections, run=engine.run, classifier=engine.classifier, crop_of=crop_of)
        result = {}
        for line in lines:
            found, _ = aligned[line.id]
            answers = []
            for unit in found:
                reason = queue.quality_reason(unit, [], image.size)
                if reason in (None, "visual-disagreement"):
                    votes = engine.reader.read(crop_of(None, unit.box))["votes"]
                    reason = queue.quality_reason(unit, votes, image.size)
                answers.append((unit.box.model_dump() if unit.box else None, reason is None, reason or "accepted"))
            result[line.id] = answers
        align.clear_crop_cache()
        return result


def cutter_of(name: str):
    if name == "consensus":
        return Consensus()
    raise ValueError(f"no cutter named {name}")


def run(args):
    from PIL import Image

    from glyph_atlas import align

    pages = [json.loads(row) for row in (BENCH / "pages.jsonl").read_text(encoding="utf-8").splitlines()]
    pages = [p for p in pages if p["split"] == args.split]
    by_book = defaultdict(list)
    for page in pages:
        by_book[page["title"]].append(page)
    chosen = []
    for title in sorted(by_book):
        book = by_book[title]
        random.Random(0).shuffle(book)
        chosen += book[:args.pages] if args.pages else book
    cutter = cutter_of(args.cutter)
    totals = defaultdict(Counter)
    ious = defaultdict(list)
    records = []
    started = time.time()
    for number, page in enumerate(chosen):
        with Image.open(BENCH / page["image"]) as handle:
            image = handle.convert("RGB")
        pairs = []
        for line, truth in lines_of(page):
            if len(align.tokens_of(line)) == len(truth):
                pairs.append((line, truth))
            else:
                totals[page["title"]]["lines-skipped"] += 1
        answers = cutter.cut(image, [line for line, _ in pairs])
        for line, truth in pairs:
            found = answers.get(line.id) or []
            for position, unit in enumerate(truth):
                box, accepted, reason = found[position] if position < len(found) else (None, False, "missing")
                overlap = iou(box, unit["box"]) if box else 0.0
                counts = totals[page["title"]]
                counts["characters"] += 1
                counts["cut"] += overlap >= IOU
                counts["boxed"] += box is not None
                if accepted:
                    counts["accepted"] += 1
                    counts["right"] += overlap >= IOU
                    ious[page["title"]].append(overlap)
                else:
                    counts["withheld:" + reason] += 1
                if args.records:
                    record = {"page": page["id"], "book": page["title"], "line": line.id, "position": position,
                              "text": unit["text"], "truth": unit["box"], "box": box, "accepted": accepted,
                              "reason": reason, "iou": round(overlap, 3)}
                    placed = getattr(cutter, "placements", {}).get(line.id)
                    if placed and position < len(placed):
                        record["kind"] = placed[position].kind
                        record["features"] = placed[position].features
                    records.append(record)
        if (number + 1) % 10 == 0:
            print(f"  {number + 1}/{len(chosen)} pages, {time.time() - started:.0f} s", flush=True)
    report = {"cutter": args.cutter, "split": args.split, "pages": len(chosen),
              "seconds_per_page": round((time.time() - started) / max(1, len(chosen)), 2), "books": {}}
    overall = Counter()
    for title, counts in totals.items():
        overall.update(counts)
        report["books"][title] = summary(counts, ious[title])
    report["all"] = summary(overall, [value for values in ious.values() for value in values])
    out = BENCH / "results"
    out.mkdir(exist_ok=True)
    stem = f"{args.cutter}-{args.split}" + (f"-{args.tag}" if args.tag else "")
    (out / f"{stem}.json").write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    if args.records:
        with (out / f"{stem}.records.jsonl").open("w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"{'book':<12}{'chars':>8}{'boxed':>8}{'cut':>8}{'yield':>8}{'precision':>10}{'mean IoU':>9}")
    for title, row in [*report["books"].items(), ("all", report["all"])]:
        print(f"{title:<12}{row['characters']:>8}{row['boxed_share']:>8.3f}{row['cut_share']:>8.3f}"
              f"{row['yield']:>8.3f}{row['precision']:>10.3f}{row['mean_iou']:>9.3f}")
    print(json.dumps(report["all"]["withheld"], ensure_ascii=False))


def summary(counts: Counter, overlaps: list[float]) -> dict:
    n = max(1, counts["characters"])
    return {"characters": counts["characters"], "lines_skipped": counts["lines-skipped"],
            "boxed_share": round(counts["boxed"] / n, 4), "cut_share": round(counts["cut"] / n, 4),
            "accepted": counts["accepted"], "right": counts["right"],
            "yield": round(counts["right"] / n, 4),
            "precision": round(counts["right"] / max(1, counts["accepted"]), 4),
            "mean_iou": round(statistics.fmean(overlaps), 4) if overlaps else 0.0,
            "withheld": {key[9:]: value for key, value in sorted(counts.items()) if key.startswith("withheld:")}}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    builder = commands.add_parser("build")
    builder.add_argument("--train", action="store_true",
                         help="list the training books instead, as train-pages.jsonl, for models/refiner")
    runner = commands.add_parser("run")
    runner.add_argument("--cutter", default="consensus")
    runner.add_argument("--split", default="val", choices=("val", "test"))
    runner.add_argument("--pages", type=int, default=0, help="pages a book, drawn with a fixed seed; 0 for all")
    runner.add_argument("--tag", default="")
    runner.add_argument("--records", action="store_true", help="also write one record a character")
    args = parser.parse_args()
    if args.command == "build":
        build(("train",), "train-pages.jsonl") if args.train else build()
    else:
        run(args)


if __name__ == "__main__":
    main()
