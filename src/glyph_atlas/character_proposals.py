"""Character boxes the detector finds on a page photo, proposed for a reviewer to read.

A source with page photos and no transcription (EAP140's Tangut sutras) has nothing to align
detections with, so each kept detection is proposed as it stands: a unit with no character, which
a reviewer gives one or removes. `atlas review propose-characters` records them through
`Store.propose_units`, the path the interlinear marks take.

The detector was trained on Japanese pages, and on Tangut manuscripts its scores stay low (median
about 0.04 on four EAP140 pages) while its boxes still sit on single characters. The operating point
is therefore the collection run's own, score 0.02 and NMS 0.2, with the detector's ink gate dropping
boxes on blank paper and show-through.

On the Kyujanggak land registers, whose vertical columns stack characters, the detector returns one
box over two characters here and there. `--split` reads the boxes of a page as columns, takes each
column's pitch from the steps between its boxes, and cuts a box of about two pitches at the row of
least ink where the pitch puts the cut.

The proposer's name carries the checksum of the detector, the operating point, the detector's ink
gate and tiling, and whether boxes were split, so a new model, threshold, gate or split is a new
version: `propose_units` records each version once per page and retires the untouched proposals of an
earlier one.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path
from typing import Any, Literal

import numpy as np
from PIL import Image

from .schema import Box

#: The family name of the proposer; a version is `characters-<digest>`.
FAMILY = "characters"
SCORE = 0.02
NMS = 0.2
#: A box this many pitches of its column tall holds two characters.
SPLIT_PITCHES = 1.5
#: A part shorter than this share of a pitch is a sliver, so its box is left whole.
MIN_PART = 0.4
#: A box this share of whose area lies in a larger box is that character's part, not a character.
NESTED = 0.9
#: Two boxes whose combined height is at most this many pitches are one character
#: when the paper between them is no more than `GAP` of a pitch.
MERGED = 1.25
GAP = 0.15


@dataclass(frozen=True)
class Proposal:
    """One proposed character box in image pixels and the detector's score for it."""

    x: int
    y: int
    w: int
    h: int
    score: float
    kind: Literal["character"] = "character"
    features: dict[str, Any] = field(default_factory=dict)


def proposer(onnx: Path, *, score: float = SCORE, nms: float = NMS, split: bool = False) -> str:
    """The proposer version: the detector's checksum, the operating point and the page geometry and
    ink gate `detect` applies, hashed together, and the split when boxes were cut."""
    from . import detect

    model = hashlib.sha256(Path(onnx).read_bytes()).hexdigest()
    settings: tuple[Any, ...] = (model, score, nms, detect.TILE, detect.OVERLAP, detect.CONTAIN, detect.INK,
                                 detect.INK_PERCENTILE, detect.INK_REFERENCE)
    if split:
        settings += (("split", 2, SPLIT_PITCHES, MIN_PART, NESTED, MERGED, GAP),)
    return f"{FAMILY}-{hashlib.sha256(repr(settings).encode()).hexdigest()[:12]}"


def propose(image: Image.Image, detector: Any, *, split: bool = False) -> list[Proposal]:
    """Every box the detector keeps on `image`, highest score first; with `split`, a box that holds two
    stacked characters of an orderly column becomes two boxes."""
    proposals = [Proposal(x=box.x, y=box.y, w=box.w, h=box.h, score=round(float(score), 4))
                 for box, score in detector.boxes(image)]
    return split_boxes(image, proposals) if split else proposals


