"""How well a scan shows a crop's letterform, measured from its pixels.

A crop keeps every measurement, and a consumer (form clustering, a training set, a review round)
decides what to leave out by `tags`. Nothing is removed for being poor: a noisy, blurry or thresholded
scan is still a witness to its letterform.

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

import numpy as np
from PIL import Image

#: Names the statistics and how they are computed; a change to either takes a new method.
METHOD = "pixel-statistics-v1"
#: Shorter side, in source pixels, below which a crop is `small`.
SMALL = 24


def measure(image: Image.Image) -> dict:
    """The statistics of one crop, cut from the page at its source resolution."""
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
        elif quality["sharpness"] < 0.30:
            found.append("blurry")
        if quality["noise"] > 0.06:
            found.append("noisy")
        if quality["chroma"] < 1:
            found.append("greyscale")
    if box is not None and min(box.w, box.h) < SMALL:
        found.append("small")
    return found
