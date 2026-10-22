"""One writer per export folder.

The exporters (`scripts/export_cloudflare.py`, `scripts/export_cloudflare_corpus.py`) append to an export
folder's packs and rows, and `scripts/atlas3/address_packs.py` renames those packs and rewrites the rows.
Each holds an exclusive lock on the folder's `.export.lock` while it runs, so none of them works on a
folder another is changing.
"""

from __future__ import annotations

import fcntl
import sys
from pathlib import Path

NAME = ".export.lock"


def hold(folder: Path):
    """Lock `folder` for this process, or exit naming the folder when another process holds it. Keep the
    returned file open for as long as the lock is needed."""
    handle = open(Path(folder) / NAME, "a")  # noqa: SIM115 -- held until the process exits
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        sys.exit(f"another process is exporting into or addressing {folder}; nothing done")
    return handle
