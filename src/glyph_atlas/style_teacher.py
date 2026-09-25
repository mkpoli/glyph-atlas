"""Input preparation for the script-style teacher, shared by its training and its use on crops.

The teacher is trained on Calli-Tongji (`data/sources/calli-tongji.yaml`), whose images are
binarised, dark ink on white, with no paper or stone texture. A crop from this project's scans is
brought to the same form before the model sees it: grey, thresholded by Otsu's method, inverted
when the dark side is the larger one (a rubbing's white characters on black), cut to the ink with a
margin, padded to a square and resized.
"""
from __future__ import annotations

import numpy as np
from PIL import Image

from .classify import MEAN, STD

SIZE = 128
#: Calli-Tongji's 书体 label for each value of `data/vocab/style.yaml` the teacher can give.
STYLES = {"篆": "seal", "隶": "clerical", "楷": "regular", "行": "running", "草": "cursive"}
CLASSES = list(STYLES.values())
#: The ink's box grows by this share of its longer side on every edge.
MARGIN = 0.08


def otsu(values: np.ndarray) -> int:
    """The grey level that best separates the histogram of `values` into two classes."""
    histogram = np.bincount(values.ravel(), minlength=256).astype(np.float64)
    weight = np.cumsum(histogram)
    mean = np.cumsum(histogram * np.arange(256))
    total, total_mean = weight[-1], mean[-1]
    background = total - weight
    with np.errstate(divide="ignore", invalid="ignore"):
        between = (total_mean * weight - mean * total) ** 2 / (weight * background)
    return int(np.nanargmax(np.where(np.isfinite(between), between, np.nan)))


def binarise(image: Image.Image) -> np.ndarray:
    """A boolean ink mask of `image`: True where the ink is."""
    grey = np.asarray(image.convert("L"), dtype=np.uint8)
    if grey.max() == grey.min():
        return np.zeros(grey.shape, dtype=bool)
    ink = grey <= otsu(grey)
    return ~ink if ink.mean() > 0.5 else ink


def prepare(image: Image.Image, *, size: int = SIZE) -> Image.Image:
    """`image` as the grey square the teacher takes: black ink on white, cut to the ink."""
    ink = binarise(image)
    rows, columns = np.flatnonzero(ink.any(axis=1)), np.flatnonzero(ink.any(axis=0))
    if len(rows):
        top, bottom, left, right = rows[0], rows[-1] + 1, columns[0], columns[-1] + 1
        ink = ink[top:bottom, left:right]
    height, width = ink.shape
    side = int(max(height, width) * (1 + 2 * MARGIN)) + 1
    canvas = np.full((side, side), 255, dtype=np.uint8)
    y, x = (side - height) // 2, (side - width) // 2
    canvas[y:y + height, x:x + width][ink] = 0
    return Image.fromarray(canvas, mode="L").resize((size, size), Image.Resampling.BILINEAR)


def tensor(image: Image.Image) -> np.ndarray:
    """A prepared square as the (3, size, size) float32 array the backbone takes."""
    array = np.asarray(image, dtype=np.float32) / np.float32(255.0)
    array = (array - np.float32(MEAN)) / np.float32(STD)
    return np.ascontiguousarray(np.repeat(array[None], 3, axis=0))
