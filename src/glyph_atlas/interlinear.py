"""Propose the interlinear marks of a page photo: small glosses beside printed text, and break circles.

Korean books read in 구결 carry small glosses written beside the printed Chinese text, and small
circles where a phrase ends. `propose` finds candidates for both on one page photo from the ink alone,
for a reviewer to confirm or remove; nothing here reads a character.

Every measure is a multiple of `unit`, the size of a printed character on the page, taken as the 97th
percentile of the sizes of the ink components. The steps:

1. Binarise with an adaptive threshold, and find the ruled lines of the frame (straight runs of ink
   two units long).
2. The printed characters are the blobs of a coarse closing (0.12 unit), leaving the rules out, that
   are 0.5 to 2.2 unit long, or runs of characters the closing joined, 0.5 to 1.6 unit across. Their
   larger pieces give the width of a printed stroke.
3. The text block is the extent of the character-sized blobs of a fine closing (0.05 unit) that have
   neighbours, with half a unit of margin across the lines and a quarter along them.
4. Candidates are the blobs of the fine closing from 0.09 to 0.5 unit, no longer than 4.5 times their
   width. A candidate is dropped when it lies on a rule; when its strokes are as wide as printed ones
   (0.85 of them), or nearly so while ink encloses it on three sides or two opposite ones, which is a
   piece of a printed character; when it lies outside the text block or on a red seal; when its ink
   is faint beside the printed text (0.35 of its contrast) or stands less than eight deviations out
   of the paper's own mottling, which is a stain, a fold or a shadow; and when it is one of a vertical
   run of thin candidates, which is a crease.
5. A small round candidate with a hole is a circle; the rest are marks.

The result is deterministic for a given image. A page whose characters are too small to show marks
(`unit` under 24 pixels), or with too few characters to form a text block, yields nothing. Glosses that
touch a printed character, or are written as heavily as the print, are missed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np
from PIL import Image

#: The version of the proposer; a unit records it, and a change to what it proposes changes it.
PROPOSER = "interlinear-1"

#: Pages whose printed characters are smaller than this many pixels are not searched.
MIN_UNIT = 24
#: A text block needs at least this many printed characters.
MIN_CHARACTERS = 12
#: A candidate whose ink is lighter than this share of the printed text's contrast is a stain or a fold.
FAINT = 0.35
#: Nor is one that stands out from the paper's own mottling by less than this many deviations.
CLEAR = 8.0
#: A candidate whose strokes are this share of the printed strokes' width or wider is a piece of print;
#: the brush of a gloss is finer.
THICK = 0.85


@dataclass(frozen=True)
class Proposal:
    """One proposed mark: its box in image pixels, what it looks like, and how sure the proposer is."""

    x: int
    y: int
    w: int
    h: int
    kind: Literal["mark", "circle"]
    score: float
    features: dict[str, Any] = field(default_factory=dict)

    @property
    def box(self) -> tuple[int, int, int, int]:
        return self.x, self.y, self.w, self.h


def _cv2():
    try:
        import cv2
    except ImportError as exc:  # pragma: no cover - the extra is part of the development environment
        raise RuntimeError("proposing marks needs OpenCV: uv sync --extra marks") from exc
    return cv2


def _odd(value: float, least: int = 3) -> int:
    return max(least, round(value) | 1)


def survey(image: Image.Image | np.ndarray) -> tuple[float, tuple[float, ...], list[dict[str, Any]]]:
    """Every candidate of a page with its measures, and `dropped` naming the filter that removed it.

    Returns the unit, the text block (x0, y0, x1, y1) and the candidates; `propose` keeps the ones
    no filter dropped. A page too small or without a text block has no candidates.
    """
    cv2 = _cv2()
    rgb = np.asarray(image.convert("RGB")) if isinstance(image, Image.Image) else np.asarray(image)
    if rgb.ndim == 2:
        rgb = np.stack([rgb] * 3, axis=-1)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    height, width = gray.shape
    if min(height, width) < 4 * MIN_UNIT:
        return 0.0, (), []

    first = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV,
                                  _odd(min(height, width) / 60, 15), 15)
    unit = _unit(cv2, first)
    if unit < MIN_UNIT:
        return unit, (), []
    ink = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV,
                                _odd(0.35 * unit, 15), 15)
    speck = max(1, round(0.014 * unit))
    ink = cv2.morphologyEx(ink, cv2.MORPH_OPEN, np.ones((speck, speck), np.uint8))

    rules = _rules(cv2, ink, unit)
    fine = _blobs(cv2, ink, 0.05 * unit)
    printed, stroke = _characters(cv2, np.where(rules, 0, ink).astype(np.uint8), unit, fine)
    characters = _glyphs(fine, unit)
    if len(characters) < MIN_CHARACTERS:
        return unit, (), []
    block = _block(characters, unit, width, height)
    seals = _seals(cv2, rgb, unit)
    background = float(np.median(gray[(ink == 0)]))
    reference = background - float(np.median(gray[(ink > 0) & (printed > 0)]))

    count, labels, stats, centres = fine
    found: list[dict[str, Any]] = []
    for index in range(1, count):
        x, y, w, h, _ = (int(value) for value in stats[index])
        size = max(w, h)
        if not 0.09 * unit <= size <= 0.5 * unit or size / max(1, min(w, h)) > 4.5:
            continue
        region = labels[y:y + h, x:x + w] == index
        mine = region & (ink[y:y + h, x:x + w] > 0)
        if not mine.any():
            continue
        cx, cy = float(centres[index][0]), float(centres[index][1])
        contrast, clarity = _contrast(gray, ink, mine, (x, y, w, h), unit)
        candidate = {"box": (x, y, w, h), "mine": mine, "size": size / unit,
                     "contrast": contrast / max(1.0, reference), "clarity": clarity,
                     "stroke": _stroke(cv2, mine) / stroke,
                     "sides": _sides(ink, labels, index, (x, y, w, h), unit)}
        if rules[y:y + h, x:x + w][mine].mean() > 0.3:
            candidate["dropped"] = "rule"
        elif candidate["stroke"] >= THICK or (_embedded(candidate["sides"]) and candidate["stroke"] >= 0.8 * THICK):
            candidate["dropped"] = "printed"
        elif not (block[0] <= cx <= block[2] and block[1] <= cy <= block[3]):
            candidate["dropped"] = "outside"
        elif seals[int(cy), int(cx)]:
            candidate["dropped"] = "seal"
        elif candidate["contrast"] < FAINT or candidate["clarity"] < CLEAR:
            candidate["dropped"] = "faint"
        found.append(candidate)
    kept = [candidate for candidate in found if "dropped" not in candidate]
    for number in _creases(kept, unit):
        kept[number]["dropped"] = "crease"
    for candidate in found:
        x, y, w, h = candidate["box"]
        holes = _holes(cv2, candidate["mine"])
        circle = holes > 0 and 0.7 < w / h < 1.4 and candidate["size"] < 0.28 and holes <= 2
        candidate["kind"] = "circle" if circle else "mark"
        candidate["holes"] = holes
    return unit, block, found


def propose(image: Image.Image | np.ndarray) -> list[Proposal]:
    """The interlinear marks and circles of one page photo, top to bottom, then left to right.

    The score is the candidate's contrast against the printed text, capped at one, weighted 0.9 for a
    circle and 0.7 for a mark; `features` keeps the measures behind it.

    `image` is the photo as stored, in the pixel orientation the page's boxes are measured in: a PIL
    image is read without applying its EXIF orientation.
    """
    unit, _, found = survey(image)
    proposals = []
    for candidate in found:
        if "dropped" in candidate:
            continue
        x, y, w, h = candidate["box"]
        circle = candidate["kind"] == "circle"
        score = min(1.0, candidate["contrast"]) * (0.9 if circle else 0.7)
        proposals.append(Proposal(
            x=x, y=y, w=w, h=h, kind=candidate["kind"], score=round(score, 3),
            features={"unit": round(unit, 1), "size": round(candidate["size"], 3),
                      "contrast": round(candidate["contrast"], 3), "clarity": round(candidate["clarity"], 1),
                      "stroke": round(candidate["stroke"], 3), "holes": candidate["holes"]},
        ))
    proposals.sort(key=lambda proposal: (proposal.y, proposal.x))
    return proposals


def _unit(cv2, ink: np.ndarray) -> float:
    count, _, stats, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    sizes = [max(stats[i, 2], stats[i, 3]) for i in range(1, count) if stats[i, 4] >= 20]
    return float(np.percentile(sizes, 97)) if len(sizes) >= 20 else 0.0


def _rules(cv2, ink: np.ndarray, unit: float) -> np.ndarray:
    """A mask of the ruled lines of the frame: straight runs of ink two units long, widened a little.

    Taken out before the characters are found, so that a column touching the frame is not one blob
    with it; a candidate lying on one is a fragment of the rule.
    """
    length = max(3, round(2 * unit))
    lines = cv2.morphologyEx(ink, cv2.MORPH_OPEN, np.ones((1, length), np.uint8))
    lines |= cv2.morphologyEx(ink, cv2.MORPH_OPEN, np.ones((length, 1), np.uint8))
    reach = _odd(0.05 * unit)
    return cv2.dilate(lines, np.ones((reach, reach), np.uint8)) > 0


def _blobs(cv2, ink: np.ndarray, reach: float):
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (_odd(reach),) * 2)
    return cv2.connectedComponentsWithStats(cv2.morphologyEx(ink, cv2.MORPH_CLOSE, kernel), connectivity=8)


def _glyphs(fine: tuple, unit: float) -> list[tuple[int, int, int, int]]:
    """The boxes of the fine blobs that are about a character large, the evidence for the text block.

    The fine closing keeps neighbouring columns apart where the coarse one may join a whole page."""
    count, _, stats, _ = fine
    boxes = []
    for index in range(1, count):
        x, y, w, h, _ = (int(value) for value in stats[index])
        if 0.4 * unit <= max(w, h) <= 1.5 * unit and max(w, h) <= 3 * min(w, h):
            boxes.append((x, y, w, h))
    return boxes


def _characters(cv2, ink: np.ndarray, unit: float, fine: tuple) -> tuple[np.ndarray, float]:
    """A mask of the ink of the printed characters, and the median width of their strokes in pixels.

    The width is measured on the fine blobs from 0.5 to 1.3 unit long that lie in a character: whole
    characters, or their larger parts.
    """
    count, labels, stats, _ = _blobs(cv2, ink, 0.12 * unit)
    keep = np.zeros(count, bool)
    for index in range(1, count):
        x, y, w, h, _ = (int(value) for value in stats[index])
        long, short = max(w, h), min(w, h)
        single = 0.5 * unit <= long <= 2.2 * unit and long <= 2.5 * short
        run = 0.5 * unit <= short <= 1.6 * unit and long <= 12 * unit
        keep[index] = single or run
    printed = (keep[labels] & (ink > 0)).astype(np.uint8)
    fine_count, fine_labels, fine_stats, _ = fine
    strokes: list[float] = []
    for index in range(1, fine_count):
        x, y, w, h, _ = (int(value) for value in fine_stats[index])
        if not 0.5 * unit < max(w, h) <= 1.3 * unit:
            continue
        piece = (fine_labels[y:y + h, x:x + w] == index) & (ink[y:y + h, x:x + w] > 0)
        owners = labels[y:y + h, x:x + w][piece]
        if owners.size and keep[int(np.bincount(owners).argmax())]:
            strokes.append(_stroke(cv2, piece))
    return printed, float(np.median(strokes)) if strokes else 0.03 * unit


def _block(boxes: list[tuple[int, int, int, int]], unit: float, width: int, height: int) -> tuple[float, ...]:
    """The text block: the extent of the characters that have neighbours, with a margin.

    A character of a text has at least two others within one unit of its edges; a stain or a tear the
    closing took for a character stands alone. The block spans the 1st to 99th percentile of the
    edges of the neighbourly characters, widened by half a unit across the lines, where the glosses
    of the outermost line lie, and by a quarter unit along them.
    """
    edges = np.array([(x, y, x + w, y + h) for x, y, w, h in boxes], dtype=float)
    gap_x = np.maximum(edges[:, None, 0] - edges[None, :, 2], edges[None, :, 0] - edges[:, None, 2])
    gap_y = np.maximum(edges[:, None, 1] - edges[None, :, 3], edges[None, :, 1] - edges[:, None, 3])
    near = np.maximum(gap_x, gap_y) <= unit
    kept = edges[near.sum(axis=1) >= 3]
    if len(kept) < MIN_CHARACTERS:
        kept = edges
    return (max(0.0, float(np.percentile(kept[:, 0], 1)) - 0.5 * unit),
            max(0.0, float(np.percentile(kept[:, 1], 1)) - 0.25 * unit),
            min(float(width), float(np.percentile(kept[:, 2], 99)) + 0.5 * unit),
            min(float(height), float(np.percentile(kept[:, 3], 99)) + 0.25 * unit))


def _seals(cv2, rgb: np.ndarray, unit: float) -> np.ndarray:
    """A mask of the red seals: the boxes of red areas at least a character across, widened by 0.2
    unit, so that the whole face of a seal is covered and a small red gloss stays."""
    hsv = cv2.cvtColor(np.ascontiguousarray(rgb), cv2.COLOR_RGB2HSV)
    hue, saturation, value = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    # Red is hue 0 on OpenCV's half-degree scale; tanned paper and brown stains sit near 15 to 20.
    red = (((hue <= 8) | (hue >= 170)) & (saturation >= 90) & (value >= 60)).astype(np.float32)
    # A seal is densely red; stains hold scattered red pixels, which the density leaves out.
    window = _odd(0.3 * unit)
    dense = (cv2.blur(red, (window, window)) >= 0.15).astype(np.uint8) * 255
    count, _, stats, _ = cv2.connectedComponentsWithStats(dense, connectivity=8)
    mask = np.zeros(red.shape, bool)
    pad = round(0.2 * unit)
    for index in range(1, count):
        x, y, w, h, _ = (int(value) for value in stats[index])
        if max(w, h) >= unit:
            mask[max(0, y - pad):y + h + pad, max(0, x - pad):x + w + pad] = True
    return mask


def _contrast(gray: np.ndarray, ink: np.ndarray, mine: np.ndarray, box: tuple[int, int, int, int],
              unit: float) -> tuple[float, float]:
    """How much darker the candidate's ink is than the paper around it, in grey levels and in units of
    the paper's own mottling (its median absolute deviation, plus one)."""
    x, y, w, h = box
    pad = max(2, round(0.15 * unit))
    top, left = max(0, y - pad), max(0, x - pad)
    around = gray[top:y + h + pad, left:x + w + pad]
    paper = around[ink[top:y + h + pad, left:x + w + pad] == 0]
    if paper.size == 0:
        return 0.0, 0.0
    level = float(np.median(paper))
    contrast = level - float(np.median(gray[y:y + h, x:x + w][mine]))
    return contrast, contrast / (float(np.median(np.abs(paper.astype(np.float32) - level))) + 1.0)


