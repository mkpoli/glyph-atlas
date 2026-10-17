"""Irregular crops: boxes far narrower or wider than the other crops of their character, for 要修正.

A half character, two or three characters cut as one, or a box that slipped onto a neighbour has a
width-to-height ratio unlike the character's. The `shape` score is that ratio (logarithm) as a robust
standard score among the character's crops: median and scaled MAD, the MAD floored at `MAD_FLOOR`,
for characters with `MIN_CROPS` crops or more. A hand that writes everything narrow or wide shifts a
whole document, so each document's median departure from its characters' ratios is taken out first.
A crop is irregular when its score is beyond `THRESHOLD`.

Measured on 2026-10-16 over 378,830 local crops of the publication catalogue of 2026-10-04, against
the hosted reviews exported on 2026-10-05 (108 crops reviewers disputed, 450 they confirmed): the
rule marks 3,713 crops (1.0%), 10 of the disputed and 2 of the confirmed. Reviewed crops mostly came
through the 要修正 view, so that ratio flatters the rule. In two random samples of 48 marked crops
nobody had reviewed, about a quarter were plain errors on inspection (は cut in half, a column of
characters in one box, a stray stroke); most of the rest were narrow cursive kana. Other
measures were tried and dropped: the box's height against the glyphs on its line marked some 3,500
more crops, mostly kanji standing beside smaller kana; its width against the line, and its size
against the same character in the same document, separated disputed from confirmed crops no better
than chance. Requiring a crop to stand out among its own book's crops of the character as well kept
a book's own narrow or wide forms out, but left 1 of the 10 disputed crops: a cutting error tends to
repeat through a book.

A mark is an aid for a person, never a verdict.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import numpy as np

METHOD = "box-shape-robust-z-v1"
#: A crop is irregular when its score lies beyond this many robust standard deviations.
THRESHOLD = 5.0
#: A character needs this many crops before its spread means anything.
MIN_CROPS = 20
#: The smallest spread a character is given, so a character written very evenly does not mark small wobbles.
MAD_FLOOR = 0.05


def scores(crops: list[dict]) -> np.ndarray:
    """The `shape` score of each crop (`label`, `document`, `box`), NaN where its character has too few crops."""
    n = len(crops)
    aspect = np.log(np.array([c["box"]["w"] / c["box"]["h"] for c in crops], dtype=float))
    by_label, by_document = _groups(c["label"] for c in crops), _groups(c["document"] for c in crops)
    typical = np.zeros(n)
    for members in by_label.values():
        typical[members] = np.median(aspect[members])
    for members in by_document.values():
        aspect[members] -= np.median(aspect[members] - typical[members])
    return _robust(aspect, by_label, MIN_CROPS)


def _robust(values: np.ndarray, groups: dict[Any, list[int]], least: int) -> np.ndarray:
    """Each value's robust standard score within its group, NaN in groups smaller than `least`."""
    z = np.full(len(values), np.nan)
    for members in groups.values():
        if len(members) < least:
            continue
        v = values[members]
        median = np.median(v)
        z[members] = (v - median) / max(float(np.median(np.abs(v - median))) * 1.4826, MAD_FLOOR)
    return z


def _groups(keys: Iterable) -> dict[Any, list[int]]:
    groups: dict[Any, list[int]] = defaultdict(list)
    for i, key in enumerate(keys):
        groups[key].append(i)
    return groups


def marks(crops: list[dict]) -> dict[str, dict]:
    """The irregular crops by id: the score, with the label and box measured."""
    if not crops:
        return {}
    shape = scores(crops)
    return {crop["id"]: {"shape": round(float(shape[i]), 2), "label": crop["label"], "box": crop["box"]}
            for i, crop in enumerate(crops) if abs(shape[i]) > THRESHOLD}


def catalogue_crops(catalogues: Iterable[Path]) -> list[dict]:
    """The local crops of publication catalogues (`catalogue.sqlite`), a crop in several as the last one gives it."""
    crops: dict[str, dict] = {}
    for catalogue in catalogues:
        with sqlite3.connect(f"file:{catalogue}?mode=ro", uri=True) as db:
            for identity, document, label, box in db.execute(
                    "SELECT id, document, json_extract(data,'$.label'), json_extract(data,'$.box') FROM units WHERE origin='local'"):
                box = json.loads(box) if box else None
                if not label or not box or not all(isinstance(box.get(k), (int, float)) for k in "xywh") or box["w"] <= 0 or box["h"] <= 0:
                    continue
                crops[identity] = {"id": identity, "label": label, "document": document, "box": box}
    return list(crops.values())


def compute(catalogues: list[Path], target: Path) -> dict[str, Any]:
    """Mark the irregular crops of `catalogues` into the file `target`."""
    crops = catalogue_crops(catalogues)
    if not crops:
        raise RuntimeError("the catalogues hold no local crop with a label and a box")
    found = marks(crops)
    inputs = {"crops": hashlib.sha256("\n".join(sorted(c["id"] for c in crops)).encode()).hexdigest()}
    revision = hashlib.sha256(json.dumps({"method": METHOD, "inputs": inputs, "marks": found}, sort_keys=True).encode()).hexdigest()[:16]
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_text(json.dumps({"revision": revision, "method": METHOD, "threshold": THRESHOLD, "min_crops": MIN_CROPS,
                                     "scored": len(crops), "inputs": inputs, "marks": found}, ensure_ascii=False, sort_keys=True))
    os.replace(temporary, target)
    return {"scored": len(crops), "irregular": len(found), "revision": revision}


def sql(marks_file: Path, out: Path) -> int:
    """D1 SQL that replaces `unit_irregular` with the marks of `marks_file` and starts a new catalogue version."""
    found = json.loads(Path(marks_file).read_text())["marks"]
    quoted = lambda value: "'{}'".format(value.replace("'", "''"))  # noqa: E731
    lines = ["DELETE FROM unit_irregular;"]
    for identity, mark in sorted(found.items()):
        values = [mark["box"].get(k) for k in "xywh"] + [mark["shape"]]
        if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in values):
            raise ValueError(f"{identity}: a mark needs a box of x, y, w and h, and a shape score")
        lines.append(f"INSERT INTO unit_irregular VALUES({quoted(identity)},{quoted(mark['label'])},"
                     f"{','.join(repr(float(v)) for v in values)});")
    # Listings and counts the edge keeps per catalogue version are read again.
    lines.append("INSERT OR REPLACE INTO metadata(key,value) VALUES('units_refreshed_at',"
                 "json_quote(strftime('%Y-%m-%dT%H:%M:%fZ','now')));")
    out.write_text("\n".join(lines) + "\n")
    return len(found)
