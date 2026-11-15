"""Fit the cutter's two models on pages whose character boxes are known.

    python models/cutter/fit.py collect --split val        # run the detector and classifier once
    python models/cutter/fit.py collect --split fit        # the same on the collection's reviewed pages
    python models/cutter/fit.py fit                        # frame.json and gate.json
    python models/cutter/fit.py measure --split test       # the fitted cutter on the held-out books

`collect` keeps, for every line of the benchmark's pages (`models/benchmark/cuts.py build`), the
cells, the candidate boxes with their features and what the classifier gives each of them for every
class a character of the split may stand for, so the alignment can be run again without a model. `fit` labels a candidate box as framing a
character when it reaches IoU 0.7 with one of the page's boxes, fits the framing model on those,
runs the alignment with it, and fits the acceptance model on the placements: right when the box
reaches IoU 0.7 with the box of the token's own character.

A transcription is not always the text of its line box. One line in four is therefore given one
fault before the acceptance model sees it: a character or two taken out of the transcription, put
into it, or exchanged for another. A box placed for a character that is not there is wrong whatever
it frames. And one character in twenty is read as one the classifier has no class for, so the model
learns what the rest of a line says about a character the classifier cannot read.

A reviewed page of the collection (`cuts.py build-reviewed`) knows the boxes a reviewer confirmed or
reported and no others. Its candidate boxes and placements are labelled where such a box decides
them and left out elsewhere, its lines are given no fault, and its rows together weigh `--reviewed`
of CODH's.
"""
from __future__ import annotations

import argparse
import json
import pickle
import random
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "models/benchmark"))
import cuts

from glyph_atlas import align, cut

OUT = ROOT / "models/cutter"
WORK = cuts.BENCH / "fit"
GOOD = 0.7
#: The splits of the collection's reviewed pages (`cuts.py build-reviewed`); their pages know only some boxes.
REVIEWED = ("fit", "held")
FRAME_FEATURES = ["kind_box", "kind_part", "kind_merge", "kind_ink", "kind_mixed", "cells", "tall", "wide",
                  "tall_abs", "wide_abs", "score", "valley", "top", "entropy", "density", "ink_before",
                  "ink_after", "ink_beside", "quality", "quality_given", "shift"]
GATE_FEATURES = [*FRAME_FEATURES, "cost", "leads", "known", "margin", "log_posterior", "frame", "passed",
                 "before", "after", "before_cost", "after_cost", "between", "line_cost", "line_whole",
                 "line_placed", "line_tokens", "kana", "mark", "cost_margin", "cost_whole"]
BLANK = np.zeros((1, 1), dtype=bool)


def collect(args):
    from PIL import Image

    bench = cut.BenchCutter(OUT / "none")
    listing = "reviewed-pages.jsonl" if args.split in REVIEWED else "pages.jsonl"
    pages = [json.loads(row) for row in (cuts.BENCH / listing).read_text(encoding="utf-8").splitlines()]
    by_book = defaultdict(list)
    for page in pages:
        if page["split"] == args.split:
            by_book[page["title"]].append(page)
    chosen = []
    for title in sorted(by_book):
        random.Random(0).shuffle(by_book[title])
        chosen += by_book[title][:args.pages] if args.pages else by_book[title]
    classes = bench.classifier.classes
    kept = []
    started = time.time()

    class Remembering:
        """The classifier, keeping the probabilities of the crops it last read."""

        def __init__(self):
            self.classes = classes
            self.rows = np.zeros((0, len(classes)))

        def probabilities_many(self, crops):
            self.rows = bench.classifier.probabilities_many(crops)
            return self.rows

    classifier = Remembering()
    for number, page in enumerate(chosen):
        with Image.open(cuts.BENCH / page["image"]) as handle:
            image = handle.convert("RGB")
        if page.get("partial"):
            pairs = cuts.reviewed_lines_of(page, image.size)
        else:
            pairs = [(line, [(unit["box"], None) for unit in truth]) for line, truth in cuts.lines_of(page)
                     if len(align.tokens_of(line)) == len(truth)]
        truths = {line.id: truth for line, truth in pairs}
        detections = [align.Detection(box=box, score=score) for box, score in bench.detector.boxes(image)]
        works = cut.prepare_page([line for line, _ in pairs], detections, image, classifier,
                                 refiner=bench.cutter.refiner)
        first = 0
        for work in works:
            rows = classifier.rows[first:first + len(work.spans)]
            first += len(work.spans)
            kept.append({
                "page": page["id"], "book": page["title"], "line": work.line_id, "vertical": work.frame.vertical,
                "tokens": work.tokens, "cells": work.cells, "spans": work.spans, "sizes": work.sizes,
                "rows": rows.astype(np.float16),
                "truth": [right for right, _ in truths[work.line_id]],
                "rejected": [wrong for _, wrong in truths[work.line_id]],
                "page_truth": [unit["box"] for unit in page["units"]],
                "page_rejected": [unit["box"] for unit in page.get("rejected", ())],
                "whole": len(work.tokens) == len(truths[work.line_id]),
                "partial": bool(page.get("partial")),
            })
        if (number + 1) % 20 == 0:
            print(f"  {number + 1}/{len(chosen)} pages, {time.time() - started:.0f} s", flush=True)
    # Only the classes a token of these lines may stand for are ever asked of a candidate box.
    index = {name: position for position, name in enumerate(classes)}
    wanted = sorted({index[point] for record in kept for token in record["tokens"]
                     for point in token.code_points if point in index})
    for record in kept:
        record["mass"] = record.pop("rows")[:, wanted]
    WORK.mkdir(parents=True, exist_ok=True)
    target = WORK / f"{args.split}.pickle"
    with target.open("wb") as handle:
        pickle.dump({"classes": [classes[position] for position in wanted], "lines": kept}, handle)
    print(f"{len(kept)} lines of {len(chosen)} pages -> {target}")


