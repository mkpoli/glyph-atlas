"""Fast review hints from nearby transcription, independent of image recognition."""

from __future__ import annotations

import unicodedata
from difflib import SequenceMatcher

from .. import koji
from ..schema import Line, Unit


def _normalize(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def context_guesses(unit: Unit, line: Line | None, neighbors: list[Unit]) -> dict:
    """Offer nearby characters and contiguous spans without asserting a visual match.

    Match original unit text to parsed transcription, rather than treating `seq` as a
    string offset: missed detections, split glyphs, spaces and ruby all affect that offset.
    No OCR, language-model score or automatic-repair vote is produced here.
    """
    unavailable = {"status": "unavailable", "candidates": [], "basis": "transcription"}
    if line is None or not unit.text_source or len(line.text_raw) > 8192:
        return unavailable
    parsed = koji.parse(line.text_raw)
    if parsed.malformed:
        return unavailable
    tokens: list[str] = []
    barriers: set[int] = set()
    for char in parsed.chars:
        if char.role not in {"main", "warigaki", "gap", "unreadable"}:
            continue
        # Combining kana marks and variation selectors belong to the preceding glyph.
        cp = ord(char.text)
        if tokens and (unicodedata.combining(char.text) or 0xFE00 <= cp <= 0xFE0F
                       or 0xE0100 <= cp <= 0xE01EF):
            tokens[-1] = _normalize(tokens[-1] + char.text)
        else:
            # Printed marks occupy ink just like letters. They may share a detector
            # box with a neighbour, so keep them in joined-crop suggestions.
            if char.role in {"gap", "unreadable", "warigaki"} or char.text.isspace():
                barriers.add(len(tokens))
            tokens.append(_normalize(char.text))
    if not tokens or len(tokens) > 2048:
        return unavailable
    source: list[str] = []
    positions: dict[str, int] = {}
    groups: dict[str, int] = {}
    for neighbor in sorted(neighbors, key=lambda u: (u.seq, u.id)):
        if (not neighbor.active or not neighbor.text_source
                or neighbor.upstream.get("role") in {"ruby", "ruby-left"}):
            continue
        if neighbor.group_id and neighbor.group_id in groups:
            positions[neighbor.id] = groups[neighbor.group_id]
            continue
        positions[neighbor.id] = len(source)
        if neighbor.group_id:
            groups[neighbor.group_id] = len(source)
        source.append(_normalize(neighbor.text_source))
    position = positions.get(unit.id)
    if position is None or len(source) > 2048:
        return unavailable
    anchor = None
    for match in SequenceMatcher(None, source, tokens, autojunk=False).get_matching_blocks():
        if match.a <= position < match.a + match.size:
            anchor = match.b + position - match.a
            break
    if anchor is None or anchor in barriers:
        return unavailable

    candidates = []
    seen: set[str] = set()
    # The first group covers a shifted single-character label; the second covers joined ink.
    spans = [(0, 1), (1, 1), (-1, 1), (2, 1), (-2, 1),
             (0, 2), (0, 3), (-1, 2), (-1, 3), (1, 2), (0, 4), (1, 3), (-2, 3)]
    for offset, length in spans:
        start, end = anchor + offset, anchor + offset + length
        if start < 0 or end > len(tokens) or any(i in barriers for i in range(start, end)):
            continue
        text = "".join(tokens[start:end])
        if text in seen or not text or any((unicodedata.category(c)[0] in "PZ" or unicodedata.category(c) in {"Cc", "Cf", "Cs"}) for c in text):
            continue
        seen.add(text)
        candidates.append({"text": text, "engine": "Nearby transcription", "offset": offset,
                           "before": "".join(tokens[max(0, start - 4):start]),
                           "after": "".join(tokens[end:end + 4])})
    return {"status": "ready" if candidates else "unavailable", "basis": "transcription",
            "candidates": candidates, "line_id": line.id, "anchor": anchor,
            "source_text": unit.text_source}
