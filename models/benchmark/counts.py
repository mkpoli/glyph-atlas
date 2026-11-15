"""Score a cutter of untranscribed pages against detector boxes whose characters were counted by eye.

    python models/benchmark/counts.py --cutter segment       # glyph_atlas.segment
    python models/benchmark/counts.py --cutter split         # character_proposals.split_boxes
    python models/benchmark/counts.py --cutter detector      # the detector's boxes as they are
    python models/benchmark/counts.py --cutter detector --scale 1.5   # any of them on the page enlarged
    python models/benchmark/counts.py --cutter detector --scale 1.5 --fill 2

`counts/kyujanggak.json` lists 268 boxes the detector drew on eight pages of the Kyujanggak land
registers of 1722 and 1904, each with the number of characters it holds. A cutter is right about a
box when it makes as many boxes of it as it holds characters; where the cuts fall is not checked.
A box the cutter joins with another counts as part of a box for each.

On each page the boxes were drawn from two groups: those long or large against their surroundings,
which are the ones a cutter may need to cut, and the rest. `pages` holds how many boxes of each
group the page has, so the report also gives the result for 100 boxes of a page that hold whole
characters. The four pages of sample `first` were looked at while `glyph_atlas.segment` was being
changed; those of `second` were drawn and counted afterwards and decide whether a change is one.

A listed box is found again among the detector's as the one that overlaps it most, so a retrained
detector is scored on the boxes it still draws (IoU 0.7); the rest are reported as gone. The listed
boxes are the detector's on the page at its own size. With `--scale` the cutter works from the boxes
of the resized page (`Detector.boxes_at`), and every box it makes, one made of unboxed ink included,
is laid over the listed boxes.
"""
from __future__ import annotations

import argparse
import glob
import json
from collections import Counter
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LABELS = ROOT / "models/benchmark/counts/kyujanggak.json"
RESULTS = ROOT / "models/benchmark/results"
RUN = ROOT / "models/align/runs/collection-v2.yaml"
SAME = 0.7


def iou(a, b) -> float:
    across = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
    down = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
    if across <= 0 or down <= 0:
        return 0.0
    return across * down / (a[2] * a[3] + b[2] * b[3] - across * down)


def inside(a, b) -> float:
    """The share of box `a` that lies in box `b`."""
    across = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
    down = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
    return max(0, across) * max(0, down) / max(1, a[2] * a[3])


def shares(pieces: list[tuple[int, ...]], boxes: list[tuple[int, int, int, int]]) -> Counter:
    """How many boxes a cutter made of each detection, from the detections each of its boxes stands for.

    A box that stands for a detection and the detections lying inside it is one box of the outer
    one. A box that joins detections lying apart is a share of a box for each of them, so two
    single characters joined are both wrong.
    """
    made: Counter = Counter()
    for detections in pieces:
        outer = [index for index in detections
                 if not any(other != index and inside(boxes[index], boxes[other]) >= 0.7 for other in detections)]
        for index in outer or detections:
            made[index] += Fraction(1, len(outer or detections))
    return made


def stood_for(made: list[tuple[int, int, int, int]], boxes: list[tuple[int, int, int, int]]) -> list[tuple[int, ...]]:
    """The detections each box of a cutter that names none stands for.

    A box stands for the detections that lie in it, which is how one kept whole or joined from two is
    read, and a part of a detection for that detection: the one it lies in that it overlaps most.
    """
    pieces = []
    for box in made:
        within = [index for index, detection in enumerate(boxes) if inside(detection, box) >= 0.6]
        around = [index for index, detection in enumerate(boxes) if inside(box, detection) >= 0.6]
        whole = max(around, key=lambda index: iou(box, boxes[index]), default=None)
        if whole is not None and whole not in within:
            within.append(whole)
        if within:
            pieces.append(tuple(sorted(within)))
    return pieces


class Cutters:
    """The models, loaded once, and the boxes each cutter makes of every detection of a page."""

    def __init__(self):
        from glyph_atlas import align, cut, refine
        from glyph_atlas.classify import Classifier
        from glyph_atlas.detect import Detector

        run = align.load_run(RUN)
        self.detector = Detector(ROOT / run.detector, score=run.score, nms=run.nms)
        self.classifier = Classifier(ROOT / run.classifier)
        self.refiner = refine.Refiner()
        self.framing = cut.Logistic(cut.MODELS / "frame.json")

    def made(self, name: str, image, scale: float = 1.0,
             fill: tuple[float, ...] = ()) -> tuple[list[tuple[int, int, int, int]], list[int]]:
        """The detector's boxes on a page at its own size and how many boxes cutter `name` makes of each."""
        from glyph_atlas import align, segment
        from glyph_atlas.character_proposals import Proposal, split_boxes

        own = self.detector.boxes(image)
        boxes = [(box.x, box.y, box.w, box.h) for box, _ in own]
        resized = scale != 1.0 or bool(fill)
        found = self.detector.boxes_at(image, scale, fill) if resized else own
        if name == "detector" and not resized:
            return boxes, [1] * len(boxes)
        if name == "detector":
            made = [(box.x, box.y, box.w, box.h) for box, _ in found]
        elif name == "split":
            made = [(p.x, p.y, p.w, p.h) for p in split_boxes(image, [
                Proposal(x=box.x, y=box.y, w=box.w, h=box.h, score=round(float(score), 4)) for box, score in found])]
        else:
            detections = [align.Detection(box=box, score=float(score)) for box, score in found]
            pieces = segment.cut_page(detections, image, self.classifier, self.framing, refiner=self.refiner)
            if not resized:
                counts = shares([piece.detections for piece in pieces], boxes)
                return boxes, [counts[index] for index in range(len(boxes))]
            made = [(piece.box.x, piece.box.y, piece.box.w, piece.box.h) for piece in pieces]
        counts = shares(stood_for(made, boxes), boxes)
        return boxes, [counts[index] for index in range(len(boxes))]