def load(split: str):
    with (WORK / f"{split}.pickle").open("rb") as handle:
        data = pickle.load(handle)
    data["index"] = {name: position for position, name in enumerate(data["classes"])}
    return data


def work_of(record, tokens, index) -> cut.Work:
    """A line as the alignment reads it, its cost table rebuilt for `tokens` from the kept classes."""
    spans = record["spans"]
    costs = np.full((len(tokens), max(1, len(spans))), cut.FLOOR)
    known = []
    for i, token in enumerate(tokens):
        positions = [index[point] for point in token.code_points if point in index]
        known.append(bool(positions))
        if positions and spans:
            mass = record["mass"][:, positions].astype(np.float64).sum(axis=1)
            costs[i, :len(spans)] = -np.log(np.maximum(mass, align.PROBABILITY_FLOOR))
    return cut.Work(line_id=record["line"], tokens=list(tokens), cells=record["cells"], spans=spans, costs=costs,
                    known=known, frame=cut.Frame(record["vertical"], BLANK), sizes=record["sizes"])


def faulted(record, pool, rng):
    """A line's tokens with one fault, and each token's truth box (None for a character not on the line)."""
    tokens, truth = list(record["tokens"]), list(record["truth"])
    if len(tokens) < 3:
        return tokens, truth
    kind = rng.choice(("drop", "add", "swap"))
    count = rng.choice((1, 1, 2))
    at = rng.randrange(len(tokens) - count + 1)
    if kind == "drop":
        del tokens[at:at + count], truth[at:at + count]
    elif kind == "add":
        tokens[at:at] = [rng.choice(pool) for _ in range(count)]
        truth[at:at] = [None] * count
    else:
        for position in range(at, at + count):
            other = rng.choice(pool)
            if other.code_points != tokens[position].code_points:
                tokens[position], truth[position] = other, None
    return tokens, truth


def logistic(rows, labels, names, *, weights=None, l2=1.0):
    from scipy.optimize import minimize

    x = np.array([[row[name] for name in names] for row in rows], dtype=np.float64)
    y = np.array(labels, dtype=np.float64)
    share = np.ones(len(y)) if weights is None else np.asarray(weights, dtype=np.float64)
    share = share / share.sum()
    mean = share @ x
    scale = np.sqrt(share @ (x - mean) ** 2)
    scale[scale < 1e-9] = 1.0
    z = (x - mean) / scale

    def loss(parameters):
        logits = z @ parameters[:-1] + parameters[-1]
        gradient = (1 / (1 + np.exp(-logits)) - y) * share
        value = share @ (np.logaddexp(0, logits) - y * logits) + l2 * (parameters[:-1] ** 2).sum() / (2 * len(y))
        return value, np.append(z.T @ gradient + l2 * parameters[:-1] / len(y), gradient.sum())

    found = minimize(loss, np.zeros(z.shape[1] + 1), jac=True, method="L-BFGS-B", options={"maxiter": 500})
    return {"features": names, "mean": mean.tolist(), "scale": scale.tolist(), "weights": found.x[:-1].tolist(),
            "bias": float(found.x[-1])}


def unread(token):
    """A token as one the classifier has no class for."""
    return token.model_copy(update={"code_points": {"U+F0000"}})


