"""Headwords of a dictionary whose text runs on from column to column, placed on a page by order.

A table gives a page's headwords in the order they are printed and how long each one's gloss is; it
gives no column and no place in a column. The print sets a headword large and its gloss in two
half-columns, and lets a gloss run over a column's foot into the next. So a half-leaf's columns,
joined foot to head from the right, make one tape along which headwords stand at distances the text
predicts: a headword's height, then half the characters of its gloss in gloss rows.

`half` finds the columns of a half-leaf and the boxes that may be headwords. `features` of a crop are
compared with those of its headword drawn in a font (`Anchors`), which anchors the order where no
reader knows the character. `solve` gives the best order-preserving pairing and `posterior` the
probability of each pair over all of them. The font is a reference for comparing shapes, never a
source of one: what is drawn stays out of every record.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from itertools import pairwise

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .schema import Box

#: A gloss character's box is this much shorter than a headword's, in gloss heights.
TALL = 1.25
#: A headword box's least height, in gloss heights.
LEAST = 1.4
#: A headword box's least width in half-column pitches; a narrower tall box is half of one.
WIDE = 1.2
#: The least overlap of a headword's left and right parts, of the shorter.
MEETS = 0.6
#: The widest a headword's crop is taken, in half-column pitches either side of the column's middle.
SPAN = 0.9
#: How far a box's ink is followed past the box, of its height, and the blank rows that end it.
FOLLOW = 0.15
BLANK = 4
#: A row holds ink when this share of the column's width does.
INK = 0.04
#: A box is a part of another of its column when this share of its rows lies in it, and a divided box keeps this share of its height.
PART, KEPT = 0.6, 0.4
#: A missed headword costs as much as this many gloss rows of gap error.
MISSED = 1.0
#: The most headwords a pairing may pass over, and the largest gap error it may have, in gloss rows.
SKIP = 5
GAP = 3.0
#: A pair's shape cost is the similarity's lag behind the box's mean, over this, clipped.
SHAPE_UNIT = 0.10
SHAPE_CLIP = 2.5


@dataclass(frozen=True)
class Slot:
    """What the print sets in order: a headword, or a section title or tone header (`char` None and `entry` -1).

    `gloss` is how many characters follow it before the next. `char` is None for a headword the table
    leaves blank; it keeps its place and stays unlabelled."""

    char: str | None
    gloss: int
    entry: int
    sub: int = 0


@dataclass
class Half:
    """A half-leaf's grid and candidate headword boxes: (place on the tape, column, box), the rightmost column 0."""

    strength: float
    pitch: float
    tilt: float
    gloss: float
    top: float
    bottom: float
    columns: list[int]
    middle: Callable[[int, float], float]
    candidates: list[tuple[float, int, Box]]


def comb(boxes: Sequence[Box], lowest: float, highest: float) -> tuple[float, float, float, float]:
    """The half-column comb the boxes' centres fit best: strength, pitch, phase (radians) and tilt."""
    centres = np.array([(b.x + b.w / 2, b.y + b.h / 2) for b in boxes], float)
    best = (-1.0, 0.0, 0.0, 0.0)
    for pitch in np.linspace(lowest, highest, 61):
        for tilt in np.arange(-0.03, 0.0301, 0.002):
            mean = np.exp(2j * np.pi * (centres[:, 0] - centres[:, 1] * tilt) / pitch).mean()
            if abs(mean) > best[0]:
                best = (float(abs(mean)), float(pitch), float(np.angle(mean)), float(tilt))
    return best


