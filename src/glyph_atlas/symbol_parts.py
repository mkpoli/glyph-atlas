"""Read detached printed triangles separately from neighbouring characters.

Geometry only proposes regions. An OCR vote must identify each triangle before
it appears in a suggestion; automatic splitting requires reliable child readings.
"""

from __future__ import annotations

from itertools import pairwise, product

import numpy as np
from PIL import Image

from .split_proposals import graphemes

ENGINE = "Separated symbol OCR"


def triangle_regions(image: Image.Image) -> list[dict]:
    """Locate filled, upright, disconnected triangles without cutting other ink."""
    if image.width < 10 or image.height < 16:
        return []
    try:
        import cv2
    except ImportError:
        return []
    gray = np.asarray(image.convert("L"))
    if int(gray.max()) - int(gray.min()) < 30:
        return []
    _, mask = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    components = [(i, stats[i]) for i in range(1, count) if stats[i, 4] >= 4]
    regions = []
    for index, (x, y, w, h, mass) in components:
        if w < 8 or h < 6 or w < image.width * .4 or not .65 <= w / h <= 3:
            continue
        component = np.uint8(labels[y:y+h, x:x+w] == index) * 255
        contours, _ = cv2.findContours(component, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        contour = max(contours, key=cv2.contourArea)
        area = cv2.contourArea(contour)
        hull_area = cv2.contourArea(cv2.convexHull(contour))
        if not .38 <= area / (w * h) <= .65 or area / max(1, hull_area) < .87:
            continue
        vertices = cv2.approxPolyDP(contour, .07 * cv2.arcLength(contour, True), True)[:, 0]
        if len(vertices) != 3:
            continue
        apex, *base = sorted(vertices, key=lambda point: point[1])
        left, right = sorted(base, key=lambda point: point[0])
        if (apex[1] > h * .25 or min(left[1], right[1]) < h * .65
                or abs(int(left[1]) - int(right[1])) > h * .25
                or not left[0] + w * .2 <= apex[0] <= right[0] - w * .2):
            continue
        other = (labels != index) & (mask > 0)
        if int(other[y:y+h].sum()) > max(2, mass * .04):
            continue
        # Even when adjacent components have no empty row between them, their
        # non-overlapping vertical extents provide an ink-preserving boundary.
        rows = np.flatnonzero(other.sum(axis=1) >= max(2, image.width * .04))
        before, after = rows[rows < y], rows[rows >= y+h]
        top = (int(before[-1]) + 1 + int(y)) // 2 if len(before) else 0
        bottom = (int(y+h) + int(after[0])) // 2 if len(after) else image.height
        if top == 0 and bottom == image.height:
            continue  # Already one symbol; there is no joined region to separate.
        regions.append((top, bottom))
    regions = sorted(set(regions))
    if not regions or len(regions) > 2 or any(a[1] > b[0] for a, b in pairwise(regions)):
        return []
    edges = sorted({0, image.height, *(v for region in regions for v in region)})
    return [{"box": {"x": 0, "y": top, "w": image.width, "h": bottom-top},
             "symbol": (top, bottom) in regions} for top, bottom in pairwise(edges)]


def _votes(result):
    return result.get("votes", result.get("candidates", []))


def _triangle_confirmed(votes):
    supported = [v for v in votes if v.get("text") == "▲" and v.get("score", 0) >= .95
                 and v.get("identity_scope", "character") == "character"]
    contradicted = any(v.get("text") not in (None, "▲") and v.get("score", 0) >= .95 for v in votes)
    return bool(supported) and not contradicted


def _choices(result):
    choices = [c["text"] for c in result.get("candidates", [])
               if c.get("text") and c.get("score", 0) >= .5]
    for vote in _votes(result):
        if vote.get("identity_scope") == "family" and vote.get("score", 0) >= .9:
            # Offer both ordinary kana scripts when a normalized family cannot
            # distinguish them. These alternatives never become exact OCR votes.
            choices.extend(c for c in vote.get("members", [])
                           if all(ord(char) <= 0xffff for char in c))
    return list(dict.fromkeys(c for c in choices if 1 <= len(graphemes(c)) <= 3))[:3]


def _reliable_text(votes):
    ndl = next((v for v in votes if v.get("engine") == "NDLkotenOCR"), {})
    text = ndl.get("text")
    if not text or ndl.get("score", 0) < .98:
        return None
    for vote in votes:
        if vote.get("engine") != "Atlas classifier" or vote.get("score", 0) < .9:
            continue
        if vote.get("identity_scope") == "family":
            if len(graphemes(text)) == 1 and text not in vote.get("members", []):
                return None
        elif vote.get("text") and vote["text"] != text and len(graphemes(text)) == 1:
            return None
    return text


def recognize_parts(image: Image.Image, recognize) -> dict | None:
    """Use base OCR (without this pass) so work stays bounded and non-recursive."""
    regions = triangle_regions(image)
    if not regions:
        return None
    children, options, reliable = [], [], []
    for region in regions:
        box = region["box"]
        child = image.crop((0, box["y"], image.width, box["y"]+box["h"]))
        result = recognize(child)
        votes = _votes(result)
        if region["symbol"]:
            if not _triangle_confirmed(votes):
                return None
            choices = ["▲"]
            agreeing = {v.get("engine") for v in votes
                        if v.get("text") == "▲" and v.get("score", 0) >= .95}
            certain = "▲" if {"NDLkotenOCR", "Atlas classifier"} <= agreeing else None
        else:
            choices = _choices(result)
            certain = _reliable_text(votes)
        children.append({**region, "votes": votes, "candidates": result.get("candidates", [])})
        options.append(choices)
        reliable.append(certain)
    candidates = [{"text": "".join(parts), "engine": ENGINE, "basis": "symbol-parts", "verified": False}
                  for parts in product(*options)] if all(options) else []
    accepted = all(reliable)
    return {"accepted": accepted, "text": reliable if accepted else [],
            "boxes": [r["box"] for r in regions], "children": children,
            "partial": accepted and any(len(graphemes(t)) > 1 for t in reliable),
            "reason": "separate triangle confirmed by OCR; " + (
                "remaining regions independently recognized" if accepted else "remaining text needs review"),
            "boundary_model": "separate-triangle-v1", "candidates": candidates[:3]}
