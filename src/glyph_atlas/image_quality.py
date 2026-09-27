"""How well a scan shows a crop's letterform, measured from its pixels.

The measurements of every crop are kept in an index (`INDEX`), and a consumer (form clustering, a
training set, a review round) decides what to leave out by `tags`. Nothing is removed for being poor:
a noisy, blurry or thresholded scan is still a witness to its letterform.

`measure` scales a crop down to a shorter side of `EDGE` pixels, so that sharpness and noise are read at
the scale of the glyph and not of the scan's resolution, then records five numbers under `METHOD`:

- `contrast`: ink to paper, the 5th to the 95th percentile of grey, 0 to 255.
- `mid_grey`: share of pixels more than a fifth of the contrast away from both ink and paper. A
  thresholded scan has almost none.
- `sharpness`: the steepest step between neighbouring pixels (99th percentile) as a share of the
  contrast. A sharp edge crosses the contrast in one or two pixels; a blurred one spreads it over many.
- `noise`: spread of the lightest 40% of pixels, the paper, as a share of the contrast.
- `chroma`: mean distance of a pixel's channels from their own mean. A greyscale scan has none.

Thresholding, blur, noise and colour belong to a scan, so `tags` reads them from the median of a
page's crops (`page_summary`); only `small` belongs to the crop. The thresholds were set on
2026-09-27 on the 368,860 extracted crops of the review store. They separate the books a reader calls
blurry (豊橋の年中行事), noisy (風俗畫報, every one of its 105 pages) and thresholded (豊橋志要) from
clean ones (小野湖山翁小伝) and from brush writing on high-resolution scans. They are a first
calibration, not a validated one.
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
METHOD = "pixel-statistics-v2"
#: Shorter side, in pixels, a larger crop is scaled down to before it is measured.
EDGE = 48
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
    if min(image.size) > EDGE:
        scale = EDGE / min(image.size)
        image = image.resize((max(1, round(image.width * scale)), max(1, round(image.height * scale))),
                             Image.Resampling.BOX)
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


def page_summary(qualities: Iterable[dict]) -> dict | None:
    """The median of each statistic over a page's crops, in the form `measure` returns."""
    current = [q for q in qualities if q and q.get("method") == METHOD]
    if not current:
        return None
    return {"method": METHOD, **{k: float(np.median([q[k] for q in current])) for k in STATISTICS}}


def tags(page: dict | None, box=None) -> list[str]:
    """The quality tags of a crop: the scan's from its page's `page_summary`, `small` from its box."""
    found = []
    if page and page.get("method") == METHOD:
        if page["mid_grey"] < 0.02 and page["contrast"] >= 240:
            found.append("binary")
        elif page["sharpness"] < 0.32 and page["contrast"] >= BLANK:
            found.append("blurry")
        if page["noise"] > 0.065:
            found.append("noisy")
        if page["chroma"] < 1:
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
        page_id TEXT NOT NULL, box TEXT NOT NULL, page_sha256 TEXT NOT NULL, contrast REAL NOT NULL, mid_grey REAL NOT NULL,
        sharpness REAL NOT NULL, noise REAL NOT NULL, chroma REAL NOT NULL, PRIMARY KEY (id, method))""")
    return db


def _box(unit: Unit) -> str:
    return json.dumps([unit.box.x, unit.box.y, unit.box.w, unit.box.h])


def index_units(db: sqlite3.Connection, units: Iterable[Unit], pages: dict[str, Page],
                image_of: Callable[[str, str | None], tuple[Path, str] | None]) -> dict[str, int]:
    """Measure every unit with a box whose current box and page image are not in the index yet.

    `image_of(url, sha256)` finds a page's cached image and its sha256 (`images.resolver`), so a row
    names the very file it was measured on. Units are grouped by page, so each page image is opened
    once. A unit whose page is unknown, uncached, unreadable, of no recorded size or of another size
    is counted and skipped.
    """
    held = {(row[0]): (row[1], row[2]) for row in
            db.execute("SELECT id, box, page_sha256 FROM quality WHERE method=?", (METHOD,))}
    by_page = defaultdict(list)
    counts = defaultdict(int)
    for unit in units:
        if unit.box is None:
            continue
        page = pages.get(unit.page_id) if unit.page_id else None
        if page is None:
            counts["page-unknown"] += 1
        else:
            by_page[page.id].append(unit)
    for page_id, waiting in by_page.items():
        page = pages[page_id]
        found = image_of(page.image, page.sha256)
        if found is None:
            counts["page-not-cached"] += len(waiting)
            continue
        path, sha256 = found
        waiting = [unit for unit in waiting if held.get(unit.id) != (_box(unit), sha256)]
        if not waiting:
            continue
        if not page.width or not page.height:
            counts["page-size-unknown"] += len(waiting)
            continue
        try:
            with Image.open(path) as handle:
                if handle.size != (page.width, page.height):
                    counts["page-size-differs"] += len(waiting)
                    continue
                image = handle.convert("RGB")
        except (OSError, ValueError):
            counts["page-unreadable"] += len(waiting)
            continue
        rows = []
        for unit in waiting:
            b = unit.box
            if b.w < 1 or b.h < 1 or b.x < 0 or b.y < 0 or b.x + b.w > image.width or b.y + b.h > image.height:
                counts["box-outside-page"] += 1
                continue
            quality = measure(image.crop((b.x, b.y, b.x + b.w, b.y + b.h)))
            rows.append((unit.id, METHOD, page.id, _box(unit), sha256, *(quality[k] for k in STATISTICS)))
        with db:
            db.executemany("INSERT OR REPLACE INTO quality VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
        counts["measured"] += len(rows)
    return dict(counts)


def forget(db: sqlite3.Connection, ids: Iterable[str]) -> int:
    """Drop the rows of crops that no longer exist, so they leave their page's median."""
    with db:
        return db.executemany("DELETE FROM quality WHERE id=?", [(i,) for i in ids]).rowcount


def lookup(db: sqlite3.Connection, ids: Iterable[str] | None = None) -> dict[str, dict]:
    """The measurements of the given crops, or of every crop, in the form `measure` returns, with the
    crop's `page_id` and the `page_summary` of its page as `page`."""
    rows = db.execute(f"SELECT id, page_id, {', '.join(STATISTICS)} FROM quality WHERE method=?", (METHOD,))
    crops, by_page = {}, defaultdict(list)
    for row in rows:
        quality = {"method": METHOD, **dict(zip(STATISTICS, row[2:], strict=True))}
        crops[row[0]] = {**quality, "page_id": row[1]}
        by_page[row[1]].append(quality)
    pages = {page_id: page_summary(qualities) for page_id, qualities in by_page.items()}
    wanted = crops.keys() if ids is None else set(ids) & crops.keys()
    return {i: {**crops[i], "page": pages[crops[i]["page_id"]]} for i in wanted}