def half(boxes: Sequence[Box], lowest: float, highest: float) -> Half | None:
    """The columns and candidate headword boxes of a half-leaf's boxes, or None where they have no grid."""
    if len(boxes) < 60:
        return None
    strength, pitch, phase, tilt = comb(boxes, lowest, highest)
    origin = phase / (2 * np.pi) * pitch
    centres = np.array([(b.x + b.w / 2, b.y + b.h / 2) for b in boxes], float)
    across = (centres[:, 0] - centres[:, 1] * tilt - origin) / pitch
    down = centres[:, 1] + centres[:, 0] * tilt
    gloss = float(np.median([b.h for b in boxes]))
    tall = [i for i, b in enumerate(boxes) if b.h >= TALL * gloss]
    wide = [i for i in tall if boxes[i].w >= WIDE * pitch]
    if len(wide) < 3:
        return None
    # A headword stands on the boundary of two half-columns, which fixes how they pair.
    odd = float(np.angle(np.exp(1j * np.pi * across[wide]).mean()) / np.pi) % 2
    column = np.round((across - odd) / 2).astype(int)
    found: list[tuple[int, Box, float]] = []
    taken: set[int] = set()
    for i in wide:
        if boxes[i].h >= LEAST * gloss:
            found.append((int(column[i]), boxes[i], float(down[i] - boxes[i].h / 2)))
    parts = [i for i in tall if i not in wide]

    def offset(i: int) -> float:
        return float(across[i] - odd - 2 * column[i])

    def overlap(i: int, j: int) -> float:
        a, b = boxes[i], boxes[j]
        return min(a.y + a.h, b.y + b.h) - max(a.y, b.y)
    for i in parts:
        if i in taken:
            continue
        mates = [j for j in parts if j != i and j not in taken and column[j] == column[i] and offset(i) * offset(j) < 0
                 and overlap(i, j) >= MEETS * min(boxes[i].h, boxes[j].h)]
        if mates:
            j = max(mates, key=lambda m: overlap(i, m))
            taken |= {i, j}
            a, b = boxes[i], boxes[j]
            x1, y1, x2, y2 = min(a.x, b.x), min(a.y, b.y), max(a.x + a.w, b.x + b.w), max(a.y + a.h, b.y + b.h)
            if y2 - y1 >= LEAST * gloss:
                found.append((int(column[i]), Box(x=x1, y=y1, w=x2 - x1, h=y2 - y1),
                              min(float(down[i] - a.h / 2), float(down[j] - b.h / 2))))
        else:
            taken.add(i)
            if boxes[i].h >= LEAST * gloss:
                found.append((int(column[i]), boxes[i], float(down[i] - boxes[i].h / 2)))
    heights = np.array([b.h for b in boxes]) / 2
    top, bottom = float(np.percentile(down - heights, 1)), float(np.percentile(down + heights, 99))
    used = sorted({int(c) for c in column if (column == c).sum() >= 6}, reverse=True)

    def middle(c: int, y: float) -> float:
        return origin + (odd + 2 * c) * pitch + y * tilt
    height = bottom - top
    # A column with too few boxes to count still takes its length on the tape.
    first = used[0] if used else 0
    candidates = sorted((((first - c) * height + place - top, c, box) for c, box, place in found if c in used), key=lambda z: z[0])
    return Half(strength, pitch, tilt, gloss, top, bottom, used, middle, candidates)


def apart(boxes: list[tuple[float, int, Box]], ink: np.ndarray, page: Half) -> list[tuple[float, int, Box]]:
    """Boxes of one column that overlap: one whose rows lie mostly in another's is a part of the same headword and is
    dropped, and two that share fewer rows are divided at the row between their middles that holds the least ink."""
    out: list[tuple[float, int, Box] | None] = list(boxes)
    by_column: dict[int, list[int]] = {}
    for n, (_, column, _) in enumerate(boxes):
        by_column.setdefault(column, []).append(n)
    for members in by_column.values():
        for n in sorted(members, key=lambda m: boxes[m][2].h):
            small = out[n][2] if out[n] else None
            for m in members:
                if small is None or m == n or out[m] is None:
                    continue
                large = out[m][2]
                shared = min(small.y + small.h, large.y + large.h) - max(small.y, large.y)
                if large.h >= small.h and shared >= PART * small.h:
                    out[n] = None
                    small = None
        rest = sorted((m for m in members if out[m] is not None), key=lambda m: out[m][2].y + out[m][2].h / 2)
        for upper, lower in pairwise(rest):
            a, b = out[upper][2], out[lower][2]
            if a.y + a.h <= b.y:
                continue
            first, last = int(a.y + a.h / 2), int(b.y + b.h / 2)
            middle = page.middle(out[upper][1], (a.y + b.y + b.h) / 2)
            x0, x1 = int(max(0, middle - SPAN * page.pitch)), int(min(ink.shape[1], middle + SPAN * page.pitch))
            if last - first < 4 or not 0 <= first < ink.shape[0]:
                continue
            row = first + int(np.argmin(ink[first:last, x0:x1].sum(1)))
            if row - a.y < KEPT * a.h or b.y + b.h - row < KEPT * b.h:
                continue
            out[upper] = (*out[upper][:2], Box(x=a.x, y=a.y, w=a.w, h=row - a.y))
            out[lower] = (*out[lower][:2], Box(x=b.x, y=row, w=b.w, h=b.y + b.h - row))
    return [o for o in out if o is not None]


