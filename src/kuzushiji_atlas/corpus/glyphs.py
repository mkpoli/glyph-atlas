"""Machine-located glyph rectangles, kept apart from line rectangles.

A text occurrence tells you a character is in a line. A **glyph rectangle** tells you
where the character is inside that line. They are different claims with different
strength, so they are stored differently: the line rectangle comes from the source
dataset and is a measurement; the glyph rectangle is ours and is a proposal until a
human review event accepts it.

The registry is a JSON sidecar next to the index, keyed by the *strict* identity key
(:attr:`Occurrence.identity_key`) so a rectangle can only ever attach to the occurrence
it was measured on. Registry entries are validated on load: a glyph rectangle that
falls outside its own line rectangle is rejected rather than served, because that is
the signature of a rectangle that drifted onto a different line.

Nothing here mints a rectangle from arithmetic. ``derived_char_index`` boxes are not
accepted: a probe of an evenly divided line box landed on the neighbouring glyph.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .occurrence import Rect

GLYPHS_FILE = "glyphs.json"

#: Bases a glyph rectangle may legitimately claim.
ALLOWED_BASES = ("machine_projection", "upstream_bbox", "iiif_region")


@dataclass
class GlyphEntry:
    """One isolated glyph rectangle, with the provenance of the claim."""

    identity_key: str
    char: str
    codepoint: str
    x: int
    y: int
    w: int
    h: int
    basis: str
    method: str | None = None
    line_rect: dict[str, int] | None = None
    #: Never a calibrated probability. ``confidence`` stays None unless a review event
    #: supplied one; ``heuristic_score`` is our own uncalibrated ordering hint.
    heuristic_score: float | None = None
    confirmed: bool = False
    source: dict[str, Any] = field(default_factory=dict)
    image_service: str | None = None
    iiif_url: str | None = None
    review: dict[str, Any] = field(default_factory=dict)
    inspection_note: str | None = None
    crop_file: str | None = None
    crop_sha256: str | None = None
    crop_bytes: int | None = None

    def as_rect(self) -> Rect:
        return Rect(
            x=self.x,
            y=self.y,
            w=self.w,
            h=self.h,
            role="glyph",
            basis=self.basis,
            confirmed=self.confirmed,
            confidence=None,
            method=self.method,
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def contained(inner: dict[str, int], outer: dict[str, int]) -> bool:
    """Whether `inner` sits inside `outer`."""
    return (
        inner["x"] >= outer["x"]
        and inner["y"] >= outer["y"]
        and inner["x"] + inner["w"] <= outer["x"] + outer["w"]
        and inner["y"] + inner["h"] <= outer["y"] + outer["h"]
    )


class GlyphRegistry:
    """Glyph rectangles keyed by strict occurrence identity."""

    def __init__(self, entries: Iterable[GlyphEntry] = (), rejected: Iterable[dict] = ()):
        # One primary rectangle per occurrence, plus any alternates. A padded box and
        # a tight ink box describe the same glyph; the padded one renders better, so it
        # is primary, and the other is kept rather than discarded.
        grouped: dict[str, list[GlyphEntry]] = {}
        for entry in entries:
            grouped.setdefault(entry.identity_key, []).append(entry)
        self.entries: dict[str, GlyphEntry] = {}
        self.alternates: dict[str, list[GlyphEntry]] = {}
        for key, group in grouped.items():
            group.sort(key=lambda e: (-(e.w * e.h), e.x, e.y))
            self.entries[key] = group[0]
            if len(group) > 1:
                self.alternates[key] = group[1:]
        self.rejected: list[dict] = list(rejected)

    def __len__(self) -> int:
        return len(self.entries)

    def get(self, identity_key: str) -> GlyphEntry | None:
        return self.entries.get(identity_key)

    def for_char(self, char: str) -> list[GlyphEntry]:
        return [e for e in self.entries.values() if e.char == char]

    @property
    def crop_files(self) -> list[str]:
        return sorted({e.crop_file for e in self.entries.values() if e.crop_file})

    def alternates_for(self, identity_key: str) -> list[GlyphEntry]:
        """Other boxes measured for the same glyph (the tight ink box, say)."""
        return self.alternates.get(identity_key, [])

    def as_dict(self) -> dict[str, Any]:
        return {
            "glyphs": [e.as_dict() for e in self.entries.values()],
            "alternates": [e.as_dict() for group in self.alternates.values() for e in group],
            "rejected": self.rejected,
        }

    # ------------------------------------------------------------------- IO
    @classmethod
    def load(cls, path: str | Path) -> GlyphRegistry:
        path = Path(path)
        if not path.exists():
            return cls()
        payload = json.loads(path.read_text(encoding="utf-8"))
        entries = [GlyphEntry(**row) for row in payload.get("glyphs", [])]
        entries += [GlyphEntry(**row) for row in payload.get("alternates", [])]
        return cls(entries, payload.get("rejected", []))

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.as_dict(), ensure_ascii=False, indent=1), encoding="utf-8")


def identity_key_of(
    source: dict[str, Any], span_start: int, char_class: str, codepoint: str, family: str
) -> str:
    """Rebuild the strict identity key the index assigns to this occurrence.

    Must match :attr:`Occurrence.identity_key` exactly. It is derived from the stored
    fields rather than typed, so an anchor cannot silently attach to the wrong line.
    """
    doc = source.get("document_id") or "-"
    for prefix in ("hl:", "hk:", "cd:", "codh-omt:"):
        if doc.startswith(prefix):
            doc = doc[len(prefix) :]
            break
    return "|".join(
        [
            family,
            doc,
            source.get("page_id") or "-",
            source.get("line_id") or "-",
            codepoint,
            str(span_start),
            char_class,
        ]
    )


def build(anchor_records: Iterable[dict[str, Any]], families: dict[str, str] | None = None) -> GlyphRegistry:
    """Turn inspected anchor records into a registry, rejecting unsafe rectangles.

    An entry is rejected when its glyph rectangle is not inside the line rectangle it
    claims to belong to. That check is the one that stops a measurement made on line A
    from being served as line B's glyph.
    """
    families = families or {}
    entries: list[GlyphEntry] = []
    rejected: list[dict] = []
    for record in anchor_records:
        source = record.get("source") or {}
        text = record.get("text") or {}
        codepoint = record.get("codepoint") or "?"
        span = text.get("span_start")
        char_class = record.get("char_class")
        if span is None or not char_class:
            rejected.append({"reason": "anchor lacks span or class", "line_id": source.get("line_id")})
            continue
        family = families.get(source.get("corpus") or "", source.get("corpus") or "unknown")
        key = identity_key_of(source, span, char_class, codepoint, family)
        line = next((r for r in record.get("rects", []) if r.get("role") == "line"), None)
        glyphs = [r for r in record.get("rects", []) if r.get("role") == "glyph"]
        if not line:
            rejected.append(
                {"reason": "anchor has no line rectangle to validate against", "identity_key": key}
            )
            continue
        for rect in glyphs:
            if rect.get("basis") not in ALLOWED_BASES:
                rejected.append(
                    {
                        "reason": f"basis {rect.get('basis')!r} is not a measured glyph rectangle",
                        "identity_key": key,
                    }
                )
                continue
            box = {k: rect[k] for k in ("x", "y", "w", "h")}
            line_box = {k: line[k] for k in ("x", "y", "w", "h")}
            if not contained(box, line_box):
                rejected.append(
                    {
                        "reason": "glyph rectangle outside its own line rectangle",
                        "identity_key": key,
                        "glyph": box,
                        "line": line_box,
                    }
                )
                continue
            entries.append(
                GlyphEntry(
                    identity_key=key,
                    char=record.get("char") or "?",
                    codepoint=codepoint,
                    x=box["x"],
                    y=box["y"],
                    w=box["w"],
                    h=box["h"],
                    basis=rect.get("basis"),
                    method=rect.get("method"),
                    line_rect=line_box,
                    heuristic_score=rect.get("heuristic_score"),
                    confirmed=bool(rect.get("confirmed")),
                    source=source,
                    image_service=record.get("image_service"),
                    iiif_url=record.get("iiif_url") or record.get("iiif_glyph_url"),
                    review=record.get("review") or {},
                    inspection_note=(record.get("evidence") or {}).get("inspection_note"),
                    crop_file=record.get("crop_file"),
                    crop_sha256=record.get("crop_sha256"),
                    crop_bytes=record.get("crop_bytes"),
                )
            )
    # One entry per (identity, box): keep the larger, better-rendering box.
    best: dict[tuple[str, int, int, int, int], GlyphEntry] = {}
    for entry in entries:
        key = (entry.identity_key, entry.x, entry.y, entry.w, entry.h)
        best[key] = entry
    return GlyphRegistry(best.values(), rejected)
