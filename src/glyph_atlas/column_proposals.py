"""Character boxes a page's columns imply where the detector left none, proposed for a reviewer to read.

A Tangut sutra page is written in regular vertical columns of about square characters. The detector,
trained on Japanese pages, finds most of them and passes over the rest: a single character here and
there, and on some pages a run of eight or ten down one column, or a column's first characters. The
boxes it did find give the columns and their pitch, so the characters it missed are the inked cells
of those columns that hold no box. A page whose boxes form no such columns (a cover, a frontispiece)
gets none. On the nine pages of EAP140/1/52 this adds 78 boxes to the detector's 1,333, each on a whole
character: 55 on page 9, where whole runs were missed, 16 on page 7 and 7 on page 5, none on the cover,
the frontispiece, the two faded leaves or the leaves' edges.

`propose` reads the boxes a page already has and chains them into columns. The ink no box holds is
taken stroke by stroke (connected ink), each stroke given to the column it lies in; down each column,
between its boxes and beyond its ends within the text block, the strokes are grouped into characters
of the column's height, and a character's box is the box of its whole strokes. A group too faint, too
flat or the wrong shape for a character is left out, as is one that is the rest of a character a box
holds only in part: that box wants fixing. `atlas review propose-columns` records them through
`Store.propose_units` under their own proposer, so the detector's boxes stay as they are.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from itertools import pairwise

import cv2
import numpy as np
from PIL import Image

from .character_proposals import Proposal
from .schema import Box

#: The family name of the proposer; a version is `columns-<digest>`.
FAMILY = "columns"

# Ink is what is darker than its paper by this many grey levels. The paper is the grey closing of the
# page over a window wider than a stroke, smoothed, and anything darker than `MAT` there is the scanner
# mat around the leaf, which holds no ink. Light lines narrower than `CRACK`, a crack in the leaf, are
# opened away first, or the paper beside them would read as ink.
CRACK = 7
CLOSING = 41
SMOOTHING = 61
CONTRAST = 45
MAT = 90
# A box joins the column whose last box is centred within this share of the page's character size.
ALIGN = 0.45
# A column's pitch is the median step from one box to the next, taken over steps shorter than `ADJACENT`
# character heights. A run of ink taller than `SPLIT` character heights is characters touching, cut at
# the rows of least ink within `SLACK` of a pitch from where the pitch puts each cut.
ADJACENT = 1.6
SPLIT = 1.5
SLACK = 0.25
# A row whose ink spans less than this share of the column's width is a joining stroke where a run
# meets a box or a cut.
THIN = 0.2
# A stroke lies in the column whose centre is within this share of the column's width of its own, and
# belongs to a box of the page when this share of it lies inside that box.
REACH = 0.6
TAKEN = 0.5
# A stroke of fewer pixels than this is a speck; a run of ink shorter than `SPECK` character heights may
# stand alone as one, at the cost `DROP`, where joining a character would make that character too tall.
DOT = 12
SPECK = 0.25
DROP = 0.3
# A character found above the highest box or below the lowest box of the columns reaches no further than
# this share of a character height beyond it; past that lie the leaf's edge and the scanner mat.
MARGIN = 0.15
# A character whose strokes reach this share of its character height into a box of its column, and are
# together no taller than `SPLIT` characters, is the part of a character that box holds only in part;
# that box wants fixing, and no box is proposed beside it. Taller strokes are characters touching.
STRADDLE = 0.25
# A character holds a stroke at least this share of its character height tall; the strata of a leaf's
# edge are flat.
STROKE = 0.35
# A cell holds a character when its ink covers this share of the column's width times its character
# height and reaches this share of that height.
INK = 0.1
HEIGHT = 0.5
# A proposed box is this share of the page's character size wide at least, and at most these shares
# wide and tall; a thinner one is the edge of the leaf, a larger one two characters.
NARROWEST = 0.5
WIDEST = 1.6
TALLEST = 1.4
# A proposed box covering more than this share of its area with a box of the page is that box.
OVERLAP = 0.15
# A column is read when it chains this many boxes, and a page when it has this many such columns: a
# cover, a frontispiece or a faded leaf gives scattered boxes that would chain into false columns.
SHORTEST = 6
FEWEST = 3
# A page is read when this share of its boxes lies in such columns. On EAP140/1/52 the text pages put
# 96 to 100% of their boxes in columns, and the cover, a damaged leaf, the frontispiece and a faded leaf
# 57 to 78%.
ORDERLY = 0.85
# A run of at most `LOOSE` boxes ending a column more than `STRAY` of the column's pitches from the rest
# is no part of it: the detector's boxes on a page number or the colour chart below the leaf.
STRAY = 3
LOOSE = 3


def proposer() -> str:
    """The proposer version: the settings that place a cell, hashed together."""
    settings = (CRACK, CLOSING, SMOOTHING, CONTRAST, MAT, ALIGN, ADJACENT, SPLIT, SLACK, THIN, REACH, TAKEN, DOT, SPECK,
                DROP, MARGIN, STRADDLE, STROKE, INK, HEIGHT, NARROWEST, WIDEST, TALLEST, OVERLAP, SHORTEST, FEWEST, ORDERLY, STRAY, LOOSE)
    return f"{FAMILY}-{hashlib.sha256(repr(settings).encode()).hexdigest()[:12]}"


def ink(image: Image.Image) -> np.ndarray:
    """True where `image` holds ink: darker than its paper, off the scanner mat, specks removed."""
    grey = cv2.medianBlur(np.asarray(image.convert("L")), 3)
    opened = cv2.morphologyEx(grey, cv2.MORPH_OPEN, np.ones((CRACK, CRACK), np.uint8))
    paper = cv2.blur(cv2.dilate(opened, np.ones((CLOSING, CLOSING), np.uint8)).astype(np.float32),
                     (SMOOTHING, SMOOTHING))
    mask = ((paper - grey > CONTRAST) & (paper > MAT)).astype(np.uint8)
    return cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8)).astype(bool)


def _centre(box: Box) -> float:
    return box.x + box.w / 2


def columns(boxes: Sequence[Box], size: float) -> list[list[Box]]:
    """`boxes` chained top to bottom into columns: each joins the column whose last box is centred
    nearest it, within `ALIGN` of `size`, or starts a column."""
    chains: list[list[Box]] = []
    for box in sorted(boxes, key=lambda box: box.y):
        near = [chain for chain in chains if abs(_centre(chain[-1]) - _centre(box)) < ALIGN * size]
        if near:
            min(near, key=lambda chain: abs(_centre(chain[-1]) - _centre(box))).append(box)
        else:
            chains.append([box])
    return chains


def trimmed(column: list[Box]) -> list[Box]:
    """`column` without a run of `LOOSE` boxes or fewer at either end lying more than `STRAY` pitches
    from the rest."""
    pitch = float(np.median([box.h for box in column]))
    gaps = [lower.y - (upper.y + upper.h) > STRAY * pitch for upper, lower in pairwise(column)]
    start = max((n + 1 for n in range(min(LOOSE, len(gaps))) if gaps[n]), default=0)
    end = min((len(column) - 1 - n for n in range(min(LOOSE, len(gaps))) if gaps[len(gaps) - 1 - n]),
              default=len(column))
    return column[start:end] if end - start >= 1 else column


def _shared(a: Box, b: Box) -> int:
    return (max(0, min(a.x + a.w, b.x + b.w) - max(a.x, b.x))
            * max(0, min(a.y + a.h, b.y + b.h) - max(a.y, b.y)))


def _trim(profile: np.ndarray, across: float, head: bool, tail: bool) -> tuple[int, int]:
    """The rows of `profile` left once the thin rows (ink across less than `THIN` of `across`) are taken
    off its head and its tail, as asked, as (start, end)."""
    if not head and not tail:
        return 0, len(profile)
    thick = np.flatnonzero(profile >= THIN * across)
    if not len(thick):
        return 0, 0
    return (int(thick[0]) if head else 0), (int(thick[-1]) + 1 if tail else len(profile))


def _segments(runs: list[tuple[int, int]], tall: float) -> list[list[int]]:
    """The runs of ink down a stretch of column, `(top, bottom)` in order, grouped into characters: the
    grouping whose characters come nearest `tall` high, where a short run may also stand alone as a speck.
    Each group is the indices of its runs; specks are left out."""
    best: list[tuple[float, list[list[int]]]] = [(0.0, [])]
    for end in range(1, len(runs) + 1):
        options = []
        top, bottom = runs[end - 1]
        if bottom - top < SPECK * tall:
            options.append((best[end - 1][0] + DROP, best[end - 1][1]))
        for start in range(end - 1, -1, -1):
            height = runs[end - 1][1] - runs[start][0]
            if height > 2 * tall:
                break
            options.append((best[start][0] + (height / tall - 1) ** 2, [*best[start][1], list(range(start, end))]))
        best.append(min(options, key=lambda option: option[0]) if options else
                    (best[end - 1][0] + DROP, best[end - 1][1]))
    return best[-1][1]


def propose(image: Image.Image, boxes: Sequence[Box]) -> list[Proposal]:
    """The character boxes the columns of `boxes` imply on `image` where none stands, top to bottom
    within each column. `boxes` are the page's boxes in pixels of `image`."""
    mask = ink(image)
    height, width = mask.shape
    # Boxes clipped to the image; one with nothing left on it, or no height or width, is left out.
    boxes = [Box(x=max(0, box.x), y=max(0, box.y), w=min(width, box.x + box.w) - max(0, box.x),
                 h=min(height, box.y + box.h) - max(0, box.y)) for box in boxes]
    boxes = [box for box in boxes if box.w > 0 and box.h > 0]
    if not boxes:
        return []
    size = float(np.median([max(box.w, box.h) for box in boxes]))
    read = [column for column in map(trimmed, columns(boxes, size)) if len(column) >= SHORTEST]
    if len(read) < FEWEST or sum(len(column) for column in read) < ORDERLY * len(boxes):
        return []
    # The text block the columns span.
    first = min(column[0].y for column in read)
    last = max(column[-1].y + column[-1].h for column in read)

    # The strokes: connected ink, each with its box, its centre and its pixel count. A stroke mostly
    # inside a box of the page belongs to that box's character.
    count, _, stats, centres = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    strokes = []
    for n in range(1, count):
        x, y, w, h, area = (int(v) for v in stats[n])
        if area < DOT or w > WIDEST * size or h > 3 * size:
            continue
        stroke = Box(x=x, y=y, w=w, h=h)
        if any(_shared(stroke, box) > TAKEN * w * h for box in boxes):
            continue
        strokes.append((stroke, float(centres[n][0]), float(centres[n][1]), area))

    # Each column's centre at a height, followed down its boxes; each free stroke goes to the nearest
    # column whose centre is within reach of it.
    lines = []
    for column in read:
        across = float(np.median([box.w for box in column]))
        lines.append(([box.y + box.h / 2 for box in column], [_centre(box) for box in column], across))
    owned: list[list[tuple[Box, float, float, int]]] = [[] for _ in read]
    for stroke in strokes:
        _, cx, cy, _ = stroke
        if not first - MARGIN * size <= cy <= last + MARGIN * size:
            continue
        offsets = [abs(cx - float(np.interp(cy, ys, xs))) / across for ys, xs, across in lines]
        nearest = int(np.argmin(offsets))
        if offsets[nearest] < REACH:
            owned[nearest].append(stroke)

    found: list[Box] = []
    for column, parts in zip(read, owned):
        tall = float(np.median([box.h for box in column]))
        across = float(np.median([box.w for box in column]))
        steps = [lower.y - upper.y for upper, lower in pairwise(column) if 0 < lower.y - upper.y < ADJACENT * tall]
        pitch = float(np.median(steps)) if steps else tall
        # The stretches between the column's boxes, each read on its own; a stroke reaching into a box is
        # read only outside it.
        walls = [None, *column, None]
        for upper, lower in pairwise(walls):
            ceiling = upper.y + upper.h if upper else 0
            floor = lower.y if lower else height
            middle = (upper.y + upper.h / 2 if upper else -np.inf, lower.y + lower.h / 2 if lower else np.inf)
            stretch = sorted((part for part in parts if middle[0] < part[2] < middle[1]
                              and part[0].y + part[0].h > ceiling and part[0].y < floor), key=lambda part: part[0].y)
            if not stretch:
                continue
            # Runs: strokes whose rows overlap, joined.
            runs: list[list] = []
            for part in stretch:
                top, bottom = max(part[0].y, ceiling), min(part[0].y + part[0].h, floor)
                if runs and top <= runs[-1][1]:
                    runs[-1][1] = max(runs[-1][1], bottom)
                    runs[-1][2].append(part)
                else:
                    runs.append([top, bottom, [part]])
            # A run cut off by a box of the column, or cut from the characters it touches, starts or ends in
            # the thin stroke joining it to them; those rows are left out. A run still too tall for one
            # character is characters touching, cut at the rows of least ink.
            pieces: list[tuple[int, int, list]] = []
            for top, bottom, members in runs:
                x0 = min(part[0].x for part in members)
                x1 = max(part[0].x + part[0].w for part in members)
                profile = mask[top:bottom, x0:x1].sum(1)
                start, end = _trim(profile, across, top == ceiling, bottom == floor)
                if end <= start:
                    continue
                if end - start <= SPLIT * tall:
                    pieces.append((top + start, top + end, members))
                    continue
                count = max(2, round((end - start) / pitch))
                cuts = [start]
                for n in range(1, count):
                    guess = start + n * (end - start) / count
                    low = max(cuts[-1] + 1, int(guess - SLACK * pitch))
                    high = min(end, int(guess + SLACK * pitch) + 1)
                    window = profile[low:high]
                    # The middle of the rows of least ink, where a joining stroke is all there is.
                    cuts.append(low + int(np.median(np.flatnonzero(window == window.min()))) if high > low
                                else int(guess))
                cuts.append(end)
                for n, (a, b) in enumerate(pairwise(cuts)):
                    shift, stop = _trim(profile[a:b], across, n > 0, n < count - 1)
                    a, b = a + shift, a + stop
                    if b <= a:
                        continue
                    cols = np.flatnonzero(mask[top + a:top + b, x0:x1].any(0))
                    piece = Box(x=x0 + int(cols[0]), y=top + a, w=int(cols[-1] - cols[0]) + 1, h=b - a)
                    pieces.append((top + a, top + b, [(piece, 0.0, 0.0, 0)]))
            for group in _segments([(top, bottom) for top, bottom, _ in pieces], tall):
                members = [part for n in group for part in pieces[n][2]]
                top, bottom = pieces[group[0]][0], pieces[group[-1]][1]
                x0 = min(part[0].x for part in members)
                x1 = max(part[0].x + part[0].w for part in members)
                box = Box(x=x0, y=top, w=x1 - x0, h=bottom - top)
                inked = int(mask[box.y:box.y + box.h, box.x:box.x + box.w].sum())
                reach = max(max(ceiling - part[0].y, part[0].y + part[0].h - floor) for part in members)
                whole = max(part[0].y + part[0].h for part in members) - min(part[0].y for part in members)
                straddles = reach >= STRADDLE * tall and whole <= SPLIT * tall
                stroked = max(part[0].h for part in members) >= STROKE * tall
                inside = first - MARGIN * size <= box.y and box.y + box.h <= last + MARGIN * size
                if inside and stroked and not straddles and inked > INK * across * tall and box.h > HEIGHT * tall:
                    found.append(box)
    kept: list[Box] = []
    for box in found:
        if not NARROWEST * size <= box.w <= WIDEST * size or box.h > TALLEST * size:
            continue
        if any(_shared(box, other) > OVERLAP * box.w * box.h for other in [*boxes, *kept]):
            continue
        kept.append(box)
    return [Proposal(x=box.x, y=box.y, w=box.w, h=box.h, score=0.0) for box in kept]
