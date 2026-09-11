"""Measure predicted units against truth units on the same pages.

A predicted unit is matched to a truth unit by IoU within a page, greedily by descending IoU, so
that one truth unit takes at most one prediction. From the matching, the module reports the joint
precision (box and label), box precision, label precision, coverage of the truth, split and merge
errors, and truth units no prediction reaches. Every rate carries a Wilson interval, and the overall
interval is also computed by a cluster bootstrap over pages, since units on one page are not
independent.

Labels are compared under the equivalence policy the caller names, so that a hentaigana form and the
modern kana it reads as count as one label when the policy says they do.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from statistics import mean

from .schema import Box, Unit

MISS_IOU = 0.3
# Share of a truth unit's width that a matched prediction must cover for the unit to count as
# swallowed, and the overlap in pixels it must have across the writing direction.
MERGE_SPAN = 0.25
MERGE_OVERLAP = 4


def coverage(inner: Box, outer: Box) -> float:
    """Share of `inner`'s area that lies inside `outer`; 0 when `inner` has no area."""
    left, top = max(inner.x, outer.x), max(inner.y, outer.y)
    right, bottom = min(inner.x + inner.w, outer.x + outer.w), min(inner.y + inner.h, outer.y + outer.h)
    if right <= left or bottom <= top:
        return 0.0
    area = inner.w * inner.h
    return ((right - left) * (bottom - top) / area) if area else 0.0


def iou(a: Box, b: Box) -> float:
    """Intersection over union of two boxes; 0 when they do not overlap."""
    left, top = max(a.x, b.x), max(a.y, b.y)
    right, bottom = min(a.x + a.w, b.x + b.w), min(a.y + a.h, b.y + b.h)
    if right <= left or bottom <= top:
        return 0.0
    intersection = (right - left) * (bottom - top)
    union = a.w * a.h + b.w * b.h - intersection
    return intersection / union if union else 0.0


def swallows(predicted: Box, truth: Box) -> bool:
    """True when a prediction box reaches well into a truth unit, so the unit reads as swallowed."""
    span = min(predicted.x + predicted.w, truth.x + truth.w) - max(predicted.x, truth.x)
    across = min(predicted.y + predicted.h, truth.y + truth.h) - max(predicted.y, truth.y)
    return span >= MERGE_SPAN * truth.w and span >= 1 and across >= MERGE_OVERLAP


