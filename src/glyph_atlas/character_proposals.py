"""Character boxes the detector finds on a page photo, proposed for a reviewer to read.

A source with page photos and no transcription (EAP140's Tangut sutras) has nothing to align
detections with, so each kept detection is proposed as it stands: a unit with no character, which
a reviewer gives one or removes. `atlas review propose-characters` records them through
`Store.propose_units`, the path the interlinear marks take.

The detector was trained on Japanese pages, and on Tangut manuscripts its scores stay low (median
about 0.04 on four EAP140 pages) while its boxes still sit on single characters. The operating point
is therefore the collection run's own, score 0.02 and NMS 0.2, with the detector's ink gate dropping
boxes on blank paper and show-through.

The proposer's name carries the checksum of the detector and the operating point, so a new model or
threshold is a new version: `propose_units` records each version once per page and retires the
untouched proposals of an earlier one.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from PIL import Image

#: The family name of the proposer; a version is `characters-<digest>`.
FAMILY = "characters"
SCORE = 0.02
NMS = 0.2


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


def proposer(onnx: Path, *, score: float = SCORE, nms: float = NMS) -> str:
    """The proposer version: the detector's checksum and the operating point, hashed together."""
    model = hashlib.sha256(Path(onnx).read_bytes()).hexdigest()
    return f"{FAMILY}-{hashlib.sha256(f'{model}:{score}:{nms}'.encode()).hexdigest()[:12]}"


def propose(image: Image.Image, detector: Any) -> list[Proposal]:
    """Every box the detector keeps on `image`, highest score first."""
    return [Proposal(x=box.x, y=box.y, w=box.w, h=box.h, score=round(float(score), 4))
            for box, score in detector.boxes(image)]