def framed(record, box) -> bool | None:
    """Whether a candidate box frames a character of its page; None where the page does not say.

    A page that knows only some of its boxes says so of a candidate that reaches one of them, and
    says a candidate is wrong when it holds most of a known character and more, or only a part of one.
    """
    best = max((cuts.iou(box, truth) for truth in record["page_truth"]), default=0.0)
    if best >= GOOD or not record.get("partial"):
        return best >= GOOD
    if any(cuts.iou(box, wrong) >= GOOD for wrong in record["page_rejected"]):
        return False
    for truth in record["page_truth"]:
        across = min(box["x"] + box["w"], truth["x"] + truth["w"]) - max(box["x"], truth["x"])
        along = min(box["y"] + box["h"], truth["y"] + truth["h"]) - max(box["y"], truth["y"])
        shared = max(across, 0) * max(along, 0)
        if best < 0.5 and (shared >= 0.5 * truth["w"] * truth["h"] or shared >= 0.6 * box["w"] * box["h"]):
            return False
    return None


def placements_of(data, cutter, *, fault: float = 0.0, unknown: float = 0.0, seed: int = 0):
    """Every placement of the collected lines with its IoU against its own character's box.

    On a page that knows only some of its boxes, the placements of the other tokens are left out,
    but for one that sits on a known character of another token or on the box a reviewer reported
    for its own, which are wrong.
    """
    rng = random.Random(seed)
    pool = [token for record in data["lines"] for token in record["tokens"] if token.kind is align.UnitKind.CHAR]
    rows = []
    for record in data["lines"]:
        if not record["whole"]:
            continue
        partial = record.get("partial", False)
        tokens, truth = record["tokens"], record["truth"]
        if fault and not partial and rng.random() < fault:
            tokens, truth = faulted(record, pool, rng)
        if unknown:
            tokens = [unread(token) if rng.random() < unknown else token for token in tokens]
        placements = cut.place(work_of(record, tokens, data["index"]), cutter.weights, cutter.framing)
        reported = record["rejected"] if partial else [None] * len(truth)
        for placement, box, wrong in zip(placements, truth, reported, strict=True):
            there = box is not None
            if partial and not there:
                placed = placement.box.model_dump() if placement.box is not None else None
                if placed is None or not (
                        max((cuts.iou(placed, other) for other in record["page_truth"]), default=0.0) >= 0.5
                        or (wrong is not None and cuts.iou(placed, wrong) >= GOOD)):
                    continue
            overlap = cuts.iou(placement.box.model_dump(), box) if placement.box is not None and there else 0.0
            rows.append({"book": record["book"], "placement": placement, "there": there, "iou": overlap,
                         "unread": placement.token.code_points == {"U+F0000"}})
    return rows


def curve(rows, boxed, probabilities):
    """Yield and precision at every acceptance threshold; yield is over characters that are on their line."""
    there = sum(row["there"] for row in rows)
    order = np.argsort(-probabilities)
    right = np.cumsum([boxed[i]["iou"] >= GOOD for i in order])
    return [{"accept": float(probabilities[order[k]]), "yield": float(right[k] / there),
             "precision": float(right[k] / (k + 1))} for k in range(len(order))]


def at_precision(points, target):
    best = None
    for point in points:
        if point["precision"] >= target and (best is None or point["yield"] > best["yield"]):
            best = point
    return best


def shares(counts, reviewed: float):
    """A weight for every row: the reviewed pages' rows together weigh `reviewed` of what CODH's do."""
    codh, own = counts
    return [1.0] * codh + [reviewed * codh / own] * own if own else [1.0] * codh


