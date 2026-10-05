"""A crop's paper colour, which the site paints in the crop's place until its image arrives.

The paper is read from the crop's border, a band `BORDER` of the shorter side wide: a crop is cut
around its character with a margin (`glyph_atlas.review.atlas.crop_bounds`), so the border is mostly
paper whatever the ink. Of the border's pixels, those in the middle half by luminance are averaged,
which leaves out strokes crossing the border in either direction: dark ink on paper, and the light
strokes of a rubbing on its dark ground.
"""
from __future__ import annotations

from PIL import Image

#: The border band's width, as a share of the crop's shorter side.
BORDER = 0.12
#: The crop is read at most this many pixels on its longer side: the paper is a mean, and the
#: reduction (a box filter) is that mean taken early.
EDGE = 64


def _luminance(pixel: tuple[int, int, int]) -> float:
    r, g, b = pixel
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def paper_tone(image: Image.Image) -> str:
    """The paper colour of a crop as `#rrggbb`."""
    small = image.convert("RGB")
    small.thumbnail((EDGE, EDGE), Image.Resampling.BOX)
    w, h = small.size
    k = max(1, round(min(w, h) * BORDER))
    pixels = small.load()
    border = [pixels[x, y] for y in range(h) for x in range(w) if x < k or y < k or x >= w - k or y >= h - k]
    border.sort(key=_luminance)
    n = len(border)
    middle = border[n // 4:n - n // 4] or border
    return "#" + "".join(f"{round(sum(p[c] for p in middle) / len(middle)):02x}" for c in range(3))


def crop_tone(path) -> dict:
    """What a crop's record carries about its image before it loads: its paper colour (`tone`) and
    its size in pixels (`image_size`, width and height), from the image file at `path`."""
    with Image.open(path) as image:
        return {"tone": paper_tone(image), "image_size": [image.width, image.height]}
