"""Separate disconnected character groups while keeping connected ink together."""

from __future__ import annotations

import unicodedata
from collections.abc import Callable
from itertools import combinations, pairwise, product

from PIL import Image

from .split_proposals import graphemes, ink_profile


def propose_partial(crop: Image.Image, expected: str, recognize: Callable[[Image.Image], dict]) -> dict:
    """Cut only through blank runs, requiring independently read children to reproduce the text.

    A three-character block can become a two-character group plus one character. A
    child with several characters stays a sequence; no equally sized slices are used.
    The caller protects known encoded ligatures before invoking this routine.
    """
    count = len(graphemes(expected))
    refused = {"accepted": False, "reason": "no independently recognized partition at a blank gap"}
    if not 2 <= count <= 4:
        return refused
    profile = ink_profile(crop, smooth=1)
    width, height = crop.size
    minimum = max(2, round(height * .015))
    margin = max(4, round(height * .12))
    gaps = []
    start = None
    for y in range(height + 1):
        if y < height and profile[y] == 0:
            if start is None:
                start = y
        elif start is not None:
            if y - start >= minimum and start >= margin and y <= height - margin:
                gaps.append(((start + y) // 2, y - start))
            start = None
    gaps = sorted(sorted(gaps, key=lambda pair: pair[1], reverse=True)[:6])
    if not gaps:
        return refused
    memo = {}
    proposals = []
    for cuts_count in range(1, min(count - 1, len(gaps)) + 1):
        for selected in combinations(gaps, cuts_count):
            edges = [0, *(pair[0] for pair in selected), height]
            options = []
            for top, bottom in pairwise(edges):
                if bottom - top < 4:
                    break
                key = (top, bottom)
                if key not in memo:
                    memo[key] = recognize(crop.crop((0, top, width, bottom)))
                result = memo[key]
                votes = result.get("votes", result.get("candidates", []))
                ndl = next((v for v in votes if v.get("engine") == "NDLkotenOCR"), None)
                classifier = next((v for v in votes if v.get("engine") == "Atlas classifier"), None)
                # Sequence OCR sometimes answers ASCII brackets for an isolated kana.
                # That is an abstention here, not a competing calibrated probability.
                japanese = ndl and ndl.get("text") and all(
                    unicodedata.category(c).startswith("L") for c in ndl["text"])
                choices = []
                if ndl and ndl.get("text") and ndl.get("score", 0) >= .97:
                    choices.append(ndl)
                if (classifier and classifier.get("identity_scope", "character") == "character"
                        and classifier.get("text") and classifier.get("score", 0) >= .97
                        and (not japanese or classifier["text"] == ndl["text"])):
                    choices.append(classifier)
                if not choices:
                    break
                options.append([{**choice, "box": {"x": 0, "y": top, "w": width, "h": bottom - top},
                                 "votes": votes} for choice in choices])
            if len(options) != len(edges) - 1:
                continue
            for children in product(*options):
                if ("".join(c["text"] for c in children) != expected
                        or not any(len(graphemes(c["text"])) == 1 for c in children)):
                    continue
                proposals.append({"accepted": True, "text": [c["text"] for c in children],
                                  "boxes": [c["box"] for c in children], "children": list(children),
                                  "cuts": edges[1:-1], "gap_widths": [p[1] for p in selected],
                                  "reason": "blank gaps and independently recognized child crops",
                                  "partial": any(len(graphemes(c["text"])) > 1 for c in children)})
    if not proposals:
        return refused
    # Prefer more individually usable characters, then a wide measured gap. Different
    # padding within the same blank gap is not a different segmentation of the ink.
    proposals.sort(key=lambda p: (sum(len(graphemes(t)) == 1 for t in p["text"]),
                                  sum(p["gap_widths"])), reverse=True)
    return proposals[0]


def child_units(parent, proposal: dict, crop_sha256: str):
    """Derived units for a checked partition. The unchanged parent is retained as retired."""
    import hashlib
    import json

    from . import refs
    from .schema import Box, ReviewState, Unit, UnitKind
    from .split_proposals import to_page

    if not proposal.get("accepted") or not parent.box:
        raise ValueError("an accepted partition and source geometry are required")
    if parent.unicode and refs.ligature(parent.unicode):
        raise ValueError("an encoded ligature is one character")
    texts = proposal["text"]
    boxes = [Box(**box) for box in proposal["boxes"]]
    if len(texts) != len(boxes) or len(texts) < 2:
        raise ValueError("a partition must describe every child")
    for i, box in enumerate(boxes):
        if (box.w <= 0 or box.h <= 0 or box.x < 0 or box.y < 0
                or box.x + box.w > parent.box.w or box.y + box.h > parent.box.h):
            raise ValueError("child outside source crop")
        for other in boxes[i + 1:]:
            if (box.x < other.x + other.w and other.x < box.x + box.w
                    and box.y < other.y + other.h and other.y < box.y + box.h):
                raise ValueError("children overlap")
    fingerprint = hashlib.sha256(json.dumps({"parent": parent.id, "crop": crop_sha256,
                                            "proposal": proposal}, sort_keys=True).encode()).hexdigest()[:12]
    children = []
    for i, (text, box) in enumerate(zip(texts, to_page(boxes, parent.box), strict=True)):
        single = len(graphemes(text)) == 1
        children.append(Unit(id=f"{parent.id}:s{fingerprint}:{i}", document_id=parent.document_id,
            page_id=parent.page_id, line_id=parent.line_id, seq=parent.seq,
            box=box, kind=UnitKind.CHAR if single else UnitKind.SEQUENCE,
            granularity="char" if single else "sequence", text_source=text, reading=text,
            unicode=" ".join(refs.to_code_points(text)), method="detect-align", review=ReviewState.MACHINE,
            script=refs.script_of(text) if single else "unknown",
            antecedent_ids=[parent.id], upstream={**parent.upstream, "segmentation_parent": parent.id},
            meta={"segmentation": {"policy": "blank-gap-v1", "parent_id": parent.id,
                  "crop_sha256": crop_sha256, "automated": True,
                  "evidence": proposal["children"][i]}}))
    retired = parent.model_copy(update={"active": False, "split_into": [c.id for c in children]})
    return retired, children
