"""Give each detection on a page to one transcription line.

A line box from Honkoku-Lines v2.0 is 200 to 330 px wide on pages whose columns stand about 100 px
apart, so it holds its own column and most of each neighbour's. Taking every detection whose centre
lies inside the box gave one character to two or three lines: on 願書控帳 page 38 the 上 of 一上田屋
sits inside the boxes of its own line and of the next one, whose alignment labelled it 岸. About
1,700 published boxes stood under two lines that way.

The page's detections are grouped into ink columns (`ainu.columns_of`) and the columns are paired
with the lines, the way `ainu.pair_columns` pairs a page's columns with its transcribed lines:

- Lines whose boxes stand side by side, overlapping down most of their height, form a tier; a page
  with an upper and a lower register has two. A tier is read right to left.
- Lines of a tier that stand one above the other in the same column share a slot, so the column can
  go to both, each taking the detections inside its own box.
- Each slot takes one to `MAX_SPAN` neighbouring columns, keeping reading order. The cost of a pairing
  is the distance from the columns' ink to the slot's box centre, in column pitches, the share of the
  columns' ink that lies outside the slot's boxes, and the log ratio of the detections inside the
  boxes to the characters the slot's lines name. A column no slot takes is left unread, and a slot
  that takes no column leaves its lines without detections.
- A column the ink grouping chained across two lines, which happens on crowded pages whose characters
  wander sideways, is split first between the lines whose box centres it spans.

Two records of one line, with the same text and one box nearly inside the other, are collapsed
before any of this: `68C7…_25_012`, `_013` and `_015` all read 菖蒲ニ葉 on one column. The record with
the tighter box keeps the line.

A horizontal line keeps the detections inside its box that no vertical line took.
"""

from __future__ import annotations

import math
import statistics
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field

from . import ainu
from .schema import Box, Line

#: Two lines stand side by side when their boxes overlap vertically by more than this share of the
#: shorter one; less, and one stands above the other.
SIDE_OVERLAP = 0.3
#: Lines that stand one above the other share a slot when their box centres stand less than this
#: share of the tier's line pitch apart. A Honkoku-Lines box is two to three pitches wide, so the
#: boxes of an upper line and of a lower line in the next column overlap by more than half.
STACK_SHARE = 0.5
#: Two records with the same text are one line when the smaller box lies this much inside the other.
DUPLICATE_SHARE = 0.8
#: A slot may take at most this many neighbouring columns, when its ink splits where it leans.
MAX_SPAN = ainu.MAX_SPAN
#: What a slot pays for each column past the first.
SPAN_COST = ainu.SPAN_COST
#: What leaving a column unread costs, scaled by its ink as `ainu.pair_columns` scales it.
SKIP_COST = ainu.SKIP_COST
#: What a slot that takes no column costs. It is above what taking a neighbour's column at one pitch
#: off costs, so a line's own column is preferred to stealing one, and below what pairing a column of
#: three times the characters costs.
MISS_COST = 1.5


@dataclass
class Assignment:
    """Which detections each line of a page holds, by index into the page's detections."""

    lines: dict[str, list[int]] = field(default_factory=dict)
    #: Each collapsed record's id, with the id of the record that kept the line.
    duplicates: dict[str, str] = field(default_factory=dict)


def centre(box: Box) -> tuple[float, float]:
    return (box.x + box.w / 2, box.y + box.h / 2)


def inside(box: Box, point: tuple[float, float]) -> bool:
    return box.x <= point[0] <= box.x + box.w and box.y <= point[1] <= box.y + box.h


def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return min(a1, b1) - max(a0, b0)


def side_by_side(a: Box, b: Box) -> bool:
    """Whether two line boxes run beside each other down most of the shorter one's height."""
    return _overlap(a.y, a.y + a.h, b.y, b.y + b.h) > SIDE_OVERLAP * min(a.h, b.h)


def stacked(a: Box, b: Box, pitch: float) -> bool:
    """Whether two line boxes stand one above the other in one column of a page whose lines stand
    `pitch` apart."""
    return not side_by_side(a, b) and abs(centre(a)[0] - centre(b)[0]) < STACK_SHARE * pitch


def line_pitch(lines: Sequence[Line]) -> float:
    """How far apart the columns of `lines` stand: the median distance from a line's box centre to the
    nearest line standing beside it. Without such a pair, half the median box width, since a box is
    two to three columns wide."""
    steps = []
    for line in lines:
        beside = [abs(centre(line.box)[0] - centre(other.box)[0]) for other in lines
                  if other is not line and side_by_side(line.box, other.box)]
        beside = [step for step in beside if step > 0]
        if beside:
            steps.append(min(beside))
    return statistics.median(steps) if steps else statistics.median(line.box.w for line in lines) / 2


