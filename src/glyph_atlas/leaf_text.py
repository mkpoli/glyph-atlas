"""Headwords of a printed dictionary's half-leaf, placed by what the text says lies between them.

A woodblock rime book or character dictionary prints each entry as one large headword followed by
its gloss in small characters, two to a column's width, and runs the entries down ruled columns.
A transcription that gives every headword's column and its order there, and the length of every
gloss, fixes the page up to three numbers: the height a headword takes (`head`), the height of one
row of gloss (`row`), and where each column's first headword stands. From one headword's top to the
next is

    head + ceil(gloss length / 2) * row

so the tops of a column's headwords are known once those are.

`axes` finds the columns among the detected character boxes: the headword-sized boxes of a
half-leaf gather on the columns' centre lines. `fit` chooses `head`, `row` and each column's
offset as the values that put most detected boxes on a predicted top. `align` then pairs a
column's headwords, in order, with its detected boxes of headword height so that the gaps between
paired boxes differ least from the predicted ones; a row miscounted in one gloss costs that gap
alone. Where no box stands, the headword's cell is the predicted one, measured from the nearest
paired headword.

The detector alone does not tell a headword from its gloss by size: it joins two gloss characters
standing side by side into one box as wide as a headword, and misses some headwords. The text
decides where a headword is; the detector only sharpens the cell. A joined pair of gloss
characters is told from a headword by its strokes, which are too thin to outlast an erosion of
a pixel or two (`bold`).

The detector finds these headwords best on a page reduced until they are about 50 px a side
(`Detector.boxes_at(image, 0.8)` on an opening photographed at 2592 px): at full size it boxed half of
them, at 0.8 two thirds.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from itertools import pairwise

import numpy as np
from PIL import Image, ImageFilter, ImageOps

from .schema import Box

#: A headword-sized box, as a multiple of the page's median box side (`BIG[0]` up to `BIG[1]`).
BIG = (1.35, 3.0)
#: A box at least `WIDE` median sides wide is dropped when less than `THIN` of its ink outlasts erosion.
WIDE = 1.1
THIN = 0.005
STROKE = 30
#: A found column stands on a flat scan's comb within this share of the pitch.
COMB = 0.2
#: Headword boxes whose centres lie within this share of the median box side are one column.
SAME_COLUMN = 0.8
#: A candidate's centre lies within this share of the column pitch of the column's axis, and it is
#: at least `SPANS` of the pitch wide: a single gloss character is half a column wide and off axis.
ON_AXIS = 0.25
SPANS = 0.55
#: A box counts toward the fit at this share of the median headword height, with less weight below `FULL`.
SEEN = 0.55
FULL = 0.8
#: A detected box is taken for a headword when its height is within these shares of the median headword's.
HEADWORD_HEIGHT = (0.72, 1.5)
#: A box votes for an offset within this share of `head`; `TIE` votes are taken off for a spread of
#: one `head` among the agreeing boxes, which only orders sizes that gather the same votes.
VOTE = 0.18
TIE = 0.5
#: `align`'s costs, in gloss rows: a headword left without a box (`MISSED`), a headword a reader reads
#: on its box (`READ`, taken off) or refuses there (`REFUSED`), the most a gap may be off
#: (`GAP_LIMIT`), how many headwords in a row may go without a box (`SKIP`), and the weight and cap of
#: the voted offset on where the pairing starts (`START`, `START_CAP`).
MISSED = 1.2
READ = 0.8
REFUSED = 1.5
GAP_LIMIT = 2.5
SKIP = 3
START = 0.3
START_CAP = 1.0
#: How many voted offsets of a column `chain` chooses among (`PEAKS`), what a row off the text's flow
#: costs in votes (`FLOW`), how many rows are counted (`FLOW_CAP`), and where a column's first row
#: stands against the page's highest boxes, in rows (`FLOW_BIAS`: half a row above them).
PEAKS = 5
FLOW = 1.0
FLOW_CAP = 3.0
FLOW_BIAS = -0.5
#: The sizes `fit` tries: `head` as shares of the median headword height, `row` as shares of `head`.
HEADS = np.arange(0.85, 1.25, 0.025)
ROWS = np.arange(0.42, 0.60, 0.01)


@dataclass(frozen=True)
class Head:
    """One headword of a column: its character as transcribed and how many characters its gloss runs to."""

    char: str
    gloss: int


@dataclass(frozen=True)
class Fit:
    """A half-leaf's sizes in pixels and each column's offset: the top of its first headword."""

    head: float
    row: float
    offsets: Mapping[int, float]
    score: float


@dataclass(frozen=True)
class Placed:
    """A headword's cell on the page. `detected` when it is a box the detector drew, else the predicted cell.

    `error` is how far the cell's gaps to its paired neighbours in the column are off the text's, in
    gloss rows: the larger of the two, infinite for a cell with no paired neighbour or no box.
    """

    head: Head
    box: Box
    detected: bool
    error: float = float("inf")


def side(boxes: Sequence[Box]) -> float:
    """The page's typical box side: the median of the boxes' longer sides."""
    return float(np.median([max(b.w, b.h) for b in boxes])) if boxes else 0.0


def big(boxes: Sequence[Box], unit: float) -> list[Box]:
    """The headword-sized boxes of a page."""
    return [b for b in boxes if BIG[0] * unit <= max(b.w, b.h) <= BIG[1] * unit and 0.5 <= b.w / b.h <= 2]


def stroke(crop: Image.Image, reach: int = 1) -> float:
    """How much of a crop's ink outlasts an erosion of `reach` pixels: near 0 for small thin writing."""
    grey = ImageOps.autocontrast(crop.convert("L"))
    pixels = np.asarray(grey, dtype=float)
    threshold = (pixels.min() + pixels.mean()) / 2
    ink = int((pixels < threshold).sum())
    kept = int((np.asarray(grey.filter(ImageFilter.MaxFilter(2 * reach + 1)), dtype=float) < threshold).sum())
    return kept / max(1, ink)


