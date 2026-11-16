"""Draw a form from brush strokes: one filled SVG path in a 1000-unit em.

A drawn form (data/vocab/drawn-forms.yaml) is kept as strokes, each a list of knots `[x, y, width]`
with y running down, in `data/vocab/drawn-forms/<code point>.strokes.json`. A stroke is a centripetal
Catmull-Rom curve through its knots with the width changing along it, as a brush would leave it. The
strokes are painted onto one mask, so where two cross they join, and the mask's outline is written as
closed cubic curves: outer contours one way and counters the other, so the path fills alike under
either fill rule and in a font.

    uv run scripts/draw_form.py U+F0000

Writes data/vocab/drawn-forms/F0000.svg. `--sheet out.png` also writes the drawing as an image, to
look at beside the crops it was made from.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np
from scipy.ndimage import gaussian_filter1d

ROOT = Path(__file__).resolve().parents[1]
DRAWINGS = ROOT / "data" / "vocab" / "drawn-forms"
EM = 1000
SCALE = 4   # mask pixels to a unit
STEP = 22   # units between the outline's points
SMOOTH = 3  # units of smoothing along the outline, which rounds the mask's pixel steps


def curve(knots: list[list[float]], per: int = 60) -> np.ndarray:
    """The stroke's centre line and width, `per` points to each span between knots."""
    p = np.array(knots, float)
    p = np.vstack([2 * p[0] - p[1], p, 2 * p[-1] - p[-2]])
    points = []
    for i in range(1, len(p) - 2):
        p0, p1, p2, p3 = p[i - 1], p[i], p[i + 1], p[i + 2]
        t1 = max(np.linalg.norm(p1[:2] - p0[:2]) ** 0.5, 1e-6)
        t2 = t1 + max(np.linalg.norm(p2[:2] - p1[:2]) ** 0.5, 1e-6)
        t3 = t2 + max(np.linalg.norm(p3[:2] - p2[:2]) ** 0.5, 1e-6)
        for t in np.linspace(t1, t2, per, endpoint=False):
            a1 = (t1 - t) / t1 * p0 + t / t1 * p1
            a2 = (t2 - t) / (t2 - t1) * p1 + (t - t1) / (t2 - t1) * p2
            a3 = (t3 - t) / (t3 - t2) * p2 + (t - t2) / (t3 - t2) * p3
            b1 = (t2 - t) / t2 * a1 + t / t2 * a2
            b2 = (t3 - t) / (t3 - t1) * a2 + (t - t1) / (t3 - t1) * a3
            points.append((t2 - t) / (t2 - t1) * b1 + (t - t1) / (t2 - t1) * b2)
    points.append(p[-2])
    return np.array(points)


def mask(strokes: list[list[list[float]]]) -> np.ndarray:
    """The strokes painted onto one em, `SCALE` pixels to a unit."""
    painted = np.zeros((EM * SCALE, EM * SCALE), np.uint8)
    for knots in strokes:
        for x, y, width in curve(knots):
            cv2.circle(painted, (round(x * SCALE), round(y * SCALE)), max(1, round(width * SCALE / 2)), 255, -1, cv2.LINE_AA)
    return painted


def _closed(points: np.ndarray) -> str:
    """A closed curve through the points, as cubic segments."""
    n, d = len(points), [f"M {points[0][0]:.0f},{points[0][1]:.0f}"]
    for i in range(n):
        p0, p1, p2, p3 = points[i - 1], points[i], points[(i + 1) % n], points[(i + 2) % n]
        c1, c2 = p1 + (p2 - p0) / 6, p2 - (p3 - p1) / 6
        d.append(f"C {c1[0]:.0f},{c1[1]:.0f} {c2[0]:.0f},{c2[1]:.0f} {p2[0]:.0f},{p2[1]:.0f}")
    return " ".join(d) + " Z"


def outline(painted: np.ndarray) -> str:
    """The mask's outline as path data."""
    contours, hierarchy = cv2.findContours((painted > 127).astype(np.uint8), cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
    parts = []
    for contour, (_next, _previous, _child, parent) in zip(contours, hierarchy[0]):
        if cv2.contourArea(contour) / SCALE**2 < 150:
            continue
        points = contour[:, 0, :].astype(float) / SCALE
        points = np.stack([gaussian_filter1d(points[:, axis], SMOOTH * SCALE, mode="wrap") for axis in (0, 1)], 1)
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
    parser.add_argument("--sheet", type=Path, help="also write the drawing as a 400-pixel image here")
    args = parser.parse_args()
    name = args.code_point.removeprefix("U+")
    painted = mask(json.loads((DRAWINGS / f"{name}.strokes.json").read_text())["strokes"])
    path = outline(painted)
    (DRAWINGS / f"{name}.svg").write_text(
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {EM} {EM}">\n  <path d="{path}"/>\n</svg>\n', encoding="utf-8")
    if args.sheet:
        cv2.imwrite(str(args.sheet), 255 - cv2.resize(painted, (400, 400), interpolation=cv2.INTER_AREA))
    print(f"{args.code_point}: {path.count('C')} curves")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
