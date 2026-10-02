"""Evidence versions of a crop: the pixels a claim about it is made on.

A version names a crop, the checksum of the image it is cut from, and its box on that image. A local
crop's image is its page (or its pre-cut crop file), named by the file's sha256 as the collection
reports it in `image_sha256`; a corpus glyph's is its source record, named by `source_revision`. A
recrop or a re-segmentation changes the box or the image, and so makes a new version; the old one
stays, with whatever was said about it.

The id joins the three, `{unit}@{pixels}@{x},{y},{w},{h}`, with an empty box for a whole pre-cut file.
The Worker computes the same id in SQL (`units.crop_version`, migration 0046), so a number is written
as SQLite writes it: an integer without a decimal point, a real with one.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .schema import Box


def _number(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"a box coordinate must be a number, not {value!r}")
    return str(value)


def box_text(box: Box | Mapping[str, Any] | None) -> str | None:
    """A box as a version writes it, `x,y,w,h`; None for no box."""
    if box is None:
        return None
    values = box.model_dump() if isinstance(box, Box) else box
    return ",".join(_number(values[key]) for key in ("x", "y", "w", "h"))


def crop_version(unit_id: str, pixels: str | None, box: Box | Mapping[str, Any] | None) -> str | None:
    """The version id of a crop as it stands, or None when its image has no checksum."""
    if pixels is None:
        return None
    return f"{unit_id}@{pixels}@{box_text(box) or ''}"


def record_version(record: Mapping[str, Any]) -> str | None:
    """The version of a published crop record: its id, `image_sha256` or `source_revision`, and box."""
    pixels = record.get("image_sha256")
    return crop_version(record["id"], record.get("source_revision") if pixels is None else pixels, record.get("box"))