def recut(image: Image.Image, page: Half) -> list[tuple[float, int, Box]]:
    """Each candidate cut again to its column: its ink followed a little past its box, as far as a blank gap,
    and divided from the next where they would overlap."""
    levels = np.asarray(image.convert("L"), float)
    ink = levels < 0.55 * np.percentile(levels, 1) + 0.45 * np.percentile(levels, 60)
    out = []
    for place, column, box in page.candidates:
        middle = page.middle(column, box.y + box.h / 2)
        x0, x1 = int(max(0, middle - SPAN * page.pitch)), int(min(ink.shape[1], middle + SPAN * page.pitch))
        first, last = int(max(0, box.y - 0.35 * box.h)), int(min(ink.shape[0], box.y + 1.35 * box.h))
        rows = ink[first:last, x0:x1].sum(1) > max(1.0, INK * (x1 - x0))  # a speck is no ink
        if len(rows) < 4:
            out.append((place, column, box))
            continue
        start = min(max(int(box.y) - first, 0), len(rows) - 1)
        end = min(max(int(box.y + box.h) - first - 1, 0), len(rows) - 1)

        def follow(row: int, step: int, rows: np.ndarray = rows, reach: float = FOLLOW * box.h) -> int:
            at, kept, blanks = row, row, 0
            while 0 <= at + step < len(rows) and abs(at + step - row) < reach:
                at += step
                if rows[at]:
                    kept, blanks = at, 0
                else:
                    blanks += 1
                    if blanks >= BLANK:
                        break
            return kept
        top = follow(start, -1) if rows[start] else next((r for r in range(start, end) if rows[r]), start)
        bottom = follow(end, 1) if rows[end] else next((r for r in range(end, start, -1) if rows[r]), end)
        columns = np.where(ink[first + top:first + bottom + 1, x0:x1].sum(0) > 0)[0]
        if len(columns) < 4 or bottom - top < 0.5 * box.h:
            out.append((place, column, box))
            continue
        out.append((place, column, Box(x=int(x0 + columns[0]), y=int(first + top), w=int(columns[-1] - columns[0] + 1), h=int(bottom - top + 1))))
    return apart(out, ink, page)


class Anchors:
    """The features of characters drawn in fonts, the first font that has each one."""

    def __init__(self, fonts: Sequence[str], features: Callable[[Image.Image], np.ndarray], size: int = 150) -> None:
        from fontTools.ttLib import TTFont

        self._features = features
        self._fonts = [(set(TTFont(path, fontNumber=0).getBestCmap()), ImageFont.truetype(path, size)) for path in fonts]
        self._drawn: dict[str, np.ndarray | None] = {}

    def __call__(self, char: str | None) -> np.ndarray | None:
        """The features of `char` as drawn, or None for a string no font draws as one character."""
        if not char or len(char) != 1:
            return None
        if char not in self._drawn:
            font = next((f for covered, f in self._fonts if ord(char) in covered), None)
            if font is None:
                self._drawn[char] = None
            else:
                image = Image.new("L", (220, 220), 255)
                ImageDraw.Draw(image).text((30, 20), char, font=font, fill=0)
                self._drawn[char] = self._features(image)
        return self._drawn[char]


def similarity(slots: Sequence[Slot], crops: np.ndarray, anchors: Callable[[str | None], np.ndarray | None]) -> np.ndarray:
    """How like each headword's drawing each crop is, (slots, crops), NaN for a slot with no drawing."""
    out = np.full((len(slots), len(crops)), np.nan)
    for k, slot in enumerate(slots):
        drawn = anchors(slot.char)
        if drawn is not None:
            out[k] = crops @ drawn
    return out


def _costs(slots: Sequence[Slot], page: Half, alike: np.ndarray, head: float, row: float):
    """The (shape, transitions) of a pairing: each slot's cost on each box, and between pairs the gap error in gloss rows."""
    places = np.array([c[0] for c in page.candidates], float)
    m = len(places)
    long = np.array([head + np.ceil(s.gloss / 2) * row for s in slots])
    start = np.concatenate([[0.0], np.cumsum(long)[:-1]])
    has = np.isfinite(alike)
    mean = np.array([alike[has[:, j], j].mean() if has[:, j].any() else 0.0 for j in range(m)])
    shape = np.where(has, np.clip(-(np.nan_to_num(alike) - mean) / SHAPE_UNIT, -SHAPE_CLIP, SHAPE_CLIP), 0.0)
    gaps = places[None, :] - places[:, None]
    after = np.arange(m)[:, None] < np.arange(m)[None, :]
    return shape, start, gaps, after