def bold(image: Image.Image, boxes: Sequence[Box], unit: float) -> list[Box]:
    """The boxes without those as wide as a headword whose writing is thin: gloss characters the detector joined.

    The erosion reaches one pixel for every `STROKE` pixels of the page's median box side, rounded,
    so that a page seen larger is judged by much the same strokes; the rounding makes the test
    stricter just above a step (a side of 45 px erodes by two pixels, one of 44 by one).
    """
    reach = max(1, round(unit / STROKE))
    return [b for b in boxes
            if b.w < WIDE * unit or stroke(image.crop((b.x, b.y, b.x + b.w, b.y + b.h)), reach) >= THIN]


def axes(heads: Sequence[Box], unit: float) -> list[float]:
    """The x centres of the columns the headword-sized boxes stand on, left to right.

    Centres within `SAME_COLUMN` of each other are one column; a column needs two boxes, so that a
    stray box in the margin is not one.
    """
    xs = sorted(b.x + b.w / 2 for b in heads)
    groups: list[list[float]] = []
    for x in xs:
        if groups and x - groups[-1][-1] < SAME_COLUMN * unit:
            groups[-1].append(x)
        else:
            groups.append([x])
    return [float(np.median(g)) for g in groups if len(g) >= 2]


def gutter(columns: Sequence[float], width: int, count: int) -> float | None:
    """The x between an opening's two half-leaves of `count` columns each.

    A flat scan shows the two half-leaves side by side with no more than a column's pitch between
    them: when the columns found span twice `count` less one pitch, the opening divides in the
    middle of that span. A photograph of an open book shows the binding between them: otherwise the
    opening divides in the widest gap between columns in the middle of the frame. None when the
    frame shows neither.
    """
    found = sorted(columns)
    if len(found) < 2:
        return None
    pitch = float(np.median(np.diff(found)))
    span = found[-1] - found[0]
    if round(span / pitch) == 2 * count - 1:
        return found[0] + span / 2
    gaps = [(found[i + 1] - found[i], i) for i in range(len(found) - 1) if 0.3 * width < found[i] < 0.7 * width]
    if not gaps:
        return None
    _, i = max(gaps)
    return (found[i] + found[i + 1]) / 2