def score(rows: list[dict], pages: dict) -> dict:
    """Counts over the listed boxes, and the same for 100 boxes of a page as the detector draws them."""
    drawn = [row for row in rows if row["characters"] is not None and row["made"] is not None]
    judged = [row for row in drawn if row["characters"]]
    singles = [row for row in judged if row["characters"] == 1]
    several = [row for row in judged if row["characters"] > 1]
    total = right = halved = lost = missed = 0.0
    for name, page in pages.items():
        for group, size in page["boxes"].items():
            listed = [row for row in drawn if row["page"] == name and row["group"] == group]
            own = [row for row in listed if row["characters"]]
            if own:
                # A group none of whose listed boxes can be judged says nothing, and is left out of the hundred.
                # A listed box that holds no character stands for the group's others that hold none.
                weight = size / len(listed)
                total += weight * len(own)
                right += weight * sum(row["made"] == row["characters"] for row in own)
                halved += weight * sum(row["characters"] == 1 and row["made"] > 1 for row in own)
                lost += weight * sum(row["characters"] == 1 and row["made"] < 1 for row in own)
                missed += weight * sum(row["characters"] > 1 and row["made"] != row["characters"] for row in own)
    total = total or 1.0
    return {
        "boxes": len(judged), "gone": sum(row["made"] is None for row in rows),
        "one_character": len(singles), "one_character_cut": sum(row["made"] > 1 for row in singles),
        "one_character_lost": sum(row["made"] < 1 for row in singles),
        "several_characters": len(several),
        "several_right": sum(row["made"] == row["characters"] for row in several),
        "several_too_few": sum(row["made"] < row["characters"] for row in several),
        "several_too_many": sum(row["made"] > row["characters"] for row in several),
        "per_100_boxes": {"right": round(100 * right / total, 1), "one_character_cut": round(100 * halved / total, 1),
                          "one_character_lost": round(100 * lost / total, 1),
                          "several_wrong": round(100 * missed / total, 1)},
    }


def main():
    from PIL import Image

    from glyph_atlas import images

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cutter", default="segment", choices=("segment", "split", "detector"))
    parser.add_argument("--scale", type=float, default=1.0, help="resize the page by this before detecting")
    parser.add_argument("--fill", type=float, nargs="*", default=[],
                        help="scales whose boxes are added where the others left none")
    args = parser.parse_args()
    labels = json.loads(LABELS.read_text(encoding="utf-8"))
    cutters = Cutters()
    rows = []
    for name, page in labels["pages"].items():
        held = glob.glob(str(images.images_root() / page["sha256"][:2] / f"{page['sha256']}.*"))
        if not held:
            raise SystemExit(f"{name} is not in the image cache; collect it with scripts/collect_kyujanggak.py")
        with Image.open(held[0]) as handle:
            image = handle.convert("RGB")
        boxes, made = cutters.made(args.cutter, image, args.scale, tuple(args.fill))
        for row in labels["boxes"]:
            if row["page"] != name:
                continue
            best = max(range(len(boxes)), key=lambda index: iou(boxes[index], row["box"]), default=None)
            found = best is not None and iou(boxes[best], row["box"]) >= SAME
            rows.append({**row, "made": made[best] if found else None})
    report = {"cutter": args.cutter, "scale": args.scale, "fill": args.fill}
    for sample in ("first", "second", "both"):
        chosen = {name: page for name, page in labels["pages"].items() if sample in ("both", page["sample"])}
        report[sample] = score([row for row in rows if row["page"] in chosen], chosen)
        print(sample, json.dumps(report[sample], ensure_ascii=False))
    RESULTS.mkdir(parents=True, exist_ok=True)
    tag = ("" if args.scale == 1.0 else f"-x{args.scale:g}") + "".join(f"-fill{value:g}" for value in args.fill)
    (RESULTS / f"counts-{args.cutter}{tag}.json").write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n",
                                                        encoding="utf-8")


if __name__ == "__main__":
    main()
