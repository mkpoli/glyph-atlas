"""Move a character box onto its character, and say how well it frames it.

A detector box is often a little off: it reaches into the next character, stops halfway down a
long stroke, or leaves a radical outside. The refiner looks at the box with its surroundings and
answers how far each edge should move and the IoU it expects the box to have with the character.
It is trained on boxes moved off CODH's ground truth the ways a detector moves them
(`models/refiner/train.py`) and served from ONNX.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

from .schema import Box

ROOT = Path(__file__).resolve().parents[2]
MODEL = ROOT / "models/refiner/artifacts/refiner.onnx"
#: The side of the square the window is resized to.
SIZE = 112
#: How much of the box's own extent the window shows beyond each edge.
MARGIN = 0.75
#: An edge moves by at most this share of the box's extent in one round.
REACH = 0.75
BATCH = 256


def window(grey: np.ndarray, box: Sequence[float], paper: int) -> np.ndarray:
    """The box `(x, y, w, h)` with `MARGIN` of surroundings, as a `SIZE` square of grey levels.

    The box always lands on the same central rectangle of the square, whatever its shape, so the
    network needs no second channel to know which box it is asked about. Outside the page is paper.
    """
    import cv2

    x, y, w, h = box
    w, h = max(w, 2.0), max(h, 2.0)
    left, top = x - MARGIN * w, y - MARGIN * h
    scale_x, scale_y = SIZE / ((1 + 2 * MARGIN) * w), SIZE / ((1 + 2 * MARGIN) * h)
    matrix = np.array([[scale_x, 0, -left * scale_x], [0, scale_y, -top * scale_y]], dtype=np.float64)
    interpolation = cv2.INTER_AREA if min(scale_x, scale_y) < 1 else cv2.INTER_LINEAR
    return cv2.warpAffine(grey, matrix, (SIZE, SIZE), flags=interpolation, borderMode=cv2.BORDER_CONSTANT,
                          borderValue=int(paper))


def moved(box: Sequence[float], deltas: Sequence[float]) -> tuple[float, float, float, float]:
    """The box after its edges move by `deltas` (left, top, right, bottom, in shares of its extent)."""
    x, y, w, h = box
    d = np.clip(np.asarray(deltas, dtype=np.float64), -REACH, REACH)
    left, top = x + d[0] * w, y + d[1] * h
    right, bottom = x + w + d[2] * w, y + h + d[3] * h
    if right - left < 2 or bottom - top < 2:
        return (x, y, w, h)
    return (left, top, right - left, bottom - top)


def pixels(windows: np.ndarray) -> np.ndarray:
    """Windows as the network's input: one channel, paper near +1 and ink near -1."""
    return (windows.astype(np.float32) / 127.5 - 1.0)[:, None, :, :]


class Refiner:
    """The exported refiner. `refine` moves boxes and returns each with its expected IoU."""

    def __init__(self, path: Path | str = MODEL, *, session: Any = None, rounds: int = 2):
        self.path = Path(path)
        self.rounds = rounds
        if session is None:
            import onnxruntime as ort

            options = ort.SessionOptions()
            options.log_severity_level = 3
            providers: list[Any] = ["CPUExecutionProvider"]
            if "CUDAExecutionProvider" in ort.get_available_providers():
                ort.preload_dlls()
                providers.insert(0, ("CUDAExecutionProvider", {"arena_extend_strategy": "kSameAsRequested"}))
            session = ort.InferenceSession(str(self.path), sess_options=options, providers=providers)
        self.session = session

    def _run(self, grey: np.ndarray, boxes: Sequence[Sequence[float]], paper: int) -> tuple[np.ndarray, np.ndarray]:
        deltas, quality = [], []
        for start in range(0, len(boxes), BATCH):
            batch = np.stack([window(grey, box, paper) for box in boxes[start:start + BATCH]])
            found = self.session.run(None, {"pixels": pixels(batch)})
            deltas.append(found[0])
            quality.append(found[1])
        return np.concatenate(deltas), np.concatenate(quality)

    def refine(self, grey: np.ndarray, boxes: Sequence[Box]) -> list[tuple[Box, float, float]]:
        """Each box moved onto its character, with the IoU expected of the moved box and of the box as given."""
        if not boxes:
            return []
        paper = int(np.median(grey[::16, ::16]))
        current = [(float(b.x), float(b.y), float(b.w), float(b.h)) for b in boxes]
        given = None
        for _ in range(self.rounds):
            deltas, quality = self._run(grey, current, paper)
            if given is None:
                given = quality
            current = [moved(box, delta) for box, delta in zip(current, deltas, strict=True)]
        _, final = self._run(grey, current, paper)
        height, width = grey.shape[:2]
        out = []
        for (x, y, w, h), after, before in zip(current, final, given, strict=True):
            left, top = min(width - 1, max(0, round(x))), min(height - 1, max(0, round(y)))
            right, bottom = min(width, round(x + w)), min(height, round(y + h))
            out.append((Box(x=left, y=top, w=max(1, right - left), h=max(1, bottom - top)), float(after), float(before)))
        return out