def flat(columns: Sequence[float], count: int, width: int) -> tuple[float, float] | None:
    """A flat scan's middle and column pitch, from the columns found on the whole opening.

    Two half-leaves side by side are twice `count` evenly spaced columns. Every pitch near the
    median gap and every placing of a found column on one of those is tried, and the comb that most
    found columns stand on is taken; among equals, the one they stand on most closely, then the one
    whose middle is nearest the frame's, since a column missed at one edge leaves the comb free to slide. None when fewer than `count` columns
    stand on it.
    """
    found = np.asarray(sorted(columns), dtype=float)
    if len(found) < count:
        return None
    typical = float(np.median(np.diff(found)))
    best: tuple[tuple[int, float, float], float, float] | None = None
    for pitch in np.arange(0.85 * typical, 1.15 * typical, 0.5):
        for x in found:
            for index in range(2 * count):
                start = x - index * pitch
                k = np.rint((found - start) / pitch)
                off = np.abs(start + k * pitch - found) / pitch
                standing = (k >= 0) & (k < 2 * count) & (off < COMB)
                middle = start + (count - 0.5) * pitch
                key = (int(standing.sum()), -round(float(off[standing].sum()), 1), -abs(middle - width / 2))
                if best is None or key > best[0]:
                    best = (key, middle, float(pitch))
    if best is None or best[0][0] < count:
        return None
    return best[1], best[2]


def ruled(middle: float, pitch: float, count: int, right: bool) -> list[float]:
    """A half-leaf's column axes on a flat scan, column 1 (its rightmost) first: evenly spaced, the
    right half-leaf's ending at the opening's middle and the left one's beginning there."""
    return [middle + (count - line + 0.5) * pitch if right else middle - (line - 0.5) * pitch for line in range(1, count + 1)]


def complete(columns: Sequence[float], count: int) -> list[float] | None:
    """A half-leaf's `count` column axes, right to left, from those found (left to right).

    A column whose entries are all gloss holds no headword and leaves a gap of two pitches, two such
    columns one of three: every gap is filled with as many evenly spaced axes as pitches it spans
    less one. None when that does not make `count`.
    """
    found = sorted(columns)
    if len(found) < 2 or len(found) > count:
        return None
    pitch = float(np.median(np.diff(found)))
    whole = [found[0]]
    for left, right in pairwise(found):
        missing = round((right - left) / pitch) - 1
        whole += [left + (k + 1) * (right - left) / (missing + 1) for k in range(missing)]
        whole.append(right)
    return whole[::-1] if len(whole) == count else None


def candidates(boxes: Sequence[Box], x: float, pitch: float, height: float) -> list[Box]:
    """The boxes that could be a column's headwords, top to bottom: on its axis, as wide as the
    column and tall enough to be seen as one."""
    return sorted((b for b in boxes if abs(b.x + b.w / 2 - x) < ON_AXIS * pitch and b.w >= SPANS * pitch and b.h >= SEEN * height),
                  key=lambda b: b.y)


def tops(heads: Sequence[Head], head: float, row: float) -> np.ndarray:
    """Each headword's top below the column's first, in pixels."""
    gaps = [head + math.ceil(h.gloss / 2) * row for h in heads]
    return np.concatenate([[0.0], np.cumsum(gaps)])[:-1]


def fit(columns: Mapping[int, Sequence[Head]], found: Mapping[int, Sequence[Box]], height: float) -> Fit | None:
    """The sizes and column offsets under which most detected boxes stand on a predicted headword top.

    Every `head` in `HEADS` times the median headword height and every `row` in `ROWS` times `head`
    is tried. In each column every pairing of a box with a headword proposes an offset, and the
    column scores the weight of the boxes that agree on one; a box of headword height weighs 1, a
    shorter one 0.3. Sizes with the same votes are ordered by how closely the boxes agree. None when no column has both headwords and boxes.
    """
    best: Fit | None = None
    for head in HEADS * height:
        for ratio in ROWS:
            row = float(ratio * head)
            score, offsets = 0.0, {}
            for line, heads in columns.items():
                boxes = found.get(line) or ()
                if not heads or not boxes:
                    continue
                predicted = tops(heads, float(head), row)
                ys = np.array([b.y for b in boxes], dtype=float)
                weights = np.array([1.0 if b.h >= FULL * height else 0.3 for b in boxes])
                proposals = (ys[:, None] - predicted[None, :]).ravel()
                weight = np.repeat(weights, len(predicted))
                near = np.abs(proposals[:, None] - proposals[None, :]) < VOTE * head
                votes = near @ weight
                top = int(np.argmax(votes))
                offsets[line] = float(np.median(proposals[near[top]]))
                # Among sizes that gather the same votes, the one the boxes stand on most closely.
                spread = float(np.abs(proposals[near[top]] - offsets[line]).mean()) / head
                score += float(votes[top]) - TIE * spread
            if offsets and (best is None or score > best.score):
                best = Fit(float(head), row, offsets, score)
    return best


