"""How well a scan shows a crop's letterform, measured from its pixels.

The measurements of every crop are kept in an index (`INDEX`), and a consumer (form clustering, a
training set, a review round) decides what to leave out by `tags`. Nothing is removed for being poor:
a noisy, blurry or thresholded scan is still a witness to its letterform.

`measure` records five numbers under `METHOD`:

- `contrast`: ink to paper, the 5th to the 95th percentile of grey, 0 to 255.
- `mid_grey`: share of pixels more than a fifth of the contrast away from both ink and paper. A
  thresholded scan has almost none.
- `sharpness`: the steepest step between neighbouring pixels (99th percentile) as a share of the
  contrast. A sharp edge crosses the contrast in one or two pixels; a blurred one spreads it over many.
- `noise`: spread of the lightest 40% of pixels, the paper, as a share of the contrast.
- `chroma`: mean distance of a pixel's channels from their own mean. A greyscale scan has none.

The thresholds of `tags` were set on 2026-09-27 on 18 crops each of 38 typeset Honkoku-Lines books,
as the site serves them (WebP re-encodings of the source pixels). There they separate the books a
reader calls blurry (豊橋の年中行事), noisy (風俗畫報) and thresholded (豊橋志要) from clean ones
(小野湖山翁小伝); they are a first calibration, not a validated one.
"""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from collections.abc import Callable, Iterable
from pathlib import Path

import numpy as np
from PIL import Image

from .schema import Page, Unit

#: Names the statistics and how they are computed; a change to either takes a new method.
METHOD = "pixel-statistics-v1"
#: Where the measurements are kept, one row per crop and method.
INDEX = Path("work/crop-quality/index.sqlite")
STATISTICS = ("contrast", "mid_grey", "sharpness", "noise", "chroma")
#: Shorter side, in source pixels, below which a crop is `small`.
SMALL = 24
#: Contrast below which a crop shows no ink to judge sharpness by.
BLANK = 16


def measure(image: Image.Image) -> dict:
    """The statistics of one crop, cut from the page at its source resolution. A transparent crop is
    measured on a white ground, as it is shown."""
    if image.mode in ("RGBA", "LA", "PA") or (image.mode == "P" and "transparency" in image.info):
        ground = Image.new("RGBA", image.size, "white")
        image = Image.alpha_composite(ground, image.convert("RGBA"))
    rgb = np.asarray(image.convert("RGB"), dtype=np.float32)
    grey = rgb.mean(axis=2)
    ink, paper = np.percentile(grey, 5), np.percentile(grey, 95)
    contrast = max(float(paper - ink), 1.0)
    mid = (grey > ink + 0.2 * contrast) & (grey < paper - 0.2 * contrast)
    steps = [np.abs(np.diff(grey, axis=axis)) for axis in (0, 1) if grey.shape[axis] > 1]
    steepest = max((float(np.percentile(step, 99)) for step in steps), default=0.0)
    background = grey[grey >= np.percentile(grey, 60)]
    return {"method": METHOD, "contrast": round(contrast, 1), "mid_grey": round(float(mid.mean()), 3),
            "sharpness": round(steepest / contrast, 3), "noise": round(float(background.std()) / contrast, 3),
            "chroma": round(float(np.abs(rgb - rgb.mean(axis=2, keepdims=True)).mean()), 1)}


def tags(quality: dict | None, box=None) -> list[str]:
    """The quality tags of a crop from its `measure` result and, for `small`, its box on the page."""
    found = []
    if quality and quality.get("method") == METHOD:
        binary = quality["mid_grey"] < 0.02 and quality["contrast"] >= 240
        if binary:
            found.append("binary")
        elif quality["sharpness"] < 0.30 and quality["contrast"] >= BLANK:
            found.append("blurry")
        if quality["noise"] > 0.06:
            found.append("noisy")
        if quality["chroma"] < 1:
            found.append("greyscale")
    if box is not None and min(box.w, box.h) < SMALL:
        found.append("small")
    return found


def connect(path: Path = INDEX) -> sqlite3.Connection:
    """The index, created when missing. A row names the box and page image it was measured on, so a
    crop whose box or page changed is measured again."""
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.execute("""CREATE TABLE IF NOT EXISTS quality (id TEXT NOT NULL, method TEXT NOT NULL,
        box TEXT NOT NULL, page_sha256 TEXT NOT NULL, contrast REAL NOT NULL, mid_grey REAL NOT NULL,
        sharpness REAL NOT NULL, noise REAL NOT NULL, chroma REAL NOT NULL, PRIMARY KEY (id, method))""")
    return db


def _box(unit: Unit) -> str:
    return json.dumps([unit.box.x, unit.box.y, unit.box.w, unit.box.h])


def index_units(db: sqlite3.Connection, units: Iterable[Unit], pages: dict[str, Page],
                path_of: Callable[[str], Path | None]) -> dict[str, int]:
    """Measure every unit with a box whose current box and page image are not in the index yet.

    Units are grouped by page, so each page image is opened once. A page whose cached image is
    missing, or whose size differs from the page record the boxes refer to, is counted and skipped.
    """
    held = {(row[0]): (row[1], row[2]) for row in
            db.execute("SELECT id, box, page_sha256 FROM quality WHERE method=?", (METHOD,))}
    by_page = defaultdict(list)
    for unit in units:
        page = pages.get(unit.page_id) if unit.page_id else None
        if unit.box is None or page is None or not page.sha256:
            continue
        if held.get(unit.id) != (_box(unit), page.sha256):
            by_page[page.id].append(unit)
    counts = defaultdict(int)
    for page_id, waiting in by_page.items():
        page = pages[page_id]
        path = path_of(page.image)
        if path is None:
            counts["page-not-cached"] += len(waiting)
            continue
        with Image.open(path) as handle:
            if page.width and page.height and handle.size != (page.width, page.height):
                counts["page-size-differs"] += len(waiting)
                continue
            image = handle.convert("RGB")
        rows = []
        for unit in waiting:
            b = unit.box
            if b.w < 1 or b.h < 1 or b.x < 0 or b.y < 0 or b.x + b.w > image.width or b.y + b.h > image.height:
                counts["box-outside-page"] += 1
                continue
            quality = measure(image.crop((b.x, b.y, b.x + b.w, b.y + b.h)))
            rows.append((unit.id, METHOD, _box(unit), page.sha256, *(quality[k] for k in STATISTICS)))
        with db:
            db.executemany("INSERT OR REPLACE INTO quality VALUES (?,?,?,?,?,?,?,?,?)", rows)
        counts["measured"] += len(rows)
    return dict(counts)


def lookup(db: sqlite3.Connection, ids: Iterable[str] | None = None) -> dict[str, dict]:
    """The measurements of the given crops, or of every crop, in the form `measure` returns."""
    query = f"SELECT id, {', '.join(STATISTICS)} FROM quality WHERE method=?"
    rows = db.execute(query, (METHOD,))
    wanted = None if ids is None else set(ids)
    return {row[0]: {"method": METHOD, **dict(zip(STATISTICS, row[1:], strict=True))}
            for row in rows if wanted is None or row[0] in wanted}
