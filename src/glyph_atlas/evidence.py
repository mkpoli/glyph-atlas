"""Evidence versions of a crop: the pixels a claim about it is made on.

A version names a crop, the checksum of the image it is cut from, and its box on that image. A local
crop's image is its page (or its pre-cut crop file), named by the file's sha256 as the collection
reports it in `image_sha256`; a corpus glyph's is its source record, named by `source_revision`. A
recrop or a re-segmentation changes the box or the image, and so makes a new version; the old one
stays, with whatever was said about it.

The id joins the three, `{unit}@{pixels}@{x},{y},{w},{h}`, with an empty box for a whole pre-cut file.
The Worker computes the same id in SQL (`units.crop_version`, migration 0046). A box is four whole
numbers, which both write alike; a crop whose box is anything else has no version, as in SQL.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .schema import Box

_KEYS = ("x", "y", "w", "h")


def box_text(box: Box | Mapping[str, Any] | None) -> str | None:
    """A box as a version writes it, `x,y,w,h`; '' for no box; None for one that is not four whole numbers."""
    if box is None:
        return ""
    values = box.model_dump() if isinstance(box, Box) else box
    if not isinstance(values, Mapping):
        return None
    numbers = [values.get(key) for key in _KEYS]
    if any(isinstance(n, bool) or not isinstance(n, int) for n in numbers):
        return None
    return ",".join(str(n) for n in numbers)


def crop_version(unit_id: str, pixels: str | None, box: Box | Mapping[str, Any] | None) -> str | None:
    """The version id of a crop as it stands, or None when it has no image checksum or whole-pixel box."""
    text = box_text(box)
    if pixels is None or text is None:
        return None
    return f"{unit_id}@{pixels}@{text}"


def record_version(record: Mapping[str, Any]) -> str | None:
    """The version of a published crop record: its id, `image_sha256` or `source_revision`, and box."""
    pixels = record.get("image_sha256")
    return crop_version(record["id"], record.get("source_revision") if pixels is None else pixels, record.get("box"))