def peaks(heads: Sequence[Head], found: Sequence[Box], height: float, sizes: Fit, keep: int = PEAKS) -> list[tuple[float, float]]:
    """A column's most voted offsets under `sizes`, each with the weight of the boxes that agree on it."""
    if not heads or not found:
        return []
    predicted = tops(heads, sizes.head, sizes.row)
    ys = np.array([b.y for b in found], dtype=float)
    weights = np.repeat(np.array([1.0 if b.h >= FULL * height else 0.3 for b in found]), len(predicted))
    proposals = (ys[:, None] - predicted[None, :]).ravel()
    near = np.abs(proposals[:, None] - proposals[None, :]) < VOTE * sizes.head
    votes = near @ weights
    chosen: list[tuple[float, float]] = []
    for index in np.argsort(-votes):
        offset = float(np.median(proposals[near[index]]))
        if all(abs(offset - other) > 2 * VOTE * sizes.head for other, _ in chosen):
            chosen.append((offset, float(votes[index])))
        if len(chosen) == keep:
            break
    return chosen


def chain(columns: Mapping[int, Sequence[Head]], voted: Mapping[int, Sequence[tuple[float, float]]], sizes: Fit,
          top: float, bottom: float, count: int) -> dict[int, float]:
    """Each column's offset, chosen among its voted ones so that the text runs on from column to column.

    The gloss of a column's last headword fills what is left of the column and continues at the top
    of the next, so the next column's first headword stands that many rows below `top`; a column
    with no headword is all gloss, and one with headwords but no box to vote breaks the run, so
    nothing is asked of the column after it. A column's first row stands `FLOW_BIAS` rows from `top`. The offsets chosen are those that gather most votes less
    `FLOW` votes for each row a column starts away from where the text puts it (at most `FLOW_CAP`
    rows are counted, so a miscounted gloss does not rule a well-voted offset out).
    """
    capacity = (bottom - top) / sizes.row
    lines = [line for line in range(1, count + 1) if columns.get(line) and voted.get(line)]
    best: dict[int, list[tuple[float, int]]] = {}
    for at, line in enumerate(lines):
        scores = []
        for offset, votes in voted[line]:
            if at == 0:
                scores.append((votes, -1))
                continue
            before = lines[at - 1]
            # A column between with headwords of its own, but no box to vote, breaks the run of the text.
            runs_on = not any(columns.get(between) for between in range(before + 1, line))
            options = []
            for index, (earlier, _) in enumerate(voted[before]):
                last = earlier + tops(columns[before], sizes.head, sizes.row)[-1]
                rows = math.ceil(columns[before][-1].gloss / 2)
                fits = max(0, math.floor((bottom - (last + sizes.head)) / sizes.row + 0.25))
                left = max(0.0, rows - fits - (line - before - 1) * capacity)
                off = abs((offset - (top + left * sizes.row)) / sizes.row - FLOW_BIAS) if runs_on else 0.0
                options.append((best[before][index][0] + votes - FLOW * min(FLOW_CAP, off), index))
            scores.append(max(options))
        best[line] = scores
    chosen: dict[int, float] = {}
    index = int(np.argmax([score for score, _ in best[lines[-1]]])) if lines else -1
    for line in reversed(lines):
        chosen[line] = voted[line][index][0]
        index = best[line][index][1]
    return chosen


