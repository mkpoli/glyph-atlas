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
        settings += (("split", SPLIT_PITCHES, MIN_PART),)
    return f"{FAMILY}-{hashlib.sha256(repr(settings).encode()).hexdigest()[:12]}"


def propose(image: Image.Image, detector: Any, *, split: bool = False) -> list[Proposal]:
    """Every box the detector keeps on `image`, highest score first; with `split`, a box that holds two
    stacked characters of an orderly column becomes two boxes."""
    proposals = [Proposal(x=box.x, y=box.y, w=box.w, h=box.h, score=round(float(score), 4))
                 for box, score in detector.boxes(image)]
    return split_boxes(image, proposals) if split else proposals


def split_boxes(image: Image.Image, proposals: list[Proposal], *,
                pitches: float = SPLIT_PITCHES) -> list[Proposal]:
    """`proposals` with each box that is more than `pitches` of its column's pitch tall cut in two.

    The boxes a page's detector found give its columns and their pitch: the median step between
    neighbouring boxes of a column. A box of about two pitches holds two stacked characters, and is
    cut at the row of least ink within a quarter pitch of where the pitch puts the cut. Both halves
    keep the detection's score, order and features. A page whose boxes form no orderly column keeps
    them as they are, as does a box whose parts would be slivers.
    """
    from .column_proposals import ADJACENT, SHORTEST, SLACK, columns, ink, trimmed

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
    if not cut:
        return list(proposals)
    out: list[Proposal] = []
    for index, proposal in enumerate(proposals):
        for box in cut.get(index, [Box(x=proposal.x, y=proposal.y, w=proposal.w, h=proposal.h)]):
            out.append(Proposal(x=box.x, y=box.y, w=box.w, h=box.h, score=proposal.score,
                                kind=proposal.kind, features=dict(proposal.features)))
    return out
