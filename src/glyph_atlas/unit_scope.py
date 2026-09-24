"""Distinguish character occurrences from source blocks awaiting segmentation."""

from __future__ import annotations

import unicodedata
from typing import Any


def encoded_text(value: str | None) -> str | None:
    if not value:
        return None
    try:
        points = value.split()
        if not all(p.startswith("U+") for p in points):
            return None
        text = "".join(chr(int(p.removeprefix("U+"), 16)) for p in points)
        return text if not any(unicodedata.category(c) == "Cs" for c in text) else None
    except (ValueError, OverflowError):
        return None


def character_count(text: str | None) -> int:
    """Count bases, preserving a combining mark or variation selector with its base."""
    if not text or not text.strip():
        return 0
    count = 0
    for char in unicodedata.normalize("NFC", text):
        cp = ord(char)
        if unicodedata.combining(char) or 0xFE00 <= cp <= 0xFE0F or 0xE0100 <= cp <= 0xE01EF:
            continue
        if not char.isspace():
            count += 1
    return count


def unit_scope(row: dict[str, Any]) -> dict[str, Any]:
    identity = encoded_text(row.get("unicode") or row.get("codepoint") or row.get("code_point"))
    text = identity if identity is not None else (
        row.get("text_source") or row.get("char") or row.get("label") or row.get("reading"))
    count = character_count(text)
    granularity = row.get("granularity")
    needs_split = count != 1 or granularity in {"block", "sequence", "line"} or row.get("kind") == "sequence"
    return {"granularity": granularity or ("char" if not needs_split else "sequence"),
            "character_count": count, "needs_segmentation": needs_split}
