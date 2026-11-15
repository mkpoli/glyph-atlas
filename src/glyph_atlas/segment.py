"""Cut the columns of a page that has no transcription.

`cut` places the tokens of a transcription on a line's cells. A page without one (the Kyujanggak
land registers) has the cells alone: the detector's boxes, the parts of a box long enough for two
characters, and the ink between boxes. Nothing says how many characters a column holds, so the
framing model (`models/cutter/frame.json`) decides: it gives every candidate box the probability
that it frames one character, and a column is cut into the candidates whose odds multiply highest.
A box becomes its parts when the parts frame characters better than the whole does, two boxes
become one when their union does, and unboxed ink becomes a box when it is more likely a character
than not.

A column runs down the page, and its cells are cut along it by the size of the column's characters.
A box the column leaves whole is then read alone, by its own shape, down it or across it, so two
small characters in a column of large ones, or two side by side, are cut apart. The detector's boxes
are cut or joined and never dropped: removing one is the reviewer's.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from . import cut
from .align import Detection
from .schema import Box, Line

#: The log-odds a candidate that is not one whole detection must gain over the detector's own boxes.
MARGIN = 0.0
#: Leaving a detection out of a column costs more log-odds than any candidate can: a box is cut or
#: joined, never lost.
DROP = 30.0


@dataclass
class Piece:
    """One character box of an untranscribed page."""

    box: Box
    kind: str  # as `cut.KINDS`
    probability: float  # the framing model's
    detections: tuple[int, ...]  # the page's detections the box stands for, held ones included; none for unboxed ink


@dataclass
class Column:
    """A run of the page's detections read as one line, down the page or across one box."""

    work: cut.Work
    members: list[int]
    #: The page's detections that lie inside a member, by that member: no cell of their own.
    held: dict[int, list[int]]


def columns_of(detections: Sequence[Detection], image) -> list[Column]:
    """The page's detections as columns of cells, then every long or wide box as a line of its own.

    A column gives its boxes the size of its characters. A box read alone has only its shape: a
    character is about as long as it is wide, so a box longer than that is offered in parts down
    it, and a wider one in parts across it, whatever the size of the characters around it.
    """
    from .column_proposals import columns

    if not detections:
        return []
    ink = cut.ink_map(image)
    sizes = cut.page_sizes(detections)
    boxes = [detection.box for detection in detections]
    position = {id(box): index for index, box in enumerate(boxes)}
    size = statistics.median(max(box.w, box.h) for box in boxes)
    found = [_column(f"column-{number}", True, [position[id(box)] for box in chain], detections, ink, sizes)
             for number, chain in enumerate(columns(boxes, size))]
    inside = {index for column in found for held in column.held.values() for index in held}
    for index, box in enumerate(boxes):
        if index in inside:
            continue
        for vertical, extent, breadth in ((True, box.h, box.w), (False, box.w, box.h)):
            if extent >= cut.TALL[0] * breadth:
                alone = _column(f"alone-{index}", vertical, [index], detections, ink, (breadth, breadth))
                if len(alone.work.cells) > 1:
                    found.append(alone)
    return found


def _column(name: str, vertical: bool, members: list[int], detections: Sequence[Detection], ink: np.ndarray,
            sizes: tuple[float, float]) -> Column:
    line = Line(id=name, page_id="", seq=0, vertical=vertical, text_raw="", text="")
    frame = cut.Frame(vertical, ink)
    own = [detections[index] for index in members]
    cells, line_sizes = cut.cells_of(line, own, frame, sizes)
    work = cut.Work(line_id=name, tokens=[], cells=cells, spans=cut.spans_of(cells, line_sizes),
                    costs=np.zeros((0, 0)), known=[], frame=frame, sizes=line_sizes)
    held = {members[outer]: [members[inner] for inner in inners] for outer, inners in cut.nested(own).items()}
    return Column(work=work, members=members, held=held)


def choose(work: cut.Work, framing: cut.Logistic, *, margin: float = MARGIN) -> list[tuple[cut.Span, float]]:
    """The candidates of a column whose odds of framing a character multiply highest, in order.

    Every cell is taken by one chosen candidate or left out: unboxed ink freely, a detection at
    `DROP`. A candidate that takes unboxed ink or joins detections must be more likely a character
    than not, so where the model doubts every candidate the detector's boxes stand. A candidate that
    is not a whole detection gives up `margin`.
    """
    cells, spans = work.cells, work.spans
    if not spans:
        return []
    framed = framing.probabilities([span.features for span in spans])
    starts: list[list[cut.Span]] = [[] for _ in cells]
    for span in spans:
        starts[span.start].append(span)
    best = [0.0] * (len(cells) + 1)
    taken: list[cut.Span | None] = [None] * len(cells)
    for boundary in range(len(cells) - 1, -1, -1):
        best[boundary] = best[boundary + 1] - (0.0 if cells[boundary].source == "ink" else DROP)
        for span in starts[boundary]:
            p = min(max(float(framed[span.index]), 1e-6), 1 - 1e-6)
            if span.kind not in ("box", "part") and p < 0.5:
                continue
            odds = math.log(p / (1 - p)) - (0.0 if span.kind == "box" else margin) + best[span.end]
            if odds > best[boundary]:
                best[boundary], taken[boundary] = odds, span
    chosen = []
    boundary = 0
    while boundary < len(cells):
        span = taken[boundary]
        if span is None:
            boundary += 1
        else:
            chosen.append((span, float(framed[span.index])))
            boundary = span.end
    return chosen


def cut_page(detections: Sequence[Detection], image, classifier, framing: cut.Logistic, *,
             refiner: Any = None, margin: float = MARGIN) -> list[Piece]:
    """The character boxes of a page with no transcription."""
    found = columns_of(detections, image)
    cut.read([column.work for column in found], image, classifier, refiner=refiner)
    return pieces_of(found, framing, margin=margin)


def pieces_of(found: Sequence[Column], framing: cut.Logistic, *, margin: float = MARGIN) -> list[Piece]:
    """The boxes chosen in columns the models have read: each column cut, then each box it left whole
    cut by its own shape."""
    alone = {column.members[0]: column for column in found if column.work.line_id.startswith("alone-")}
    pieces = []
    for column in found:
        if column.work.line_id.startswith("alone-"):
            continue
        for piece in _pieces(column, framing, margin):
            own = alone.get(piece.detections[0]) if piece.kind == "box" else None
            parts = _pieces(own, framing, margin) if own is not None else []
            pieces += parts if len(parts) > 1 else [piece]
    return pieces


def _pieces(column: Column, framing: cut.Logistic, margin: float) -> list[Piece]:
    work = column.work
    pieces = []
    for span, probability in choose(work, framing, margin=margin):
        made = []
        for cell in work.cells[span.start:span.end]:
            if cell.detection is not None and column.members[cell.detection] not in made:
                member = column.members[cell.detection]
                made += [member, *column.held.get(member, ())]
        pieces.append(Piece(box=work.frame.box(span.along, span.across), kind=span.kind,
                            probability=probability, detections=tuple(made)))
    return pieces
