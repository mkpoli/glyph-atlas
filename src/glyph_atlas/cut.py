"""Cut a transcribed line into its characters.

The transcription says how many characters a line holds and which; the page says where the ink is.
A line's detections are laid out as *cells* down the line: a detection, the parts of a detection
tall enough to hold two or three characters (cut where its ink thins, or the boxes the detector
found inside it), and the runs of ink the detector left unboxed between its boxes. A token of the
transcription may take any short run of neighbouring cells, so a box over two characters is cut,
two boxes over one character are joined and a character the detector missed is found in its ink.
Each candidate box is moved onto its character by the refiner (`refine`) before the classifier
reads it.

The alignment is the cheapest path through tokens and cells. A placement costs the classifier's
negative log probability of the token over the candidate's crop, and the framing model's negative
log probability that the candidate frames one character at all, since the classifier reads half a
character or two as surely as one.

A placement is accepted by a second model, from what the alignment knows about it: how the
classifier reads the crop, how much dearer every other placement of the token is, what cells it
took, how its size sits in its line and how its neighbours were placed. The character is the
transcriber's; the model answers only whether the box frames it. Both models are fitted on pages
whose boxes are known (`models/cutter`).
"""

from __future__ import annotations

import itertools
import json
import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from . import align
from .align import Detection, Token
from .schema import Box, Classification, Confidence, Line, ReviewState, Script, Unit, UnitKind

ROOT = Path(__file__).resolve().parents[2]
MODELS = ROOT / "models/cutter"
KINDS = ("box", "part", "merge", "ink", "mixed")

#: A match costs at most this, the classifier's floor (`align.PROBABILITY_FLOOR`).
FLOOR = -math.log(align.PROBABILITY_FLOOR)
#: The longest run of cells one token may take.
RUN = 3

WEIGHTS = {
    "skip-token": 8.0,      # a token with no ink of its own
    "skip-detection": 6.0,  # a detection the transcription has no token for
    "skip-ink": 1.5,        # unboxed ink the transcription has no token for
    "gap": 3.0,             # a gap the transcription marks
    "frame": 1.0,           # the framing model's -log probability of a candidate box
    "loose": 2.0,           # without a framing model: any candidate that is not one whole detection
}
#: A detection this many median heights tall is offered in two parts, and in three.
TALL = (1.35, 2.3)
#: A gap between detections this share of a median height is searched for ink.
GAP = 0.45


@dataclass
class Cell:
    """One piece of a line a token may take, alone or with its neighbours. Extents are `(start, end)`
    along the line and across it."""

    along: tuple[int, int]
    across: tuple[int, int]
    source: str  # "detection", "part" or "ink"
    score: float = 0.0
    detection: int | None = None
    parts: int = 1
    column: int = 0
    #: For a part: the ink where its detection was cut, as a share of the detection's mean row.
    valley: float = 0.0
    #: For a part: the extents of the detection it is part of, which all its parts together take.
    whole: tuple[tuple[int, int], tuple[int, int]] | None = None


@dataclass
class Span:
    """A run of cells as one candidate box."""

    start: int
    end: int
    along: tuple[int, int]
    across: tuple[int, int]
    kind: str  # "box", "part", "merge", "ink" or "mixed"
    score: float
    valley: float
    penalty: float = 0.0
    index: int = -1
    cells: int = 1
    features: dict[str, float] = field(default_factory=dict)


@dataclass
class Placement:
    """Where one token went, and what the alignment knows about the placement."""

    token: Token
    box: Box | None = None
    kind: str = "skip"
    cost: float = FLOOR
    features: dict[str, float] = field(default_factory=dict)
    probability: float = 0.0
    accepted: bool = False


