"""Character boxes on a page printed in ruled columns, from the page photo and its transcription.

A block-printed page such as the 1446 訓民正音 (and its 1946 facsimile) sets one character per cell of
a grid: a thick frame, thin rules between the columns, and every column the same number of cells.
The transcription gives the characters in reading order but not where each one stands, and a cell
left blank (an indent, the end of a paragraph) has no character.

`page_boxes` finds the grid and fills it:

1. The frame is the innermost of the thick rules near each edge of the page.
2. The column rules are thin lines darker than the paper on both sides, running most of the frame's
   height. A rule too faint to find leaves a column twice as wide as the others, which is divided.
3. The number of columns names the layout (`LAYOUTS`: 7 columns of 11 cells, or 8 of 13 or 14).
   The row count and the grid's offset are the ones that put the least ink on the cell edges.
4. A cell is inked when the middle of it holds ink. The small circles the print sets beside a
   character, at the right of its cell, are left out of that middle.
5. The page is kept only when its inked cells are exactly as many as the characters of its text.
   Each column then takes its share of the text in order, and is cut into that many boxes at the
   rows with the least ink, each box near one cell tall. The cut follows the print where its
   spacing drifts from the grid, as it does in the preface and postface.

A page whose grid is not found, whose count differs, or whose column will not divide, is left out
with the reason. Nothing is guessed: the label of every box is the transcription's character at
that position.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from itertools import pairwise

import numpy as np

#: Cells per column for each number of columns the book is printed in.
LAYOUTS: dict[int, tuple[int, ...]] = {7: (11,), 8: (13, 14)}

#: Wikisource templates whose argument is printed text (a smaller or larger size of it).
_PRINTED = re.compile(r"\{\{(?:\*|더더크게|더크게|크게)\|([^{}]*)\}\}")
_MARKUP = (re.compile(r"\{\{[^{}]*\}\}"), re.compile(r"<[^>]+>"), re.compile(r"=+"))
#: Punctuation the transcription adds. The print marks a pause with a small circle beside the
#: character, in no cell of its own.
ADDED_PUNCTUATION = frozenset("，。、,.：；「」 \n\t")


@dataclass(frozen=True)
class Box:
    x: int
    y: int
    w: int
    h: int


def characters(wikitext: str) -> list[str]:
    """The printed characters of a page's wikitext in reading order, one per cell.

    A Hangul syllable spelt with conjoining jamo, and a character with its tone marks, are one cell.
    """
    text = _PRINTED.sub(r"\1", wikitext)
    for pattern in _MARKUP:
        text = pattern.sub("", text)
    out: list[str] = []
    for c in text:
        if c in ADDED_PUNCTUATION:
            continue
        cp = ord(c)
        joins = unicodedata.category(c) in ("Mn", "Mc") or 0x1160 <= cp <= 0x11FF or 0xD7B0 <= cp <= 0xD7FF
        if joins and out:
            out[-1] += c
        else:
            out.append(c)
    return out


def is_han(label: str) -> bool:
    return len(label) == 1 and unicodedata.name(label, "").startswith("CJK ")


def ink(gray: np.ndarray) -> np.ndarray:
    """Ink against the page's own paper tone."""
    return gray < np.median(gray) * 0.62


def _runs(mask: np.ndarray, gap: int = 2) -> list[tuple[int, int]]:
    idx = np.where(mask)[0]
    if not len(idx):
        return []
    groups = np.split(idx, np.where(np.diff(idx) > gap)[0] + 1)
    return [(int(g[0]), int(g[-1])) for g in groups]


def frame(b: np.ndarray) -> tuple[int, int, int, int] | None:
    """The inside of the printed frame, `(x0, x1, y0, y1)`: the innermost thick rule near each edge."""
    H, W = b.shape
    v = _runs(b[int(H * .2):int(H * .8)].mean(0) > 0.6)
    h = _runs(b[:, int(W * .2):int(W * .8)].mean(1) > 0.6)
    left = [r for r in v if r[1] < W * .35]
    right = [r for r in v if r[0] > W * .65]
    top = [r for r in h if r[1] < H * .35]
    bottom = [r for r in h if r[0] > H * .65]
    if not (left and right and top and bottom):
        return None
    return left[-1][1] + 1, right[0][0], top[-1][1] + 1, bottom[0][0]


