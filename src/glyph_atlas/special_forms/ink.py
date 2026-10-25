"""Each crop framed for restoration, and restored ink trimmed for the encoder.

`frame` puts the grey crop as written (paper kept) into a 96 px square, keeping its aspect and padding with
its paper tone, beside its ink mask: Otsu ink with specks and fragments of neighbouring characters cut in at
the edge removed. It also scores the crop's quality, so the crisp ones can teach the restoration.
`refit` trims restored ink to its extent and fits it into the encoder's frame.
"""
from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor

import numpy as np

#: Side of the restoration and encoder frames.
FRAME = 96
#: Longest side of refitted ink inside its frame.
BOX = 84
#: Least grey range (1st to 99th percentile) of a crop with any ink.
CONTRAST = 25


def keep_mask(ink: np.ndarray):
    """`ink` without specks or edge fragments: pieces under 4% of the largest, and pieces touching the
    edge under 30% of it. Pieces are counted after a small closing, so a dotted stroke is one piece."""
    from scipy import ndimage
    from skimage import measure, morphology

    h, w = ink.shape
    # padded, so the closing does not erode ink at the edge and hide that a piece touches it
    lab = measure.label(ndimage.binary_closing(np.pad(ink, 2), morphology.disk(2))[2:-2, 2:-2], connectivity=2)
    props = measure.regionprops(lab)
    keep = np.zeros_like(ink)
    if not props:
        return keep
    big = max(p.area for p in props)
    for p in props:
        r0, c0, r1, c1 = p.bbox
        edge = r0 == 0 or c0 == 0 or r1 == h or c1 == w
        if p.area < 0.04 * big or (edge and p.area < 0.3 * big):
            continue
        keep |= lab == p.label
    return keep


def frame_one(args) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Grey frame, ink mask and quality `(contrast, sharpness, specks, pieces)` of one crop."""
    from scipy import ndimage
    from skimage import filters, measure, morphology, transform

    g, (h, w) = args
    empty = np.zeros((FRAME, FRAME), np.uint8)
    if h == 0:
        return empty, empty, np.zeros(4, np.float32)
    g = g[:h, :w].astype(np.float32)
    lo, hi = np.percentile(g, 1), np.percentile(g, 99)
    s = FRAME / max(h, w)
    nh, nw = max(1, round(h * s)), max(1, round(w * s))
    oy, ox = (FRAME - nh) // 2, (FRAME - nw) // 2
    grey = np.full((FRAME, FRAME), np.median(g), np.float32)
    grey[oy:oy + nh, ox:ox + nw] = transform.resize(g, (nh, nw), order=1, anti_aliasing=True, preserve_range=True)
    if hi - lo < CONTRAST:
        return grey.astype(np.uint8), empty, np.zeros(4, np.float32)
    raw = g < min(filters.threshold_otsu(g), hi - 0.25 * (hi - lo))
    ink = morphology.remove_small_objects(raw, max_size=2)
    keep = keep_mask(ink)
    if not keep.any():
        return grey.astype(np.uint8), empty, np.zeros(4, np.float32)
    mask = np.zeros((FRAME, FRAME), np.float32)
    mask[oy:oy + nh, ox:ox + nw] = transform.resize((ink & keep).astype(np.float32), (nh, nw), order=1, anti_aliasing=True)
    edge = ndimage.binary_dilation(keep, morphology.disk(1)) ^ ndimage.binary_erosion(keep, morphology.disk(1))
    sharp = filters.sobel(g / 255.0)[edge].mean() / max((hi - lo) / 255, 1e-3)
    specks = 1 - (raw & keep).sum() / max(raw.sum(), 1)
    pieces = measure.label(ink & keep, connectivity=2).max()
    return (grey.astype(np.uint8), (mask > 0.5).astype(np.uint8) * 255,
            np.array([(hi - lo) / 255, sharp, specks, pieces], np.float32))


def frame(grey: np.ndarray, size: np.ndarray, workers: int = 4) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    with ProcessPoolExecutor(workers) as pool:
        out = list(pool.map(frame_one, zip(grey, map(tuple, size), strict=True), chunksize=64))
    return tuple(np.stack([o[k] for o in out]) for k in range(3))


def refit(restored: np.ndarray, usable: np.ndarray) -> np.ndarray:
    """Restored ink (0–255, ink high) of the usable crops, specks and edge fragments dropped, trimmed and fitted."""
    from skimage import transform

    out = np.zeros((len(restored), FRAME, FRAME), np.uint8)
    for i in np.nonzero(usable)[0]:
        keep = keep_mask(restored[i] > 127)
        if not keep.any():
            continue
        ys, xs = np.nonzero(keep)
        a = (restored[i].astype(np.float32) / 255 * keep)[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
        s = BOX / max(a.shape)
        a = transform.resize(a, (max(1, round(a.shape[0] * s)), max(1, round(a.shape[1] * s))), order=1, anti_aliasing=True)
        oy, ox = (FRAME - a.shape[0]) // 2, (FRAME - a.shape[1]) // 2
        out[i, oy:oy + a.shape[0], ox:ox + a.shape[1]] = (a * 255).astype(np.uint8)
    return out