def fit(args):
    data = load("val")
    reviewed = load("fit") if args.reviewed else {"lines": []}
    sources = [data, reviewed]
    spans, labels, counts = [], [], []
    for source in sources:
        for record in source["lines"]:
            frame = cut.Frame(record["vertical"], BLANK)
            for span in record["spans"]:
                label = framed(record, frame.box(span.along, span.across).model_dump())
                if label is not None:
                    spans.append(span.features)
                    labels.append(label)
        counts.append(len(spans) - sum(counts))
    frame_model = logistic(spans, labels, FRAME_FEATURES, weights=shares(counts, args.reviewed))
    frame_model["version"] = args.version
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "frame.json").write_text(json.dumps(frame_model, indent=1) + "\n", encoding="utf-8")
    print(f"frame: {counts} candidate boxes, {sum(labels)} framing a character")
    gate_path = OUT / "gate.json"
    gate_path.unlink(missing_ok=True)
    cutter = cut.Cutter(OUT)
    rows = placements_of(data, cutter, fault=args.fault, unknown=args.unknown, seed=1)
    boxed = [row for row in rows if row["placement"].box is not None]
    known = [row for source in sources[1:]
             for row in placements_of(source, cutter, unknown=args.unknown, seed=1) if row["placement"].box is not None]
    gate = logistic([row["placement"].features for row in boxed + known],
                    [row["iou"] >= GOOD for row in boxed + known], GATE_FEATURES,
                    weights=shares((len(boxed), len(known)), args.reviewed))
    gate["version"] = args.version
    gate["accept"] = 0.5
    gate_path.write_text(json.dumps(gate, indent=1) + "\n", encoding="utf-8")
    probabilities = cut.Logistic(gate_path).probabilities([row["placement"].features for row in boxed])
    chosen = at_precision(curve(rows, boxed, probabilities), args.precision)
    if chosen is None:
        raise SystemExit(f"no acceptance threshold reaches precision {args.precision} on val")
    gate["accept"] = round(chosen["accept"], 4)
    gate["fitted"] = {"split": "val", "fault": args.fault, "unknown": args.unknown, "target_precision": args.precision,
                      "yield": round(chosen["yield"], 4), "placements": len(boxed),
                      "reviewed": {"weight": args.reviewed, "placements": len(known)}}
    gate_path.write_text(json.dumps(gate, indent=1) + "\n", encoding="utf-8")
    print(f"gate: {len(boxed)} placements, accept at {gate['accept']}: yield {chosen['yield']:.3f} "
          f"at precision {chosen['precision']:.3f} on faulted val")


def measure(args):
    data = load(args.split)
    cutter = cut.Cutter(OUT)
    report = {}
    for name, fault, unknown in (("clean", 0.0, 0.0), ("faulted", args.fault, args.unknown)):
        rows = placements_of(data, cutter, fault=fault, unknown=unknown, seed=2)
        boxed = [row for row in rows if row["placement"].box is not None]
        probabilities = cutter.gate.probabilities([row["placement"].features for row in boxed])
        books = sorted({row["book"] for row in rows})
        table = {}
        for book in [*books, "all", "unread"]:
            def among(row, book=book):
                return row["unread"] if book == "unread" else book in ("all", row["book"])
            own = [(row, p) for row, p in zip(boxed, probabilities, strict=True) if among(row)]
            count = sum(row["there"] for row in rows if among(row))
            if not count:
                continue
            accepted = [row for row, p in own if p >= cutter.gate.accept]
            table[book] = {
                "characters": count,
                "cut_0.5": round(sum(row["iou"] >= 0.5 for row, _ in own) / count, 4),
                "cut_0.7": round(sum(row["iou"] >= GOOD for row, _ in own) / count, 4),
                "accepted": len(accepted),
                "yield_0.7": round(sum(row["iou"] >= GOOD for row in accepted) / count, 4),
                "precision_0.7": round(sum(row["iou"] >= GOOD for row in accepted) / max(1, len(accepted)), 4),
                "precision_0.5": round(sum(row["iou"] >= 0.5 for row in accepted) / max(1, len(accepted)), 4),
            }
        points = curve(rows, boxed, probabilities)
        table["curve"] = {str(target): at_precision(points, target) for target in (0.95, 0.97, 0.98, 0.99)}
        report[name] = table
        print(name)
        for book, row in table.items():
            if book != "curve":
                print(f"  {book:<10}", json.dumps(row, ensure_ascii=False))
        for target, point in table["curve"].items():
            print(f"  precision>={target}:", point and {k: round(v, 3) for k, v in point.items()})
    (OUT / f"measured-{args.split}.json").write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n",
                                                     encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    collector = commands.add_parser("collect")
    collector.add_argument("--split", default="val", choices=("val", "test", *REVIEWED))
    collector.add_argument("--pages", type=int, default=0, help="pages a book; 0 for all")
    fitter = commands.add_parser("fit")
    fitter.add_argument("--version", default="lattice-3")
    fitter.add_argument("--fault", type=float, default=0.25)
    fitter.add_argument("--unknown", type=float, default=0.05)
    fitter.add_argument("--precision", type=float, default=0.985)
    fitter.add_argument("--reviewed", type=float, default=0.1,
                        help="weight of the collection's reviewed pages (collect --split fit) against CODH's; 0 leaves them out")
    measurer = commands.add_parser("measure")
    measurer.add_argument("--split", default="test", choices=("val", "test", *REVIEWED))
    measurer.add_argument("--fault", type=float, default=0.25)
    measurer.add_argument("--unknown", type=float, default=0.05)
    args = parser.parse_args()
    {"collect": collect, "fit": fit, "measure": measure}[args.command](args)


if __name__ == "__main__":
    main()
