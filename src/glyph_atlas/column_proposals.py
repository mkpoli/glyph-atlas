"""Character boxes a page's columns imply where the detector left none, proposed for a reviewer to read.

A Tangut sutra page is written in regular vertical columns of about square characters. The detector,
trained on Japanese pages, finds most of them and passes over the rest: a single character here and
there, and on some pages a run of eight or ten down one column, or a column's first characters. The
boxes it did find give the columns and their pitch, so the characters it missed are the inked cells
of those columns that hold no box. A page whose boxes form no such columns (a cover, a frontispiece)
gets none. On the nine pages of EAP140/1/52 this adds 81 boxes to the detector's 1,333: 54 on page 9,
where whole runs were missed, 16 on page 7, 10 on page 5 and 1 on page 8, none on the cover, the
frontispiece and the two faded leaves. Of the 10 on page 5, 7 stand on a missed character, and one
lies between two close columns, one on half a character and one splits a character in two.

`propose` reads the boxes a page already has, chains them into columns, and proposes a box for each
inked cell between two boxes of a column and beyond its ends within the text block the columns span,
shrunk to the ink it holds. A cell holding too little ink, a box not shaped like a character, and a
box over one the page has are left out. `atlas review propose-columns` records them through
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
# mat around the leaf, which holds no ink.
CLOSING = 41
SMOOTHING = 61
CONTRAST = 45
MAT = 90
# A box joins the column whose last box is centred within this share of the page's character size.
ALIGN = 0.45
# A gap between two boxes of a column holds characters once it is this share of the column's character
# height.
GAP = 0.6
# A column's pitch is the median step from one box to the next, taken over steps shorter than `ADJACENT`
# character heights; a column with none takes its character height times `CELL`. A cut between two
# cells of a gap moves to the row of least ink within `SLACK` of a pitch.
ADJACENT = 1.6
CELL = 1.08
SLACK = 0.25
# Within a cell, ink parted from its main band by this share of the character height in empty rows is a
# neighbour's.
PARTED = 0.1
# A cell is read this share of the column's width either side of the column's centre.
REACH = 0.6
# A cell holds a character when its ink covers this share of the column's width times its character
# height and reaches this share of that height.
INK = 0.04
HEIGHT = 0.5
# A proposed box is this share of the page's character size wide at least, and at most these shares
# wide and tall; a thinner one is the edge of the leaf, a larger one two characters.
NARROWEST = 0.5
WIDEST = 1.6
TALLEST = 1.4
# A proposed box covering more than this share of its area with a box of the page is that box.
OVERLAP = 0.15
# A column end is followed at most this many cells.
STEPS = 40
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
    settings = (CLOSING, SMOOTHING, CONTRAST, MAT, ALIGN, GAP, ADJACENT, CELL, SLACK, PARTED, REACH, INK, HEIGHT, NARROWEST, WIDEST,
                TALLEST, OVERLAP, STEPS, SHORTEST, FEWEST, ORDERLY, STRAY, LOOSE)
    return f"{FAMILY}-{hashlib.sha256(repr(settings).encode()).hexdigest()[:12]}"


def ink(image: Image.Image) -> np.ndarray:
    """True where `image` holds ink: darker than its paper, off the scanner mat, specks removed."""
    grey = cv2.medianBlur(np.asarray(image.convert("L")), 3)
    paper = cv2.blur(cv2.dilate(grey, np.ones((CLOSING, CLOSING), np.uint8)).astype(np.float32),
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


def _holds(read: tuple[Box, int] | None, across: float, tall: float) -> bool:
    """True when a cell read as (box, ink) holds a character of a column `across` wide and `tall` high."""
    return read is not None and read[1] > INK * across * tall and read[0].h > HEIGHT * tall


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

    def cell(centre: float, reach: float, top: float, bottom: float, gap: int) -> tuple[Box, int] | None:
        """The ink of a cell, shrunk to its band of rows holding the most ink: rows parted from it by
        `gap` empty rows or more are a neighbour's stroke ends."""
        x0, x1 = max(0, int(centre - reach)), min(width, int(centre + reach))
        y0, y1 = max(0, int(top)), min(height, int(bottom))
        weight = mask[y0:y1, x0:x1].sum(1)
        rows = np.flatnonzero(weight)
        if not len(rows):
            return None
        bands = np.split(rows, np.flatnonzero(np.diff(rows) > gap) + 1)
        band = max(bands, key=lambda band: weight[band[0]:band[-1] + 1].sum())
        a, b = y0 + int(band[0]), y0 + int(band[-1]) + 1
        cols = np.flatnonzero(mask[a:b, x0:x1].any(0))
        box = Box(x=x0 + int(cols[0]), y=a, w=int(cols[-1] - cols[0]) + 1, h=b - a)
        return box, int(mask[box.y:box.y + box.h, box.x:box.x + box.w].sum())

    read = [column for column in map(trimmed, columns(boxes, size)) if len(column) >= SHORTEST]
    if len(read) < FEWEST or sum(len(column) for column in read) < ORDERLY * len(boxes):
        return []
    # The text block: a column end is followed no further than the highest and lowest box of the columns.
    first = min(column[0].y for column in read)
    last = max(column[-1].y + column[-1].h for column in read)
    found: list[Box] = []
    for column in read:
        tall = float(np.median([box.h for box in column]))
        across = float(np.median([box.w for box in column]))
        reach = REACH * across
        steps = [lower.y - upper.y for upper, lower in pairwise(column) if 0 < lower.y - upper.y < ADJACENT * tall]
        pitch = float(np.median(steps)) if steps else tall * CELL
        parted = max(2, int(PARTED * tall))

        for end, step in ((column[0], -1), (column[-1], 1)):
            edge = end.y if step < 0 else end.y + end.h
            for _ in range(STEPS):
                top, bottom = (edge - pitch, edge) if step < 0 else (edge, edge + pitch)
                if top < first or bottom > last:
                    break
                read = cell(_centre(end), reach, top, bottom, parted)
                if not _holds(read, across, tall):
                    break
                found.append(read[0])
                edge = read[0].y if step < 0 else read[0].y + read[0].h
        for upper, lower in pairwise(column):
            top, bottom = upper.y + upper.h, lower.y
            if bottom - top < GAP * tall:
                continue
            count = max(1, round((bottom - top - (pitch - tall)) / pitch))
            centre = (_centre(upper) + _centre(lower)) / 2
            x0, x1 = max(0, int(centre - reach)), min(width, int(centre + reach))
            profile = mask[:, x0:x1].sum(1)
            cuts = [top]
            for n in range(1, count):
                guess = top + n * (bottom - top) / count
                low = max(cuts[-1], int(guess - SLACK * pitch))
                high = min(bottom, height, int(guess + SLACK * pitch) + 1)
                cuts.append(low + int(np.argmin(profile[low:high])) if high > low else int(guess))
            cuts.append(bottom)
            for a, b in pairwise(cuts):
                read = cell(centre, reach, a, b, parted)
                if _holds(read, across, tall):
                    found.append(read[0])
    kept: list[Box] = []
    for box in found:
        if not NARROWEST * size <= box.w <= WIDEST * size or box.h > TALLEST * size:
            continue
        if any(_shared(box, other) > OVERLAP * box.w * box.h for other in [*boxes, *kept]):
            continue
        kept.append(box)
    return [Proposal(x=box.x, y=box.y, w=box.w, h=box.h, score=0.0) for box in kept]