def wilson(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for a proportion; (0, 1) when there is nothing to divide by."""
    if total == 0:
        return (0.0, 1.0)
    p = successes / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return (max(0.0, centre - half), min(1.0, centre + half))


@dataclass
class Pair:
    """One greedy match between a prediction and a truth unit."""

    predicted: str
    truth: str
    iou: float
    label: bool


@dataclass
class PageResult:
    page_id: str
    predicted: int
    truth: int
    matched: int
    joint: int
    box: int
    label: int
    splits: int
    merges: int
    missed: int
    false_positives: int
    unmatched_truth: int


@dataclass
class Report:
    pages: list[PageResult] = field(default_factory=list)
    pairs: list[Pair] = field(default_factory=list)

    @property
    def totals(self) -> dict[str, int]:
        return {
            "pages": len(self.pages),
            "predicted": sum(p.predicted for p in self.pages),
            "truth": sum(p.truth for p in self.pages),
            "matched": sum(p.matched for p in self.pages),
            "joint": sum(p.joint for p in self.pages),
            "box": sum(p.box for p in self.pages),
            "label": sum(p.label for p in self.pages),
            "splits": sum(p.splits for p in self.pages),
            "merges": sum(p.merges for p in self.pages),
            "missed": sum(p.missed for p in self.pages),
            "false_positives": sum(p.false_positives for p in self.pages),
            "unmatched_truth": sum(p.unmatched_truth for p in self.pages),
        }

    def rates(self) -> dict[str, tuple[float, tuple[float, float]]]:
        t = self.totals
        return {
            "joint precision": _rate(t["joint"], t["predicted"]),
            "box precision": _rate(t["box"], t["predicted"]),
            "label precision": _rate(t["label"], t["matched"]),
            "coverage": _rate(t["matched"], t["truth"]),
            "split errors": _rate(t["splits"], t["truth"]),
            "merge errors": _rate(t["merges"], t["truth"]),
            "missed regions": _rate(t["missed"], t["truth"]),
        }

    def bootstrap(self, key: Callable[[PageResult], tuple[int, int]], samples: int = 2000, seed: int = 0,
                  z: float = 1.96) -> tuple[float, float]:
        """Cluster bootstrap over pages for a rate given as (successes, total) per page."""
        rng = random.Random(seed)
        point = _rate(sum(key(p)[0] for p in self.pages), sum(key(p)[1] for p in self.pages))[0]
        if not self.pages:
            return (0.0, 1.0)
        draws = []
        for _ in range(samples):
            picked = [self.pages[rng.randrange(len(self.pages))] for _ in self.pages]
            successes = sum(key(p)[0] for p in picked)
            total = sum(key(p)[1] for p in picked)
            draws.append(successes / total if total else point)
        draws.sort()
        return (draws[max(0, int(0.025 * samples) - 1)], draws[min(samples - 1, int(0.975 * samples))])

    def markdown(self) -> str:
        t = self.totals
        lines = [
            "| measure | value | 95% interval | n |",
            "| --- | ---: | --- | ---: |",
        ]
        for name, (value, interval) in self.rates().items():
            denominator = {
                "joint precision": t["predicted"],
                "box precision": t["predicted"],
                "label precision": t["matched"],
                "coverage": t["truth"],
                "split errors": t["truth"],
                "merge errors": t["truth"],
                "missed regions": t["truth"],
            }[name]
            lines.append(f"| {name} | {value:.4f} | {interval[0]:.4f} to {interval[1]:.4f} | {denominator} |")
        joint_low, joint_high = self.bootstrap(lambda p: (p.joint, p.predicted))
        lines += [
            "",
            (
                f"Pages {t['pages']}, predictions {t['predicted']}, truth units {t['truth']}, "
                f"matched {t['matched']}, false positives {t['false_positives']}, "
                f"truth with no prediction within 0.3 IoU {t['missed']}."
            ),
            f"Joint precision, cluster bootstrap over pages: {joint_low:.4f} to {joint_high:.4f}.",
        ]
        return "\n".join(lines)


def _rate(successes: int, total: int) -> tuple[float, tuple[float, float]]:
    return (successes / total if total else 0.0, wilson(successes, total))


def compare(
    truth: Sequence[Unit],
    predicted: Sequence[Unit],
    *,
    page_of: Callable[[Unit], str | None] = lambda unit: unit.page_id,
    label_of: Callable[[Unit], str] | None = None,
    iou_threshold: float = 0.5,
    miss_iou: float = MISS_IOU,
) -> Report:
    """Match predictions to truth page by page.

    `label_of` returns the label a unit is compared under; by default the code point when it is set
    and the reading otherwise. Two units agree when their labels are equal.
    """
    if label_of is None:
        def label_of(unit: Unit) -> str:
            return unit.unicode or unit.reading or ""
    truth_by_page: dict[str, list[Unit]] = {}
    for unit in truth:
        if unit.active:
            truth_by_page.setdefault(page_of(unit) or "", []).append(unit)
    predicted_by_page: dict[str, list[Unit]] = {}
    for unit in predicted:
        if unit.active:
            predicted_by_page.setdefault(page_of(unit) or "", []).append(unit)

    report = Report()
    for page_id in sorted(set(truth_by_page) | set(predicted_by_page)):
        truths = [u for u in truth_by_page.get(page_id, []) if u.box is not None]
        predictions = [u for u in predicted_by_page.get(page_id, []) if u.box is not None]
        candidates: list[tuple[float, int, int]] = []
        for i, prediction in enumerate(predictions):
            for j, target in enumerate(truths):
                overlap = iou(prediction.box, target.box)
                if overlap >= iou_threshold:
                    candidates.append((overlap, i, j))
        candidates.sort(key=lambda item: (-item[0], item[1], item[2]))
        used_prediction: set[int] = set()
        used_truth: set[int] = set()
        matched = joint = box_ok = label_ok = 0
        for overlap, i, j in candidates:
            if i in used_prediction or j in used_truth:
                continue
            used_prediction.add(i)
            used_truth.add(j)
            agrees = label_of(predictions[i]) == label_of(truths[j])
            matched += 1
            box_ok += 1
            label_ok += int(agrees)
            joint += int(agrees)
            report.pairs.append(Pair(predictions[i].id, truths[j].id, overlap, agrees))
        # A split error is an unmatched prediction that overlaps two or more truth units, as when
        # one character on the page was cut in two. The overlap is counted at the lower threshold,
        # because a box that is exactly twice the size of a character has an IoU of exactly 0.5 with
        # it and the matching above would have taken either side at random.
        splits = sum(
            1 for i, prediction in enumerate(predictions) if i not in used_prediction
            and sum(iou(prediction.box, t.box) >= miss_iou for t in truths) >= 2
        )
        # A merge error is an unmatched truth unit that a matched prediction reaches into: the box
        # took one character and swallowed a neighbour. IoU alone does not see the swallowed unit,
        # because a box twice the size of a character has an IoU of exactly 0.5 with it and the
        # matching above took either side at random. The test is on width along the writing
        # direction and on overlap across it, so that a box two characters wide is caught while a
        # box that shares only a margin with the unit beside it is not.
        merges = sum(
            1 for j, target in enumerate(truths) if j not in used_truth
            and any(swallows(predictions[i].box, target.box) for i in used_prediction)
        )
        missed = sum(
            1 for j, target in enumerate(truths) if j not in used_truth
            and not any(iou(u.box, target.box) >= miss_iou for u in predictions)
        )
        report.pages.append(
            PageResult(
                page_id=page_id,
                predicted=len(predictions),
                truth=len(truths),
                matched=matched,
                joint=joint,
                box=box_ok,
                label=label_ok,
                splits=splits,
                merges=merges,
                missed=missed,
                false_positives=len(predictions) - matched,
                unmatched_truth=len(truths) - matched,
            )
        )
    return report


def mean_iou(pairs: Iterable[Pair]) -> float:
    values = [pair.iou for pair in pairs]
    return mean(values) if values else 0.0