def _sides(ink: np.ndarray, labels: np.ndarray, index: int, box: tuple[int, int, int, int],
           unit: float) -> str:
    """The sides of the candidate, of `lrtb`, where other ink lies within 0.2 unit.

    A stroke inside a printed character has ink around it; a gloss in the gap between two columns has
    ink on one side at most, the character it glosses.
    """
    x, y, w, h = box
    reach = max(2, round(0.2 * unit))
    top, left = max(0, y - reach), max(0, x - reach)
    window = (ink[top:y + h + reach, left:x + w + reach] > 0) & (labels[top:y + h + reach, left:x + w + reach] != index)
    x, y = x - left, y - top
    strips = (window[y:y + h, :x], window[y:y + h, x + w:], window[:y, x:x + w], window[y + h:, x:x + w])
    return "".join("lrtb"[i] for i, strip in enumerate(strips) if strip.size and strip.mean() > 0.02)


def _embedded(sides: str) -> bool:
    """Whether ink on these sides encloses a candidate: three sides, or two opposite ones."""
    return len(sides) >= 3 or sides in ("lr", "tb")


def _stroke(cv2, mine: np.ndarray) -> float:
    """The mean stroke width of the candidate's ink: twice its area over its perimeter."""
    contours, _ = cv2.findContours(mine.astype(np.uint8), cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
    perimeter = sum(cv2.arcLength(contour, True) for contour in contours)
    return 2.0 * float(mine.sum()) / max(1.0, perimeter)


def _holes(cv2, mine: np.ndarray) -> int:
    _, hierarchy = cv2.findContours(mine.astype(np.uint8), cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    if hierarchy is None:
        return 0
    return sum(1 for entry in hierarchy[0] if entry[3] >= 0)


def _creases(found: list[dict[str, Any]], unit: float) -> set[int]:
    """Thin candidates that line up vertically with two or more others: the pieces of a fold."""
    thin = [i for i, candidate in enumerate(found)
            if candidate["box"][2] <= 0.15 * unit and candidate["box"][3] >= 1.5 * candidate["box"][2]]
    dropped: set[int] = set()
    for i in thin:
        x, y, w, _ = found[i]["box"]
        centre = x + w / 2
        run = [j for j in thin
               if abs(found[j]["box"][0] + found[j]["box"][2] / 2 - centre) <= 0.06 * unit
               and abs(found[j]["box"][1] - y) <= 4 * unit]
        if len(run) >= 3:
            dropped.update(run)
    return dropped