def _text(line: Line) -> str:
    return unicodedata.normalize("NFC", (line.text or "").strip())


def duplicates_of(lines: Sequence[Line]) -> dict[str, str]:
    """The records that repeat another line of their page, each with the id of the record kept.

    A repeat names the same text, and the smaller of the two boxes lies `DUPLICATE_SHARE` inside the
    other. The tighter box is kept, and of two the same size the earlier record.
    """
    ordered = sorted((line for line in lines if line.box is not None and _text(line)),
                     key=lambda line: (line.box.w * line.box.h, line.seq if line.seq is not None else math.inf,
                                       line.id))
    kept: list[Line] = []
    dropped: dict[str, str] = {}
    for line in ordered:
        box = line.box
        for other in kept:
            if other.page_id != line.page_id or _text(other) != _text(line):
                continue
            shared = (max(0.0, _overlap(box.x, box.x + box.w, other.box.x, other.box.x + other.box.w))
                      * max(0.0, _overlap(box.y, box.y + box.h, other.box.y, other.box.y + other.box.h)))
            if shared >= DUPLICATE_SHARE * min(box.w * box.h, other.box.w * other.box.h):
                dropped[line.id] = other.id
                break
        else:
            kept.append(line)
    return dropped


def _components(items: Sequence[Line], joined) -> list[list[Line]]:
    parent = list(range(len(items)))

    def root(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    for a in range(len(items)):
        for b in range(a + 1, len(items)):
            if joined(items[a], items[b]):
                parent[root(a)] = root(b)
    groups: dict[int, list[Line]] = {}
    for index, item in enumerate(items):
        groups.setdefault(root(index), []).append(item)
    return list(groups.values())


def _axis(lines: Sequence[Line]) -> float:
    return statistics.mean(centre(line.box)[0] for line in lines)


def slots_of(tier: Sequence[Line]) -> list[list[Line]]:
    """A tier's lines grouped into slots, right to left: lines one above another in a column share one.

    A line joins the slot whose box centre is nearest among those whose every line stands above or
    below it in its column.
    """
    pitch = line_pitch(tier)
    slots: list[list[Line]] = []
    for line in sorted(tier, key=lambda line: (-centre(line.box)[0], line.box.y)):
        homes = [slot for slot in slots if all(stacked(line.box, other.box, pitch) for other in slot)]
        home = min(homes, key=lambda slot: abs(_axis(slot) - centre(line.box)[0]), default=None)
        if home is None:
            slots.append([line])
        else:
            home.append(line)
    return sorted(slots, key=lambda slot: -_axis(slot))


def _median_x(boxes: Sequence[Box], indices: Sequence[int]) -> float:
    return statistics.median(centre(boxes[index])[0] for index in indices)


def split_chained(columns: list[list[int]], boxes: Sequence[Box], slots: Sequence[Sequence[Line]]
                  ) -> list[list[int]]:
    """`columns` with every column whose ink spans the box centres of two or more slots split between them.

    A detection of such a column goes to the slot whose box centre is nearest, among the slots that
    hold it inside a box; one no such slot holds stays with the nearest piece.
    """
    axes = [_axis(slot) for slot in slots]
    out: list[list[int]] = []
    for column in columns:
        xs = [centre(boxes[index])[0] for index in column]
        low, high = min(xs), max(xs)
        holders = [s for s, slot in enumerate(slots)
                   if low <= axes[s] <= high
                   and any(inside(line.box, centre(boxes[index])) for line in slot for index in column)]
        if len(holders) < 2:
            out.append(column)
            continue
        pieces: dict[int, list[int]] = {s: [] for s in holders}
        for index in column:
            point = centre(boxes[index])
            near = [s for s in holders if any(inside(line.box, point) for line in slots[s])] or holders
            pieces[min(near, key=lambda s: abs(axes[s] - point[0]))].append(index)
        out.extend(piece for piece in pieces.values() if piece)
    return sorted(out, key=lambda column: -_median_x(boxes, column))


def _pair(slots: Sequence[Sequence[Line]], columns: Sequence[Sequence[int]], boxes: Sequence[Box],
          pitch: float) -> list[list[int]]:
    """The columns each slot takes, as indices into `columns`, at the least cost; both read right to left."""
    held = [[[index for index in column if any(inside(line.box, centre(boxes[index])) for line in slot)]
             for column in columns] for slot in slots]
    wanted = [max(1, sum(ainu.characters_of(line) for line in slot)) for slot in slots]
    median = statistics.median(wanted)
    axes = [_axis(slot) for slot in slots]
    skip = [SKIP_COST * (0.25 + min(1.0, len(column) / median)) for column in columns]

    def extent(s: int, k: int) -> tuple[int, int]:
        mine = held[s][k]
        return min(boxes[i].y for i in mine), max(boxes[i].y + boxes[i].h for i in mine)

    def follows(s: int, first: int, last: int) -> bool:
        spans = [extent(s, k) for k in range(first, last + 1)]
        for a in range(len(spans)):
            for b in range(a + 1, len(spans)):
                shorter = min(spans[a][1] - spans[a][0], spans[b][1] - spans[b][0])
                if _overlap(*spans[a], *spans[b]) > ainu.MAX_SPAN_OVERLAP * shorter:
                    return False
        return True

    def cost(s: int, first: int, last: int) -> float:
        mine = [index for k in range(first, last + 1) for index in held[s][k]]
        ink = sum(len(columns[k]) for k in range(first, last + 1))
        geometry = abs(_median_x(boxes, mine) - axes[s]) / pitch
        outside = 1 - len(mine) / ink
        return (geometry + outside + abs(math.log(len(mine) / wanted[s]))
                + SPAN_COST * (last - first))

    count, total = len(slots), len(columns)
    best = [[math.inf] * (total + 1) for _ in range(count + 1)]
    step: list[list[int]] = [[0] * (total + 1) for _ in range(count + 1)]  # -1 misses a slot, 0 skips a column
    best[0][0] = 0.0
    for placed in range(count + 1):
        for used in range(total + 1):
            here = best[placed][used]
            if used and best[placed][used - 1] + skip[used - 1] < here:
                here, step[placed][used] = best[placed][used - 1] + skip[used - 1], 0
            if placed and best[placed - 1][used] + MISS_COST < here:
                here, step[placed][used] = best[placed - 1][used] + MISS_COST, -1
            if placed:
                for width in range(1, min(MAX_SPAN, used) + 1):
                    first = used - width
                    if not held[placed - 1][first]:
                        break
                    if width > 1 and not follows(placed - 1, first, used - 1):
                        break
                    value = best[placed - 1][first] + cost(placed - 1, first, used - 1)
                    if value < here:
                        here, step[placed][used] = value, width
            best[placed][used] = here
    spans: list[list[int]] = [[] for _ in slots]
    placed, used = count, total
    while placed or used:
        width = step[placed][used]
        if width == -1:
            placed -= 1
        elif width == 0:
            used -= 1
        else:
            spans[placed - 1] = list(range(used - width, used))
            placed -= 1
            used -= width
    return spans


def _depth(box: Box, point: tuple[float, float]) -> float:
    """How far inside `box` a point lies, as a share of the box's half size on its nearer axis."""
    return min((point[0] - box.x) / box.w, (box.x + box.w - point[0]) / box.w,
               (point[1] - box.y) / box.h, (box.y + box.h - point[1]) / box.h)


def assign(lines: Sequence[Line], boxes: Sequence[Box]) -> Assignment:
    """Give each of a page's detections, `boxes`, to at most one of its `lines`.

    Every line with a box is in the result, possibly with no detections, and so is every collapsed
    record, with none.
    """
    result = Assignment(duplicates=duplicates_of(lines))
    boxed = [line for line in lines if line.box is not None]
    for line in boxed:
        result.lines[line.id] = []
    vertical = [line for line in boxed if line.vertical and line.id not in result.duplicates]
    claims: dict[int, list[str]] = {}
    by_id = {line.id: line for line in boxed}
    for tier in _components(vertical, lambda a, b: side_by_side(a.box, b.box)):
        indices = [index for index, box in enumerate(boxes) if any(inside(line.box, centre(box)) for line in tier)]
        if not indices:
            continue
        slots = slots_of(tier)
        pitch = line_pitch(tier)
        local = ainu.columns_of([boxes[index] for index in indices])
        columns = split_chained([[indices[i] for i in column] for column in local], boxes, slots)
        for slot, span in zip(slots, _pair(slots, columns, boxes, pitch), strict=True):
            for k in span:
                for index in columns[k]:
                    point = centre(boxes[index])
                    homes = [line for line in slot if inside(line.box, point)]
                    if homes:
                        home = min(homes, key=lambda line: abs(centre(line.box)[1] - point[1]))
                        claims.setdefault(index, []).append(home.id)
    for index, owners in claims.items():
        point = centre(boxes[index])
        owner = max(owners, key=lambda line_id: _depth(by_id[line_id].box, point))
        result.lines[owner].append(index)
    taken = set(claims)
    horizontal = [line for line in boxed if not line.vertical and line.id not in result.duplicates]
    for index, box in enumerate(boxes):
        if index in taken:
            continue
        point = centre(box)
        homes = [line for line in horizontal if inside(line.box, point)]
        if homes:
            home = min(homes, key=lambda line: abs(centre(line.box)[1] - point[1]))
            result.lines[home.id].append(index)
    for found in result.lines.values():
        found.sort()
    return result