class Frame:
    """The geometry of one line: along it and across it, whichever way it runs."""

    def __init__(self, vertical: bool, ink: np.ndarray):
        self.vertical = vertical
        self.ink = ink if vertical else ink.T

    def of(self, box: Box) -> tuple[tuple[int, int], tuple[int, int]]:
        if self.vertical:
            return (box.y, box.y + box.h), (box.x, box.x + box.w)
        return (box.x, box.x + box.w), (box.y, box.y + box.h)

    def box(self, along: tuple[int, int], across: tuple[int, int]) -> Box:
        if self.vertical:
            return Box(x=across[0], y=along[0], w=across[1] - across[0], h=along[1] - along[0])
        return Box(x=along[0], y=across[0], w=along[1] - along[0], h=across[1] - across[0])

    def patch(self, along: tuple[int, int], across: tuple[int, int]) -> np.ndarray:
        rows, columns = self.ink.shape
        return self.ink[max(0, along[0]):max(0, min(rows, along[1])), max(0, across[0]):max(0, min(columns, across[1]))]

    def tight(self, along: tuple[int, int], across: tuple[int, int]):
        """The extent of the ink inside a rectangle, or None when it holds none."""
        patch = self.patch(along, across)
        if patch.size == 0:
            return None
        rows = np.flatnonzero(patch.any(axis=1))
        columns = np.flatnonzero(patch.any(axis=0))
        if rows.size == 0:
            return None
        top, left = max(0, along[0]), max(0, across[0])
        return ((top + int(rows[0]), top + int(rows[-1]) + 1), (left + int(columns[0]), left + int(columns[-1]) + 1))