def column_rules(gray: np.ndarray, x0: int, x1: int, y0: int, y1: int, *, d: int = 4, delta: float = 10) -> list[int]:
    """The x of every thin rule inside the frame: a line darker than the paper `d` pixels either side,
    in at least 30% of the frame's rows."""
    a = gray[y0:y1, x0 - d:x1 + d]
    line = a[:, d:-d] < np.minimum(a[:, :-2 * d], a[:, 2 * d:]) - delta
    share = line.mean(0)
    share = np.maximum(share, np.maximum(np.r_[0, share[:-1]], np.r_[share[1:], 0]))
    return [x0 + (lo + hi) // 2 for lo, hi in _runs(share > 0.3, gap=3)]


def columns(gray: np.ndarray, b: np.ndarray) -> tuple[tuple[int, int, int, int], list[tuple[int, int]]] | None:
    """The frame and its columns left to right, a column too wide for one divided into equal parts."""
    found = frame(b)
    if not found:
        return None
    x0, x1, y0, y1 = found
    edges = [x0] + [x for x in column_rules(gray, x0, x1, y0, y1) if x0 + 40 < x < x1 - 40] + [x1]
    spans = [(a + 3, c - 3) for a, c in pairwise(edges) if c - a > 40]
    if not spans:
        return None
    widths = sorted(c - a for a, c in spans)
    typical = widths[len(widths) // 2]
    out = []
    for a, c in spans:
        parts = max(1, round((c - a + 6) / (typical + 6)))
        step = (c - a + 6) / parts
        out += [(int(a + i * step), int(a + (i + 1) * step) - 6) for i in range(parts)]
    return found, out


def _cut_column(b: np.ndarray, x: int, w: int, top: int, bottom: int, n: int, pitch: float) -> list[Box] | None:
    """`n` boxes down one column between `top` and `bottom`, cut where the least ink crosses."""
    core = b[:, x + int(w * .1): x + w - int(w * .15)].mean(1)
    profile = np.convolve(core, np.ones(3) / 3, "same")
    rows = np.where(profile[top:bottom] > 0.01)[0]
    if not len(rows):
        return None
    start, end = top + int(rows[0]), top + int(rows[-1]) + 1
    lo, hi = int(pitch * .45), int(pitch * 1.5)
    layers: list[dict[int, tuple[float, int | None]]] = [{start: (0.0, None)}]
    for k in range(1, n + 1):
        layer: dict[int, tuple[float, int | None]] = {}
        targets = [end] if k == n else range(start + lo * k, min(end, start + hi * k) + 1)
        for y in targets:
            here = 0.0 if k == n else float(profile[y]) * 50
            shortest = lo * (0.6 if k in (1, n) else 1)
            best = None
            for prev, (cost, _) in layers[-1].items():
                length = y - prev
                if shortest <= length <= hi:
                    total = cost + here + ((length - pitch) / pitch) ** 2
                    if best is None or total < best[0]:
                        best = (total, prev)
            if best:
                layer[y] = best
        if not layer:
            return None
        layers.append(layer)
    if end not in layers[-1]:
        return None
    cuts = [end]
    for k in range(n, 0, -1):
        cuts.append(layers[k][cuts[-1]][1])
    cuts.reverse()
    boxes = []
    for a, c in pairwise(cuts):
        inked = np.where(core[a:c] > 0.02)[0]
        t, u = (a + int(inked[0]), a + int(inked[-1]) + 1) if len(inked) else (a, c)
        t, u = max(top, t - 6), min(bottom, u + 6)
        boxes.append(Box(x, t, w, u - t))
    return boxes


@dataclass
class PageCut:
    boxes: list[tuple[Box, str, int, int]]  # box, label, column (right to left), place in the column
    layout: str
    pitch: float


def page_boxes(gray: np.ndarray, wikitext: str) -> tuple[PageCut | None, str]:
    """The character boxes of a page with their labels, or None and the reason the page is left out."""
    b = ink(gray)
    found = columns(gray, b)
    if not found:
        return None, "no frame"
    (_, _, y0, y1), cols = found
    if len(cols) not in LAYOUTS:
        return None, f"{len(cols)} columns"
    profile = np.zeros(y1 - y0)
    for a, c in cols:
        profile += b[y0:y1, a + (c - a) // 6: c - (c - a) // 6].mean(1)
    profile = np.convolve(profile, np.ones(5) / 5, "same")
    best = None
    for rows in LAYOUTS[len(cols)]:
        pitch = (y1 - y0) / rows
        for offset in range(-int(pitch * .3), int(pitch * .3) + 1, 2):
            edges = [e for e in (int(offset + k * pitch) for k in range(1, rows)) if 0 <= e < len(profile)]
            middles = [m for m in (int(offset + (k + .5) * pitch) for k in range(rows)) if 0 <= m < len(profile)]
            cost = float(np.mean(profile[edges]) / (np.mean(profile[middles]) + 1e-6))
            if best is None or cost < best[0]:
                best = (cost, rows, pitch, offset)
    _, rows, pitch, offset = best
    labels = characters(wikitext)
    per_column: list[list[Box]] = []
    for a, c in reversed(cols):
        inked = []
        for r in range(rows):
            top, bottom = int(y0 + offset + r * pitch), int(y0 + offset + (r + 1) * pitch)
            w, h = c - a, bottom - top
            middle = b[max(0, top + int(h * .12)): bottom - int(h * .12), a + int(w * .12): c - int(w * .22)]
            if middle.size and middle.mean() > 0.02:
                inked.append(Box(a, top, w, h))
        per_column.append(inked)
    count = sum(map(len, per_column))
    if count != len(labels):
        return None, f"{count} inked cells, {len(labels)} characters"
    cut: list[tuple[Box, str, int, int]] = []
    at = 0
    for column, cells in enumerate(per_column):
        if not cells:
            continue
        top = max(y0 + 4, int(cells[0].y - .3 * pitch))
        bottom = min(y1 - 4, int(cells[-1].y + cells[-1].h + .3 * pitch))
        boxes = _cut_column(b, cells[0].x, cells[0].w, top, bottom, len(cells), pitch)
        if not boxes:
            return None, f"column {column + 1} does not divide into {len(cells)}"
        for place, box in enumerate(boxes):
            middle = b[box.y:box.y + box.h, box.x + int(box.w * .1): box.x + box.w - int(box.w * .15)]
            if middle.mean() < 0.03 or not .25 * pitch <= box.h <= 1.7 * pitch:
                return None, f"column {column + 1} has an empty or misshapen box"
            cut.append((box, labels[at], column, place))
            at += 1
    return PageCut(cut, f"{len(cols)}x{rows}", pitch), "cut"
