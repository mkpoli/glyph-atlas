"""Tell a page's ink from the other pigment on it, and say which character boxes hold ink.

A page is paper that absorbs little light, ink that absorbs about as much of every colour, and
sometimes a red pigment (a ruled line, a seal, a punctuation mark, a stain) that absorbs green and
blue and lets red through. In optical density, the logarithm of how much darker a pixel is than
the paper around it, each pigment lies along a direction of its own whatever its strength, and a
pixel that holds both is the sum of the two. So a pixel's density can be shared out between the
ink's direction and the other pigment's, and a stroke under a seal still shows its ink. The idea
is the one 2SC1815J shows for cleaning stains and 朱点 off old-type images in soan (X, @2SC1815J,
2026-10-31, https://x.com/2SC1815J/status/2116527795329495151): a plane of density with an ink axis
and a redness axis, and a line that divides it. The post is one figure with a short caption and
gives no procedure; how the paper, the directions and the shares are found here is this module's
own, measured on the Kyujanggak land registers (`models/cutter/README.md`).

`inked` says which of the boxes a detector drew hold ink: one on a red ruling, a seal, a stain or
bare paper does not.

Ink of another colour than the page's own is the other pigment here: writing in vermilion, or a
second hand in brown on a page that also has red, has little ink to show.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageFilter

from .schema import Box

#: A pixel with this much density of a pigment holds that pigment.
INKED = 0.15
#: A pixel is taken for another pigment, when that pigment's direction is looked for, where its
#: density aside from the ink exceeds `FLOOR + SLOPE` times its density along it. On the 1722
#: register ink lies at a ratio of about -0.02 and the red of its rulings and seals at about 0.45,
#: with almost nothing between 0.1 and 0.2.
SLOPE = 0.15
FLOOR = 0.02
#: The darkest pixels whose density is within this cosine of grey give the ink's direction: ink
#: darkens every colour, and on a page with little writing the darkest hundredth can be a ruled line.
GREY = 0.95
#: Another pigment is shared out only when this share of the page holds it and its direction is
#: further than this cosine from the ink's; below that the two cannot be told apart by a sum.
SEEN = 0.0005
APART = 0.95
#: The page is reduced by this before its paper is read, and the paper is the `BRIGHT` quantile of
#: this many reduced pixels a side: about 400 px, wider than a seal, so that neither writing nor a
#: solid stamp is read as its own paper.
REDUCE = 16
PAPER = 25
BRIGHT = 0.8
#: A box whose strongest ink is under this share of the page's typical box is on no ink.
INK = 0.4
#: The quantile of a box's pixels its ink is read from: a thin stroke covers little of a large box.
STRONGEST = 99.5
#: The quantile of the page's boxes taken as its typical box.
TYPICAL = 75

@dataclass
class Pigments:
    """A page's pixels by what darkens them."""

    ink: np.ndarray  # H x W, true where the page's ink is, under another pigment or not
    other: np.ndarray  # H x W, true where another pigment is and no ink
    amount: np.ndarray  # H x W, the density each pixel owes to ink


def _paper(page: Image.Image) -> np.ndarray:
    """The colour of the paper around every pixel: a bright quantile of its neighbourhood."""
    small = page.resize((max(1, page.width // REDUCE), max(1, page.height // REDUCE)), Image.Resampling.BILINEAR)
    side = min(PAPER, 2 * (min(small.size) // 2) + 1)
    bright = small.filter(ImageFilter.RankFilter(side, min(side * side - 1, int(BRIGHT * side * side))))
    return np.asarray(bright.resize(page.size, Image.Resampling.BILINEAR), dtype=np.float32)


def density(page: Image.Image) -> np.ndarray:
    """The optical density of every pixel against the paper around it, H x W x 3; nought where it is brighter."""
    page = page.convert("RGB")
    rgb = np.asarray(page, dtype=np.float32)
    return np.maximum(0.0, -np.log10(np.clip(rgb, 1.0, None) / np.maximum(_paper(page), 1.0)))


def axes(dense: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The direction of the page's ink in density, and the direction red pigment leaves it by.

    The ink's is the median density of the darkest hundredth of the pixels that are near grey, or
    grey itself on a page with none; the other is what is left of "absorbs green and blue, not
    red" once the ink's direction is taken out of it.
    """
    grey = np.full(3, 1 / np.sqrt(3.0))
    strength = np.linalg.norm(dense, axis=2)
    neutral = (strength > INKED) & (dense @ grey >= GREY * strength)
    ink = grey
    if neutral.any():
        darkest = dense[neutral & (strength >= np.percentile(strength[neutral], 99.0))]
        ink = np.median(darkest, axis=0)
        ink = ink / np.linalg.norm(ink)
    red = np.array([-2.0, 1.0, 1.0]) / np.sqrt(6.0)
    red = red - red.dot(ink) * ink
    return ink, red / np.linalg.norm(red)


def pigments(page: Image.Image) -> Pigments:
    """Where the page's ink is, where another pigment is alone, and how much ink each pixel holds."""
    dense = density(page)
    ink, red = axes(dense)
    along, aside = dense @ ink, dense @ red
    apart = (aside > FLOOR + SLOPE * along) & (np.hypot(along, aside) > INKED)
    amount, rest = along, np.zeros_like(along)
    if apart.mean() >= SEEN:
        other = np.median(dense[apart], axis=0)
        other = other / np.linalg.norm(other)
        shared = float(ink @ other)
        if shared < APART:
            # Each pixel's density as so much of the ink's direction and so much of the other's.
            beside = dense @ other
            amount = np.maximum(0.0, (along - shared * beside) / (1 - shared * shared))
            rest = np.maximum(0.0, (beside - shared * along) / (1 - shared * shared))
        else:
            amount, rest = np.where(apart, 0.0, along), np.where(apart, along, 0.0)
    held = amount > INKED
    return Pigments(ink=held, other=(rest > INKED) & ~held, amount=amount.astype(np.float32))


def inked(page: Image.Image, boxes: Sequence[Box]) -> list[bool]:
    """Whether each box holds ink: its strongest ink is at least `INK` of the page's typical box.

    A page whose boxes hold no ink at all has nothing to compare against, and every box is kept.
    """
    if not boxes:
        return []
    amount = pigments(page).amount
    height, width = amount.shape
    strongest = []
    for box in boxes:
        # The box within the page, so that a slice never wraps.
        region = amount[max(0, box.y):min(height, box.y + box.h), max(0, box.x):min(width, box.x + box.w)]
        strongest.append(float(np.percentile(region, STRONGEST)) if region.size else 0.0)
    typical = float(np.percentile(strongest, TYPICAL))
    return [typical <= 0 or value >= INK * typical for value in strongest]