def ink_map(image) -> np.ndarray:
    """Which pixels of a page are ink: darker than the paper around them by a margin the page sets.

    The paper level is a median over a window about a thirteenth of the page's shorter side, wider
    than any stroke, so a brown or stained sheet counts as paper. The margin is Otsu's split of how
    much darker each pixel is, never under a fifth of the paper level.
    """
    import cv2

    grey = np.asarray(image.convert("L"))
    scale = max(1, min(grey.shape) // 400)
    small = cv2.resize(grey, (grey.shape[1] // scale, grey.shape[0] // scale), interpolation=cv2.INTER_AREA)
    paper = cv2.medianBlur(small, 31)
    paper = cv2.resize(paper, (grey.shape[1], grey.shape[0]), interpolation=cv2.INTER_LINEAR).astype(np.float32)
    darker = np.clip((paper - grey) / np.maximum(paper, 1.0), 0.0, 1.0)
    scaled = (darker * 255).astype(np.uint8)
    threshold, _ = cv2.threshold(scaled[scaled > 12], 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU) \
        if (scaled > 12).any() else (51, None)
    return scaled > max(51, threshold)


def _valleys(profile: np.ndarray, parts: int) -> list[tuple[int, float]]:
    """Where to cut a profile into `parts`: the thinnest row near each even division, with its share of the mean."""
    height = len(profile)
    if height < 8 * parts:
        return []
    smooth = np.convolve(profile, np.ones(3) / 3, mode="same")
    mean = float(smooth.mean()) or 1.0
    cuts = []
    for division in range(1, parts):
        start = int(height * (division - 0.4) / parts)
        end = max(start + 1, int(height * (division + 0.4) / parts))
        row = start + int(np.argmin(smooth[start:end]))
        cuts.append((row, float(smooth[row]) / mean))
    return cuts


def _ink_cells(frame: Frame, along: tuple[int, int], across: tuple[int, int], height: float, width: float,
               column: int) -> list[Cell]:
    """The runs of ink in a stretch of a line no detection covers, each a cell."""
    patch = frame.patch(along, across)
    if patch.size == 0:
        return []
    rows = patch.sum(axis=1)
    top = max(0, along[0])
    runs: list[list[int]] = []
    for row in np.flatnonzero(rows > 0):
        if runs and row - runs[-1][1] <= max(2, int(0.1 * height)):
            runs[-1][1] = int(row) + 1
        else:
            runs.append([int(row), int(row) + 1])
    cells = []
    for start, end in runs:
        if int(rows[start:end].sum()) < 0.02 * height * width or end - start < 3:
            continue
        pieces = [(start, end)]
        if end - start > TALL[0] * height:
            count = min(6, max(2, round((end - start) / height)))
            cuts = [start + cut for cut, _ in _valleys(rows[start:end].astype(np.float32), count)]
            edges = [start, *cuts, end]
            pieces = list(itertools.pairwise(edges))
        for first, last in pieces:
            tight = frame.tight((top + first, top + last), across)
            if tight is not None:
                cells.append(Cell(along=tight[0], across=tight[1], source="ink", column=column))
    return cells


def _nested(detections: Sequence[Detection]) -> dict[int, list[int]]:
    """Each detection that holds others, with the ones it holds: a box inside a larger box.

    The detector reports a character and, now and then, one of its halves, or two characters and the
    box around both. A held box is no cell of its own; two or more become the parts of their holder.
    """
    order = sorted(range(len(detections)), key=lambda index: -detections[index].box.w * detections[index].box.h)
    holders: dict[int, list[int]] = {}
    held: set[int] = set()
    for position, outer in enumerate(order):
        if outer in held:
            continue
        a = detections[outer].box
        for inner in order[position + 1:]:
            if inner in held:
                continue
            b = detections[inner].box
            shared = max(0, min(a.x + a.w, b.x + b.w) - max(a.x, b.x)) * max(0, min(a.y + a.h, b.y + b.h) - max(a.y, b.y))
            if shared >= 0.7 * b.w * b.h:
                holders.setdefault(outer, []).append(inner)
                held.add(inner)
    return holders


def cells_of(line: Line, detections: Sequence[Detection], frame: Frame,
             sizes: tuple[float, float]) -> tuple[list[Cell], tuple[float, float]]:
    """A line's cells in reading order: its detections, tall ones in parts, and the unboxed ink between.

    Also the median extent of the line's characters along and across it, the page's (`sizes`) where
    the line has fewer than three detections."""
    from . import ainu

    if not detections:
        if line.box is None:
            return [], sizes
        along, across = frame.of(line.box)
        if across[1] - across[0] > 2.4 * sizes[1]:
            return [], sizes
        return _ink_cells(frame, along, across, sizes[0], sizes[1], 0), sizes
    holders = _nested(detections)
    inside = {inner for held in holders.values() for inner in held}
    kept = [index for index in range(len(detections)) if index not in inside]
    extents = {index: frame.of(detections[index].box) for index in range(len(detections))}
    if len(kept) >= 3:
        height = statistics.median(extents[index][0][1] - extents[index][0][0] for index in kept)
        width = statistics.median(extents[index][1][1] - extents[index][1][0] for index in kept)
    else:
        height, width = sizes
    if line.vertical and len(kept) > 1:
        columns = [[kept[position] for position in column]
                   for column in ainu.columns_of([detections[index].box for index in kept])]
    else:
        columns = [kept]
    columns = [sorted(column, key=lambda index: extents[index][0][0]) for column in columns]
    limits = frame.of(line.box) if line.box is not None else None
    cells: list[Cell] = []
    for number, column in enumerate(columns):
        centre = statistics.median((extents[index][1][0] + extents[index][1][1]) / 2 for index in column)
        half = 0.65 * max(width, statistics.median(extents[index][1][1] - extents[index][1][0] for index in column))
        strip = (int(centre - half), int(centre + half))
        cursor = limits[0][0] if limits is not None and len(columns) == 1 else None
        for index in column:
            along, across = extents[index]
            if cursor is not None and along[0] - cursor >= GAP * height:
                cells += _ink_cells(frame, (cursor, along[0]), strip, height, width, number)
            parts = [Cell(along=extents[inner][0], across=extents[inner][1], source="part",
                          score=detections[inner].score, detection=index, parts=len(holders[index]), column=number,
                          whole=(along, across))
                     for inner in sorted(holders.get(index, ()), key=lambda inner: extents[inner][0][0])]
            if 2 <= len(parts) <= RUN and all(_stacked(a, b) for a, b in itertools.pairwise(parts)):
                cells += parts
            else:
                cells += _detection_cells(frame, along, across, detections[index].score, index, number, height)
            cursor = max(cursor or along[1], along[1])
        if limits is not None and len(columns) == 1 and cursor is not None and limits[0][1] - cursor >= GAP * height:
            cells += _ink_cells(frame, (cursor, limits[0][1]), strip, height, width, number)
    return cells, (height, width)


def _detection_cells(frame: Frame, along, across, score: float, index: int, column: int, height: float) -> list[Cell]:
    tall = (along[1] - along[0]) / max(1.0, height)
    parts = 3 if tall >= TALL[1] else 2 if tall >= TALL[0] else 1
    whole = [Cell(along=along, across=across, source="detection", score=score, detection=index, column=column)]
    if parts == 1:
        return whole
    profile = frame.patch(along, across).sum(axis=1).astype(np.float32)
    cuts = _valleys(profile, parts)
    if len(cuts) != parts - 1:
        return whole
    top = max(0, along[0])
    edges = [along[0], *[top + cut for cut, _ in cuts], along[1]]
    depth = max(share for _, share in cuts)
    cells = []
    for first, last in itertools.pairwise(edges):
        tight = frame.tight((first, last), across)
        if tight is None:
            return whole
        cells.append(Cell(along=tight[0], across=tight[1], source="part", score=score, detection=index,
                          parts=parts, column=column, valley=depth, whole=(along, across)))
    return cells


def spans_of(cells: Sequence[Cell], sizes: tuple[float, float]) -> list[Span]:
    """The runs of up to `RUN` neighbouring cells of one column that stack down the line, as candidate boxes.

    All the parts of a detection together are the detection's own box. Any other run longer than
    2.8 of the line's median extent is left out."""
    height = sizes[0]
    spans = []
    for start in range(len(cells)):
        for end in range(start + 1, min(len(cells), start + RUN) + 1):
            run = cells[start:end]
            if run[-1].column != run[0].column or (len(run) > 1 and not _stacked(run[-2], run[-1])):
                break
            along = (min(c.along[0] for c in run), max(c.along[1] for c in run))
            across = (min(c.across[0] for c in run), max(c.across[1] for c in run))
            kind = _kind(cells, start, end)
            if kind == "box" and run[0].whole is not None:
                along, across = run[0].whole
            elif kind != "box" and along[1] - along[0] > 2.8 * height:
                continue
            spans.append(Span(start=start, end=end, along=along, across=across, kind=kind, cells=len(run),
                              score=min(c.score for c in run), valley=max(c.valley for c in run)))
    for index, span in enumerate(spans):
        span.index = index
    return spans


def _stacked(first: Cell, second: Cell) -> bool:
    """Whether two cells follow each other down the line rather than standing side by side."""
    shared = min(first.across[1], second.across[1]) - max(first.across[0], second.across[0])
    narrow = min(first.across[1] - first.across[0], second.across[1] - second.across[0])
    return shared >= 0.3 * narrow and second.along[0] >= first.along[0] + 0.5 * (first.along[1] - first.along[0])


def _kind(cells: Sequence[Cell], start: int, end: int) -> str:
    run = cells[start:end]
    if all(cell.source == "ink" for cell in run):
        return "ink"
    if any(cell.source == "ink" for cell in run):
        return "mixed"
    # Whole detections: every detection the run touches has all its parts inside the run.
    whole = True
    detections = []
    for cell in run:
        if cell.detection not in detections:
            detections.append(cell.detection)
    for detection in detections:
        inside = sum(1 for cell in run if cell.detection == detection)
        if inside != run[[cell.detection for cell in run].index(detection)].parts:
            whole = False
    if whole:
        return "box" if len(detections) == 1 else "merge"
    return "part" if len(detections) == 1 else "mixed"


def _skips(cells: Sequence[Cell], weights: dict[str, float]) -> list[list[tuple[int, float]]]:
    """From each cell boundary, the ways to leave cells without a token: `(next boundary, cost)`."""
    moves: list[list[tuple[int, float]]] = [[] for _ in range(len(cells) + 1)]
    for index, cell in enumerate(cells):
        moves[index].append((index + 1, weights["skip-ink"] if cell.source == "ink" else weights["skip-detection"]))
        if cell.source == "part" and (index == 0 or cells[index - 1].detection != cell.detection):
            last = index
            while last < len(cells) and cells[last].detection == cell.detection:
                last += 1
            if last - index > 1:
                moves[index].append((last, weights["skip-detection"]))
    return moves


def _edges(tokens: Sequence[Token], spans: Sequence[Span], cells: Sequence[Cell], costs: np.ndarray,
           weights: dict[str, float]):
    """Every transition of the lattice as `(token index, from boundary, to boundary, cost, span or None, places)`.

    `places` is True for a transition that settles a token (on a span, or with no ink)."""
    skips = _skips(cells, weights)
    by_start: list[list[Span]] = [[] for _ in range(len(cells) + 1)]
    for span in spans:
        by_start[span.start].append(span)
    edges = []
    for i in range(len(tokens) + 1):
        token = tokens[i] if i < len(tokens) else None
        blank = token is not None and token.text.isspace()
        for c in range(len(cells) + 1):
            if token is not None:
                free = 0.0 if blank else weights["gap"] if token.kind is UnitKind.GAP else weights["skip-token"]
                edges.append((i, c, i + 1, c, free, None, True))
                if not blank and token.kind is not UnitKind.GAP:
                    for span in by_start[c]:
                        edges.append((i, c, i + 1, span.end, float(costs[i, span.index]) + span.penalty, span, True))
            for target, cost in skips[c] if c < len(cells) else ():
                edges.append((i, c, i, target, cost, None, False))
    return edges


def _lattice(tokens: Sequence[Token], cells: Sequence[Cell], edges) -> tuple[list, list[float], list[float]]:
    """The cheapest path's token edges, each token's margin, and each chosen edge's posterior.

    A state is (tokens settled, cells passed). Every edge moves one of the two forward, so a sweep in
    row-major order sees a state after all the edges into it. The margin of a token is how much
    dearer the cheapest whole path is that settles it any other way; the posterior is the chosen
    edge's share of all paths, each weighted by exp(-cost).
    """
    n, m = len(tokens), len(cells)
    infinity = float("inf")
    out: dict[tuple[int, int], list] = {}
    for edge in edges:
        out.setdefault((edge[0], edge[1]), []).append(edge)
    best = np.full((n + 1, m + 1), infinity)
    total = np.full((n + 1, m + 1), infinity)  # -log of the summed weight of the paths reaching a state
    back: dict[tuple[int, int], Any] = {}
    best[0, 0] = total[0, 0] = 0.0
    for i in range(n + 1):
        for c in range(m + 1):
            here, mass = best[i, c], total[i, c]
            if here == infinity:
                continue
            for edge in out.get((i, c), ()):
                target = (edge[2], edge[3])
                if here + edge[4] < best[target]:
                    best[target] = here + edge[4]
                    back[target] = edge
                total[target] = -np.logaddexp(-total[target], -(mass + edge[4]))
    after = np.full((n + 1, m + 1), infinity)
    rest = np.full((n + 1, m + 1), infinity)
    after[n, m] = rest[n, m] = 0.0
    for i in range(n, -1, -1):
        for c in range(m, -1, -1):
            for edge in out.get((i, c), ()):
                target = (edge[2], edge[3])
                after[i, c] = min(after[i, c], after[target] + edge[4])
                rest[i, c] = -np.logaddexp(-rest[i, c], -(rest[target] + edge[4]))
    chosen: list = [None] * n
    state = (n, m)
    while state != (0, 0) and state in back:
        edge = back[state]
        if edge[6]:
            chosen[edge[0]] = edge
        state = (edge[0], edge[1])
    cheapest = best[n, m]
    margins = [infinity] * n
    posteriors = [0.0] * n
    for edge in edges:
        if not edge[6]:
            continue
        i = edge[0]
        through = best[i, edge[1]] + edge[4] + after[edge[2], edge[3]]
        if chosen[i] is edge:
            mass = total[i, edge[1]] + edge[4] + rest[edge[2], edge[3]]
            posteriors[i] = float(math.exp(min(0.0, total[n, m] - mass)))
        elif through - cheapest < margins[i]:
            margins[i] = float(through - cheapest)
    return chosen, margins, posteriors


def describe(span: Span, frame: Frame, sizes: tuple[float, float], probabilities: np.ndarray) -> dict[str, float]:
    """What is known about a candidate box before any token is put on it."""
    along, across = span.along, span.across
    extent, breadth = along[1] - along[0], across[1] - across[0]
    patch = frame.patch(along, across)
    density = float(patch.mean()) if patch.size else 0.0
    before = frame.patch((along[0] - 3, along[0] - 1), across)
    after = frame.patch((along[1] + 1, along[1] + 3), across)
    left = frame.patch(along, (across[0] - 3, across[0] - 1))
    right = frame.patch(along, (across[1] + 1, across[1] + 3))
    top = float(probabilities.max())
    entropy = float(-(probabilities * np.log(np.maximum(probabilities, 1e-12))).sum())
    tall = math.log(max(extent, 1) / max(sizes[0], 1.0))
    wide = math.log(max(breadth, 1) / max(sizes[1], 1.0))
    return {
        **{f"kind_{kind}": float(span.kind == kind) for kind in KINDS},
        "cells": float(span.cells), "tall": tall, "wide": wide, "tall_abs": abs(tall), "wide_abs": abs(wide),
        "score": span.score, "valley": min(span.valley, 2.0), "top": top, "entropy": entropy, "density": density,
        "ink_before": float(before.any(axis=0).mean()) if before.size else 0.0,
        "ink_after": float(after.any(axis=0).mean()) if after.size else 0.0,
        "ink_beside": max(float(left.any(axis=1).mean()) if left.size else 0.0,
                          float(right.any(axis=1).mean()) if right.size else 0.0),
    }


class Logistic:
    """A logistic regression read from a JSON file: standardised features, weights, a bias."""

    def __init__(self, path: Path):
        model = json.loads(Path(path).read_text(encoding="utf-8"))
        self.names = model["features"]
        self.mean = np.array(model["mean"])
        self.scale = np.array(model["scale"])
        self.weights = np.array(model["weights"])
        self.bias = float(model["bias"])
        self.accept = float(model.get("accept", 0.5))
        self.version = model.get("version", "")

    def probabilities(self, rows: Sequence[dict[str, float]]) -> np.ndarray:
        if not rows:
            return np.zeros(0)
        values = np.array([[row[name] for name in self.names] for row in rows])
        return 1.0 / (1.0 + np.exp(-(((values - self.mean) / self.scale) @ self.weights + self.bias)))


@dataclass
class Work:
    """One container of a line with everything the alignment reads: no model runs after this."""

    line_id: str
    tokens: list[Token]
    cells: list[Cell]
    spans: list[Span]
    costs: np.ndarray
    known: list[bool]
    frame: Frame
    sizes: tuple[float, float]


def _iou(a: Box, b: Box) -> float:
    shared = max(0, min(a.x + a.w, b.x + b.w) - max(a.x, b.x)) * max(0, min(a.y + a.h, b.y + b.h) - max(a.y, b.y))
    return shared / (a.w * a.h + b.w * b.h - shared) if shared else 0.0


def prepare_page(lines: Sequence[Line], detections: Sequence[Detection], image, classifier, *,
                 refiner: Any = None, policy: str = "align-v1") -> list[Work]:
    """A page's lines as cells, candidate boxes and what the models read in each.

    With a refiner every candidate box is moved onto its character before the classifier reads it,
    unless the move leaves less than half of the box where it was: such a box was a candidate for
    other ink than the refiner chose.
    """
    ink = ink_map(image)
    height, width = page_sizes(detections)
    own = align.assign_page(lines, detections)
    works: list[Work] = []
    for line in lines:
        frame = Frame(line.vertical, ink)
        sizes = (height, width) if line.vertical else (width, height)
        for container in align.containers_of(line, own[line.id], policy):
            cells, line_sizes = cells_of(line, container.detections, frame, sizes)
            works.append(Work(line_id=line.id, tokens=list(container.tokens), cells=cells,
                              spans=spans_of(cells, line_sizes), costs=np.zeros((0, 0)), known=[], frame=frame,
                              sizes=line_sizes))
    spans = [(work, span) for work in works for span in work.spans]
    boxes = [work.frame.box(span.along, span.across) for work, span in spans]
    quality: list[tuple[float, float, float]] = [(0.0, 0.0, 0.0)] * len(boxes)
    if refiner is not None and boxes:
        refined = refiner.refine(np.asarray(image.convert("L")), boxes)
        for position, ((work, span), given, (box, after, before)) in enumerate(zip(spans, boxes, refined, strict=True)):
            kept = _iou(box, given)
            if kept >= 0.5:
                span.along, span.across = work.frame.of(box)
                boxes[position] = box
                quality[position] = (after, before, 1.0 - kept)
            else:
                quality[position] = (before, before, 0.0)
    crops = [image.crop((box.x, box.y, box.x + box.w, box.y + box.h)) for box in boxes]
    classes = classifier.classes
    index = {name: position for position, name in enumerate(classes)}
    probabilities = classifier.probabilities_many(crops) if crops else np.zeros((0, len(classes)))
    first = 0
    for work in works:
        own_rows = probabilities[first:first + len(work.spans)]
        work.costs = np.full((len(work.tokens), max(1, len(work.spans))), FLOOR)
        for i, token in enumerate(work.tokens):
            positions = [index[point] for point in token.code_points if point in index]
            work.known.append(bool(positions))
            if positions and len(work.spans):
                mass = own_rows[:, positions].sum(axis=1)
                work.costs[i, :len(work.spans)] = -np.log(np.maximum(mass, align.PROBABILITY_FLOOR))
        for offset, (span, row) in enumerate(zip(work.spans, own_rows, strict=True)):
            span.features = describe(span, work.frame, work.sizes, row)
            after, before, shift = quality[first + offset]
            span.features.update({"quality": after, "quality_given": before, "shift": shift})
        first += len(work.spans)
    return works


def place(work: Work, weights: dict[str, float], framing: Logistic | None) -> list[Placement]:
    """Place one container's tokens on its cells and describe each placement."""
    tokens, spans = work.tokens, work.spans
    if framing is not None and spans:
        framed = framing.probabilities([span.features for span in spans])
        for span, value in zip(spans, framed, strict=True):
            span.penalty = weights["frame"] * min(8.0, -math.log(max(float(value), 1e-6)))
    else:
        for span in spans:
            span.penalty = 0.0 if span.kind == "box" else weights["loose"]
    chosen, margins, posteriors = _lattice(tokens, work.cells, _edges(tokens, spans, work.cells, work.costs, weights))
    placements = []
    for i, token in enumerate(tokens):
        span = chosen[i][5] if chosen[i] is not None else None
        if span is None:
            placements.append(Placement(token=token, kind="gap" if token.kind is UnitKind.GAP else "skip"))
            continue
        placements.append(Placement(token=token, box=work.frame.box(span.along, span.across), kind=span.kind,
                                    cost=float(work.costs[i, span.index])))
    boxed = [p for p in placements if p.box is not None]
    line_cost = statistics.fmean(p.cost for p in boxed) if boxed else FLOOR
    whole = sum(p.kind == "box" for p in boxed) / max(1, len(placements))
    placed = len(boxed) / max(1, len(placements))
    for i, placement in enumerate(placements):
        span = chosen[i][5] if chosen[i] is not None else None
        if span is None:
            continue
        before = placements[i - 1] if i else None
        after = placements[i + 1] if i + 1 < len(placements) else None
        passed = chosen[i][1] - (chosen[i - 1][3] if i and chosen[i - 1] is not None else 0)
        placement.features = {
            **span.features,
            "cost": placement.cost,
            "leads": float(math.exp(-placement.cost) >= span.features["top"] - 1e-6),
            "known": float(work.known[i]),
            "margin": min(margins[i], 20.0),
            "log_posterior": math.log(max(posteriors[i], 1e-6)),
            "frame": span.penalty,
            "passed": float(max(0, passed)),
            "before": float(before is not None and before.box is not None),
            "after": float(after is not None and after.box is not None),
            "before_cost": before.cost if before is not None and before.box is not None else FLOOR,
            "after_cost": after.cost if after is not None and after.box is not None else FLOOR,
            "between": float(before is not None and before.kind == "box" and after is not None and after.kind == "box"),
            "line_cost": line_cost,
            "line_whole": whole,
            "line_placed": placed,
            "line_tokens": math.log(len(placements)),
            "kana": float(token.script in align.FLAG_SCRIPTS),
            "mark": float(token.kind in align.MARK_KINDS),
        }
        placement.features["cost_margin"] = placement.cost * min(placement.features["margin"], 10.0)
        placement.features["cost_whole"] = placement.cost * span.features["kind_box"]
    return placements


class Cutter:
    """The models a cut reads: the framing model the alignment weighs and the acceptance model."""

    def __init__(self, directory: Path = MODELS, *, weights: dict[str, float] | None = None, refiner: Any = None):
        self.weights = {**WEIGHTS, **(weights or {})}
        self.refiner = refiner
        self.framing = Logistic(directory / "frame.json") if (directory / "frame.json").exists() else None
        self.gate = Logistic(directory / "gate.json") if (directory / "gate.json").exists() else None

    @property
    def version(self) -> str:
        return self.gate.version if self.gate is not None else "ungated"

    def cut(self, lines: Sequence[Line], detections: Sequence[Detection], image, classifier, *,
            policy: str = "align-v1") -> dict[str, list[Placement]]:
        """Every line of a page cut into its tokens' placements, by line id."""
        result: dict[str, list[Placement]] = {line.id: [] for line in lines}
        for work in prepare_page(lines, detections, image, classifier, refiner=self.refiner, policy=policy):
            placements = place(work, self.weights, self.framing)
            boxed = [placement for placement in placements if placement.box is not None]
            if self.gate is not None and boxed:
                for placement, value in zip(boxed, self.gate.probabilities([p.features for p in boxed]), strict=True):
                    placement.probability = float(value)
                    placement.accepted = placement.probability >= self.gate.accept
            result[work.line_id] += placements
        return result


def page_sizes(detections: Sequence[Detection]) -> tuple[float, float]:
    """The page's median character height and width."""
    if not detections:
        return (48.0, 48.0)
    return (statistics.median(d.box.h for d in detections), statistics.median(d.box.w for d in detections))


def units_of(line: Line, placements: Sequence[Placement], *, run: str, version: str) -> list[Unit]:
    """The units a line's placements make, one a token, in the line's order."""
    units = []
    for sequence, placement in enumerate(placements, start=1):
        token = placement.token
        unicode = token.unicode
        classification = Classification.UNASSESSED
        if token.script is not Script.HIRAGANA and token.code_points:
            if len(token.code_points) == 1:
                unicode = next(iter(token.code_points))
                classification = Classification.IDENTIFIED
            else:
                classification = Classification.AMBIGUOUS
        if (token.script is Script.UNKNOWN and len(token.text) == 1) or (token.kind in align.MARK_KINDS and token.unicode):
            classification = Classification.IDENTIFIED
        boxed = placement.box is not None
        units.append(Unit(
            id=f"{line.id}:{version}:{sequence}", document_id=None, page_id=line.page_id, line_id=line.id,
            seq=sequence, box=placement.box, kind=token.kind, text_source=token.text or None, unicode=unicode,
            classification=classification, script=token.script,
            candidates=[align.Candidate(unicode=point, p=1.0 / len(token.code_points))
                        for point in sorted(token.code_points)],
            method="detect-align",
            confidence=Confidence(detection=placement.features.get("score") if boxed else None,
                                  segmentation=placement.probability if boxed else None,
                                  text=math.exp(-placement.cost) if boxed else None, model=version),
            review=ReviewState.MACHINE if placement.accepted else ReviewState.REJECTED,
            upstream={"source": "detect-align", "run": run, "cut": placement.kind}))
    return units


class BenchCutter:
    """The cutter as `models/benchmark/cuts.py` drives it."""

    def __init__(self, directory: Path = MODELS):
        from .classify import Classifier
        from .detect import Detector
        from .review.suggestions import classifier_path

        run = align.load_run(ROOT / "models/align/runs/collection-v2.yaml")
        self.detector = Detector(ROOT / run.detector, score=run.score, nms=run.nms)
        self.classifier = Classifier(classifier_path())
        from . import refine

        self.cutter = Cutter(directory, refiner=refine.Refiner() if refine.MODEL.exists() else None)
        self.placements: dict[str, list[Placement]] = {}

    def cut(self, image, lines):
        detections = [Detection(box=box, score=score) for box, score in self.detector.boxes(image)]
        self.placements = self.cutter.cut(lines, detections, image, self.classifier)
        return {line_id: [(p.box.model_dump() if p.box else None, p.accepted,
                           "accepted" if p.accepted else "no-box" if p.box is None else "gate")
                          for p in placements]
                for line_id, placements in self.placements.items()}


def bench_cutter(name: str) -> BenchCutter:
    if name == "lattice":
        return BenchCutter()
    if name == "lattice-raw":
        return BenchCutter(ROOT / "models/cutter/none")
    raise ValueError(f"no cutter named {name}")
