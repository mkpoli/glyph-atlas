"""Trace a drawn form from a picture of it: each figure of the picture as one filled SVG path.

A drawn form (data/vocab/drawn-forms.yaml) is cut in two styles, serif and sans. Where its drawings
were made by hand, the picture is kept as `data/vocab/drawn-forms/<code point>.source.png`, dark figures
on a light ground, one figure for each style from left to right in the order given. The two cuts share
a scale and are centred in the em. The trace widens and thickens fine strokes for text sizes.

    uv run scripts/trace_form.py U+F0000 serif sans

Writes data/vocab/drawn-forms/F0000.serif.svg and F0000.sans.svg.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np
from scipy.ndimage import gaussian_filter1d

ROOT = Path(__file__).resolve().parents[1]
DRAWINGS = ROOT / "data" / "vocab" / "drawn-forms"
EM, INK_BOX = 1000, 880
WIDTH_FACTOR = 1.30  # widen the narrow handwritten figures at chip sizes
OUTSET = 5           # default edge expansion and shared fitting margin, in em units
# The looped 事 starts with heavier strokes. These signed outsets match the mean effective stroke
# width (2 × ink area / perimeter) of F0001 and F0002 in the finished font, separately for each cut.
EDGE_ADJUSTMENTS = {("F0000", "serif"): 1.16, ("F0000", "sans"): -2.32}
ENLARGED = 4   # the picture is enlarged before its edge is read, so the edge falls between its pixels
STEP = 6       # units between the outline's points
SMOOTH = 1.2   # units of smoothing along the outline, which rounds the pixel steps and keeps a corner


def figures(picture: np.ndarray) -> list[np.ndarray]:
    """The picture's figures as masks, left to right, each cut to its own box."""
    large = cv2.resize(picture, None, fx=ENLARGED, fy=ENLARGED, interpolation=cv2.INTER_CUBIC)
    _, ink = cv2.threshold(large, 127, 255, cv2.THRESH_BINARY_INV)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(ink)
    found = sorted((i for i in range(1, count) if stats[i, cv2.CC_STAT_AREA] > 0.001 * ink.size), key=lambda i: stats[i, cv2.CC_STAT_LEFT])
    cut = []
    for i in found:
        x, y, w, h = stats[i, :4]
        cut.append(((labels[y:y + h, x:x + w] == i) * 255).astype(np.uint8))
    return cut


def _closed(points: np.ndarray) -> str:
    n, d = len(points), [f"M {points[0][0]:.0f},{points[0][1]:.0f}"]
    for i in range(n):
        p0, p1, p2, p3 = points[i - 1], points[i], points[(i + 1) % n], points[(i + 2) % n]
        c1, c2 = p1 + (p2 - p0) / 6, p2 - (p3 - p1) / 6
        d.append(f"C {c1[0]:.0f},{c1[1]:.0f} {c2[0]:.0f},{c2[1]:.0f} {p2[0]:.0f},{p2[1]:.0f}")
    return " ".join(d) + " Z"


def outline(figure: np.ndarray, scale: float) -> str:
    """One figure's outline as path data in the em: outer contours one way and counters the other."""
    height, width = figure.shape
    x_scale = scale * WIDTH_FACTOR
    left, top = (EM - width * x_scale) / 2, (EM - height * scale) / 2
    contours, hierarchy = cv2.findContours(figure, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
    parts = []
    for contour, (_next, _previous, _child, parent) in zip(contours, hierarchy[0]):
        points = contour[:, 0, :].astype(float) * (x_scale, scale) + (left, top)
        if len(points) < 16:
            continue
        ring = np.vstack([points, points[:1]])
        spacing = float(np.mean(np.linalg.norm(np.diff(ring, axis=0), axis=1)))
        points = np.stack([gaussian_filter1d(points[:, axis], SMOOTH / spacing, mode="wrap") for axis in (0, 1)], 1)
        ring = np.vstack([points, points[:1]])
        along = np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(ring, axis=0), axis=1))])
        at = np.linspace(0, along[-1], max(8, round(along[-1] / STEP)), endpoint=False)
        even = np.stack([np.interp(at, along, ring[:, 0]), np.interp(at, along, ring[:, 1])], 1)
        area = 0.5 * np.sum(even[:, 0] * np.roll(even[:, 1], -1) - np.roll(even[:, 0], -1) * even[:, 1])
        if (area > 0) == (parent != -1):
            even = even[::-1]
        parts.append(_closed(even))
    return " ".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("code_point", help="the form's code point, as U+F0000")
    parser.add_argument("styles", nargs="+", help="the style of each figure of the picture, left to right")
    args = parser.parse_args()
    name = args.code_point.removeprefix("U+")
    cut = figures(cv2.imread(str(DRAWINGS / f"{name}.source.png"), cv2.IMREAD_GRAYSCALE))
    if len(cut) != len(args.styles):
        raise SystemExit(f"the picture has {len(cut)} figures, not {len(args.styles)}")
    # Fit the pair after widening, including the ink added on both sides of each figure.
    height_scale = (INK_BOX - 2 * OUTSET) / max(figure.shape[0] for figure in cut)
    width_scale = (INK_BOX / WIDTH_FACTOR - 2 * OUTSET) / max(figure.shape[1] for figure in cut)
    scale = min(height_scale, width_scale)
    for style, figure in zip(args.styles, cut):
        radius = round(EDGE_ADJUSTMENTS.get((name, style), OUTSET) / scale)
        diameter = 2 * abs(radius) + 1
        brush = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (diameter, diameter))
        adjust = cv2.erode if radius < 0 else cv2.dilate
        figure = adjust(np.pad(figure, abs(radius)), brush)
        path = outline(figure, scale)
        (DRAWINGS / f"{name}.{style}.svg").write_text(
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {EM} {EM}">\n  <path d="{path}"/>\n</svg>\n', encoding="utf-8")
        print(f"{args.code_point} {style}: {path.count('C')} curves")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
