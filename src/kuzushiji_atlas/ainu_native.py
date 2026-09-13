"""Validate source updates with the configured ainu-records checkout's own code."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any

_SLOTS = threading.BoundedSemaphore(2)
_RUNNER = Path(__file__).parent / "native" / "ainu-feedback.ts"


class NativeAinuError(RuntimeError):
    """Native validation could not run. Feedback must remain unverified."""


def text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _private(message: str) -> str:
    home = Path.home()
    return message.replace(str(home), "~").replace(home.name, "<username>")


def validate_source_updates(
    source: Path | str, proposals: list[dict[str, Any]], *, timeout: float = 45
) -> dict[str, Any]:
    """Return validated file drafts or conflicts; never write to the source checkout.

    Each proposal has ``entry``, ``page_index`` (zero-based), ``canvas``, ``text_sha256``
    and ``correction`` (the source's per-page JSON record). The checksum must come from
    the imported text the reviewer saw. The native loader resolves the publishing unit.

    ``validated`` is false on any conflict and then ``files`` is empty. A successful file
    draft includes its original bytes and checksum for review and a later stale-file check.
    This validates placement and format, not the linguistic accuracy of a correction.
    """
    root = Path(source).expanduser().resolve()
    if not (root / "scripts/lib/corrections.ts").is_file():
        raise NativeAinuError("The configured source checkout has no native correction validator")
    bun = shutil.which("bun")
    if not bun:
        raise NativeAinuError("Bun is required to validate ainu-records source updates")
    if not isinstance(proposals, list) or not 1 <= len(proposals) <= 100:
        raise NativeAinuError("Submit between 1 and 100 source corrections at a time")
    payload = json.dumps({"version": 1, "proposals": proposals}, ensure_ascii=False).encode("utf-8")
    if len(payload) > 1_000_000:
        raise NativeAinuError("Source corrections exceed the 1 MB submission limit")
    if not _SLOTS.acquire(timeout=1):
        raise NativeAinuError("Source validation is busy; retry shortly")
    try:
        try:
            result = subprocess.run(
                [bun, str(_RUNNER), str(root)], input=payload, capture_output=True,
                timeout=timeout, check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise NativeAinuError("Source validation timed out; feedback remains unverified") from exc
        except OSError as exc:
            raise NativeAinuError("Could not start the source validator") from exc
    finally:
        _SLOTS.release()
    if result.returncode:
        raise NativeAinuError("The native source validator failed to start or load")
    try:
        output = json.loads(result.stdout)
    except (ValueError, UnicodeError) as exc:
        raise NativeAinuError("The native source validator returned an invalid response") from exc
    if not isinstance(output, dict) or output.get("version") != 1 or not isinstance(
        output.get("validated"), bool
    ):
        raise NativeAinuError("The native source validator returned an unsupported response")
    # Native errors may contain filesystem paths. File contents are never altered by redaction.
    for conflict in output.get("conflicts", []):
        if isinstance(conflict, dict) and isinstance(conflict.get("reason"), str):
            conflict["reason"] = _private(conflict["reason"])
    return output