def _transition(start: np.ndarray, gaps: np.ndarray, after: np.ndarray, row: float, a: int, k: int) -> np.ndarray:
    error = np.abs(gaps - (start[k] - start[a])) / row
    return np.where(after & (error <= GAP), error + MISSED * (k - a - 1), np.inf)


def solve(slots: Sequence[Slot], page: Half, alike: np.ndarray, head: float, row: float) -> tuple[dict[int, int], float]:
    """The order-preserving pairing of slots and boxes of least cost, and that cost: gap errors in gloss rows, missed
    slots at `MISSED` each, and how far the boxes are from their headwords in shape."""
    n, m = len(slots), len(page.candidates)
    if not n or not m:
        return {}, float("inf")
    shape, start, gaps, after = _costs(slots, page, alike, head, row)
    cost = np.full((n, m), np.inf)
    back_slot, back_box = np.full((n, m), -1), np.full((n, m), -1)
    for k in range(n):
        cost[k] = MISSED * k + shape[k]
        for a in range(max(0, k - SKIP), k):
            total = cost[a][:, None] + _transition(start, gaps, after, row, a, k)
            i = np.argmin(total, axis=0)
            value = total[i, np.arange(m)] + shape[k]
            better = value < cost[k]
            cost[k], back_slot[k], back_box[k] = np.where(better, value, cost[k]), np.where(better, a, back_slot[k]), np.where(better, i, back_box[k])
    end = cost + MISSED * (n - 1 - np.arange(n))[:, None]
    k, j = (int(v) for v in np.unravel_index(int(np.argmin(end)), end.shape))
    total, pairs = float(end[k, j]), {}
    while True:
        pairs[k] = j
        if back_slot[k, j] < 0:
            return pairs, total
        k, j = int(back_slot[k, j]), int(back_box[k, j])


def posterior(slots: Sequence[Slot], page: Half, alike: np.ndarray, head: float, row: float) -> np.ndarray:
    """The probability of each (slot, box) pair over all order-preserving pairings, each weighted by its cost."""
    n, m = len(slots), len(page.candidates)
    shape, start, gaps, after = _costs(slots, page, alike, head, row)
    edges = {(a, k): -_transition(start, gaps, after, row, a, k) for k in range(n) for a in range(max(0, k - SKIP), k)}
    forward, backward = np.full((n, m), -np.inf), np.full((n, m), -np.inf)
    for k in range(n):
        terms = [np.full(m, -MISSED * k)] + [np.logaddexp.reduce(forward[a][:, None] + edges[a, k], axis=0) for a in range(max(0, k - SKIP), k)]
        forward[k] = np.logaddexp.reduce(np.stack(terms), axis=0) - shape[k]
    for k in range(n - 1, -1, -1):
        terms = [np.full(m, -MISSED * (n - 1 - k))] + [
            np.logaddexp.reduce(edges[k, c] + (backward[c] - shape[c])[None, :], axis=1) for c in range(k + 1, min(n, k + SKIP + 1))]
        backward[k] = np.logaddexp.reduce(np.stack(terms), axis=0)
    # The pairing that misses every slot is one of them.
    paired = (forward - MISSED * (n - 1 - np.arange(n))[:, None]).ravel()
    total = np.logaddexp(np.logaddexp.reduce(paired), -MISSED * n)
    return np.exp(forward + backward - total)


def unrivalled(slots: Sequence[Slot], alike: np.ndarray, n: int, j: int, lead: float, near: int) -> bool:
    """Whether box `j` is likest slot `n`'s headword of all the page's, and leads those `near` headwords off by `lead`.

    A crop paired one place off, or with a character the page's neighbours could also be, has a rival close to its
    own label. Headwords written with the same character are not rivals, and neither is a slot with no drawing."""
    own, char = alike[n, j], slots[n].char
    heads = [k for k, slot in enumerate(slots) if slot.entry > 0]  # a headword the table leaves blank is printed too
    if not char or not np.isfinite(own) or n not in heads:
        return False
    place = heads.index(n)
    rivals = [(abs(p - place), alike[k, j]) for p, k in enumerate(heads) if slots[k].char and slots[k].char != char and np.isfinite(alike[k, j])]
    close = [value for distance, value in rivals if distance <= near]
    return bool(close) and all(value <= own for _, value in rivals) and bool(own - max(close) > lead)
