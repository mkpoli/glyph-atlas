"""Review events as they were recorded, read in the terms reviews use now.

The journal is history: an event keeps the words it was saved with, because its fingerprint, its
place in a unit's revision count and the feedback receipts that name it all depend on those words.
Events saved while crops carried a reading layer name the wrong-character issue `reading` and keep
the typed characters as `suggested_reading`. A reader of the journal asks here instead of reading
those keys itself.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def issue(value: Any) -> Any:
    """The issue a recorded review names: `reading` was the wrong-character issue."""
    return "character" if value == "reading" else value


def typed_text(evidence: Mapping[str, Any]) -> str | None:
    """The characters a reviewer typed or chose for a crop, under either recorded key."""
    value = evidence.get("suggested_text", evidence.get("suggested_reading"))
    return value if isinstance(value, str) else None