def split_boxes(image: Image.Image, proposals: list[Proposal], *,
                pitches: float = SPLIT_PITCHES) -> list[Proposal]:
    """`proposals` refined for a page of columns: a box inside another character dropped, a box that
    holds two stacked characters cut, and two boxes that hold one character joined.

    The boxes a page's detector found give its columns and their pitch: the median step between
    neighbouring boxes of a column. A box of about two pitches holds two stacked characters, and is
    cut at the row of least ink within a quarter pitch of where the pitch puts the cut. Both halves
    keep the detection's score, order and features. A page whose boxes form no orderly column keeps
    them as they are, as does a box whose parts would be slivers.
    """
    from .column_proposals import ADJACENT, SHORTEST, SLACK, columns, ink, trimmed

    proposals = _drop_nested(proposals)
    if not proposals:
        return []
    boxes = [Box(x=p.x, y=p.y, w=p.w, h=p.h) for p in proposals]
    position = {id(box): index for index, box in enumerate(boxes)}
    mask = ink(image)
    size = float(np.median([max(box.w, box.h) for box in boxes]))
    chains = [chain for chain in (trimmed(column) for column in columns(boxes, size)) if len(chain) >= SHORTEST]
    cut: dict[int, list[Box]] = {}
    for chain in chains:
        steps = [lower.y - upper.y for upper, lower in pairwise(chain)]
        short = [step for step in steps if step < ADJACENT * size]
        if not short:
            continue
        pitch = float(np.median(short))
        for box in chain:
            parts = max(1, round(box.h / pitch))
            if parts < 2 or box.h < pitches * pitch or box.h / parts < MIN_PART * pitch:
                continue
            left, right = max(0, box.x), min(mask.shape[1], box.x + box.w)
            if left >= right:
                continue
            rows = []
            for part in range(1, parts):
                centre = box.y + part * pitch
                low = max(box.y + 1, int(centre - SLACK * pitch), 0)
                high = min(box.y + box.h - 1, int(centre + SLACK * pitch), mask.shape[0])
                if low >= high:
                    continue
                band = mask[low:high, left:right].sum(axis=1)
                rows.append(low + int(np.argmin(band)))
            edges = [box.y, *sorted(set(rows)), box.y + box.h]
            if len(edges) > 2:
                cut[position[id(box)]] = [Box(x=box.x, y=top, w=box.w, h=bottom - top)
                                          for top, bottom in pairwise(edges)]
    out: list[Proposal] = []
    for index, proposal in enumerate(proposals):
        for box in cut.get(index, [Box(x=proposal.x, y=proposal.y, w=proposal.w, h=proposal.h)]):
            out.append(Proposal(x=box.x, y=box.y, w=box.w, h=box.h, score=proposal.score,
                                kind=proposal.kind, features=dict(proposal.features)))
    return _merge_fragments(out)


def _drop_nested(proposals: list[Proposal]) -> list[Proposal]:
    """`proposals` without a box `NESTED` of whose area lies in a larger box: the detector read a part
    of a character as a character of its own."""
    boxes = [Box(x=p.x, y=p.y, w=p.w, h=p.h) for p in proposals]
    keep = []
    for index, box in enumerate(boxes):
        inner = any(
            other is not box and other.w * other.h > box.w * box.h
            and _overlap(box, other) >= NESTED * box.w * box.h
            for position, other in enumerate(boxes) if position != index
        )
        if not inner:
            keep.append(proposals[index])
    return keep


def _overlap(a: Box, b: Box) -> int:
    return (max(0, min(a.x + a.w, b.x + b.w) - max(a.x, b.x))
            * max(0, min(a.y + a.h, b.y + b.h) - max(a.y, b.y)))


def _merge_fragments(proposals: list[Proposal]) -> list[Proposal]:
    """`proposals` with two boxes of one column joined when together they are one character tall and
    `GAP` of a pitch or less stands between them."""
    from .column_proposals import ADJACENT, SHORTEST, columns, trimmed

    if not proposals:
        return []
    boxes = [Box(x=p.x, y=p.y, w=p.w, h=p.h) for p in proposals]
    position = {id(box): index for index, box in enumerate(boxes)}
    size = float(np.median([max(box.w, box.h) for box in boxes]))
    family = [chain for chain in (trimmed(column) for column in columns(boxes, size)) if len(chain) >= SHORTEST]
    alive = [True] * len(boxes)
    joined = 0
    for chain in family:
        rows = [position[id(box)] for box in chain]
        steps = [lower.y - upper.y for upper, lower in pairwise(chain)]
        short = [step for step in steps if step < ADJACENT * size]
        if not short:
            continue
        pitch = float(np.median(short))
        row = 0
        while row + 1 < len(rows):
            upper, lower = rows[row], rows[row + 1]
            if not (alive[upper] and alive[lower]):
                row += 1
                continue
            a, b = boxes[upper], boxes[lower]
            gap = b.y - (a.y + a.h)
            if -0.2 * pitch <= gap <= GAP * pitch and a.h + b.h <= MERGED * pitch:
                left, right = min(a.x, b.x), max(a.x + a.w, b.x + b.w)
                boxes[upper] = Box(x=left, y=a.y, w=right - left, h=b.y + b.h - a.y)
                alive[lower] = False
                rows.pop(row + 1)
                joined += 1
                row = max(0, row - 1)
            else:
                row += 1
    if not joined:
        return proposals
    return [Proposal(x=boxes[index].x, y=boxes[index].y, w=boxes[index].w, h=boxes[index].h,
                     score=proposal.score, kind=proposal.kind, features=dict(proposal.features))
            for index, proposal in enumerate(proposals) if alive[index]]