def align(heads: Sequence[Head], found: Sequence[Box], x: float, pitch: float, height: float, sizes: Fit, offset: float,
          reads: Callable[[str, Box], bool | None] | None = None) -> tuple[list[Placed], float]:
    """A column's headwords paired in order with its detected boxes of headword height, and how well they fit.

    The pairing is the one whose gaps between paired boxes differ least from the gaps the text
    predicts, counted in gloss rows; a headword may go without a box (`MISSED` rows each) and a box
    without a headword. `offset`, the column's first top as `fit` voted it, weighs lightly on where
    the pairing starts. `reads(char, box)` is a reader's verdict on a headword in a box: True takes
    `READ` rows off the pairing's cost, False adds `REFUSED`, None (the reader does not know the
    character) leaves it. A headword without a box takes the predicted cell, measured from the nearest
    paired one. Returns the cells, each with its own gap error, and the mean gap error in rows over
    the paired headwords (inf when fewer than two are paired).
    """
    boxes = [b for b in found if HEADWORD_HEIGHT[0] * height <= b.h <= HEADWORD_HEIGHT[1] * height]
    predicted = tops(heads, sizes.head, sizes.row)
    n, m = len(heads), len(boxes)
    verdict = np.zeros((n, m))
    if reads is not None:
        for k in range(n):
            for j in range(m):
                said = reads(heads[k].char, boxes[j])
                verdict[k, j] = 0.0 if said is None else -READ if said else REFUSED
    cost = np.full((n, m), np.inf)
    back = np.full((n, m, 2), -1, dtype=int)
    for k in range(n):
        for j in range(m):
            start = MISSED * k + min(START_CAP, START * abs(boxes[j].y - (offset + predicted[k])) / sizes.row)
            cost[k, j] = start + verdict[k, j]
            for p in range(max(0, k - SKIP - 1), k):
                for i in range(j):
                    if not np.isfinite(cost[p, i]):
                        continue
                    error = abs((boxes[j].y - boxes[i].y) - (predicted[k] - predicted[p])) / sizes.row
                    if error > GAP_LIMIT:
                        continue
                    total = cost[p, i] + error + MISSED * (k - p - 1) + verdict[k, j]
                    if total < cost[k, j]:
                        cost[k, j] = total
                        back[k, j] = (p, i)
    pairs: dict[int, int] = {}
    if n and m:
        ends = cost + MISSED * (n - 1 - np.arange(n))[:, None]
        k, j = (int(v) for v in np.unravel_index(int(np.argmin(ends)), ends.shape))
        if ends[k, j] >= MISSED * n:
            k = -1  # leaving every headword without a box costs less than any pairing
        while k >= 0:
            pairs[k] = j
            k, j = (int(v) for v in back[k, j])
    errors = []
    own: dict[int, float] = {}
    for p, k in pairwise(sorted(pairs)):
        error = abs((boxes[pairs[k]].y - boxes[pairs[p]].y) - (predicted[k] - predicted[p])) / sizes.row / (k - p)
        errors.append(error)
        own[p], own[k] = max(own.get(p, 0.0), error), max(own.get(k, 0.0), error)
    placed = []
    for k, head in enumerate(heads):
        if k in pairs:
            placed.append(Placed(head, boxes[pairs[k]], True, own.get(k, float("inf"))))
            continue
        anchor = min(pairs, key=lambda q: abs(q - k)) if pairs else None
        y = offset + predicted[k] if anchor is None else boxes[pairs[anchor]].y + predicted[k] - predicted[anchor]
        placed.append(Placed(head, Box(x=int(x - 0.42 * pitch), y=int(y - 0.04 * sizes.head), w=int(0.84 * pitch), h=int(0.99 * sizes.head)), False))
    return placed, float(np.mean(errors)) if errors else float("inf")


def place(boxes: Sequence[Box], columns: Mapping[int, Sequence[Head]], count: int,
          reads: Callable[[str, Box], bool | None] | None = None,
          lines: Sequence[float] | None = None) -> dict[int, tuple[list[Placed], float]] | None:
    """The headwords of one half-leaf, by column number (1 the rightmost), from the boxes detected on it.

    Each column gives its cells and its mean gap error in rows. `lines` are the column axes where
    they are known (`ruled`); otherwise they are found among the boxes. None when the half-leaf's
    `count` columns are not found or no size fits; empty when no column has a box to vote.
    """
    unit = side(boxes)
    heads = big(boxes, unit)
    if lines is None:
        lines = complete(axes(heads, unit), count)
    if lines is None or not heads:
        return None
    pitch = float(np.median(np.abs(np.diff(lines))))
    height = float(np.median([b.h for b in heads]))
    found = {line: candidates(boxes, x, pitch, height) for line, x in enumerate(lines, 1)}
    sizes = fit(columns, found, height)
    if sizes is None:
        return None
    ys, ends = sorted(b.y for b in boxes), sorted(b.y + b.h for b in boxes)
    top, bottom = float(ys[int(0.02 * len(ys))]), float(ends[int(0.98 * len(ends))])
    voted = {line: peaks(columns[line], found[line], height, sizes) for line in columns if columns[line]}
    offsets = chain(columns, voted, sizes, top, bottom, count)
    return {line: align(columns[line], found[line], lines[line - 1], pitch, height, sizes, offset, reads)
            for line, offset in offsets.items()}
