"""Names of packs in the site's bucket.

A pack an export writes is published as `packs/<SHA-256 of its bytes>.bin` (`address_packs.py`). The
bucket's root also holds numbered packs of earlier publications (`pack-10001.bin`, `corpus-0001.bin`),
names the exporters hand out again, so a new image or record is published only from an addressed pack.
Keys and crops published before keep the places they have.
"""

from __future__ import annotations

import re

ADDRESSED = re.compile(r"packs/[0-9a-f]{64}\.bin")


def addressed(name: str) -> bool:
    return ADDRESSED.fullmatch(name or "") is not None
