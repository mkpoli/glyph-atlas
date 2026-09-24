"""One character occurrence in a source text, and what is actually known about it.

The atlas has character *units* (a glyph located on a page image) and page
*transcriptions* (text). Between the two sits a third thing that the gallery and the
search box need: a **character occurrence** — a character at a known offset in a
known source text. An occurrence may or may not have a located rectangle, and the
whole point of this module is to keep those two facts apart instead of letting a
text hit masquerade as a crop.

Three evidence tiers, and they are not interchangeable:

``page_text``
    The character occurs somewhere in a page transcription. Nothing is known about
    where on the page. A gallery must not draw it as a glyph tile.
``line_text``
    The character occurs in a line whose transcription is known, but the line has no
    rectangle (the upstream dataset did not carry one).
``line_rect``
    The character occurs in a line that carries a real rectangle in source-image
    pixels, taken from the upstream bbox / IIIF region. The rectangle is the *line*;
    the glyph inside it is not located by this alone.

A glyph rectangle is only ever attached as a separate, explicitly-basis-tagged
:class:`Rect` with ``role="glyph"``, and it is never ``confirmed`` unless a human
review event says so. Machine output is not human-reviewed truth.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

#: The トモ ligature. A single character that writes two kana.
TOMO = "\U0002a708"

#: Honkoku markup for "this glyph is the ligature": 〖𪜈：合字〗 / 【𪜈：合字】.
LIGATURE_TAG = "\u5408\u5b57"
_OPEN, _CLOSE = "\u3016\u3010", "\u3017\u3011"


def annotation_pattern(char: str) -> re.Pattern[str]:
    """The Honkoku annotation that names `char` as a ligature."""
    return re.compile(
        "[" + _OPEN + "]" + r"\s*" + re.escape(char) + r"\s*[:：]\s*" + LIGATURE_TAG + r"\s*[" + _CLOSE + "]"
    )


#: How the occurrence relates to the manuscript glyph.
OccurrenceClass = Literal["literal_text", "annotated_ligature", "expanded_text"]

#: What kind of rectangle, if any, is attached.
RectRole = Literal["line", "glyph", "page"]

#: How a rectangle was obtained. Only ``upstream_bbox`` is a measurement by the
#: source project; everything else is ours and is labelled as such.
RectBasis = Literal[
    "upstream_bbox",  # the source dataset published this rectangle
    "iiif_region",  # the rectangle is a IIIF region request we can re-issue
    "machine_projection",  # machine-located inside a line crop (needs review)
    "derived_char_index",  # arithmetic estimate; ADVISORY ONLY, never a crop
]


@dataclass(frozen=True)
class Rect:
    """A rectangle in source-image pixels, with the basis that produced it."""

    x: int
    y: int
    w: int
    h: int
    role: RectRole
    basis: RectBasis
    #: True only when a human review event covers this exact rectangle.
    confirmed: bool = False
    confidence: float | None = None
    method: str | None = None

    def iiif_region(self) -> str:
        return f"{self.x},{self.y},{self.w},{self.h}"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Source:
    """Where the text and the image come from, and under what terms."""

    corpus: str
    document_id: str
    page_id: str | None = None
    line_id: str | None = None
    title: str | None = None
    holder: str | None = None
    shelfmark: str | None = None
    production: str | None = None
    text_register: str | None = None
    dating: list[dict[str, Any]] = field(default_factory=list)
    revision: str | None = None
    #: Which page/line numbering convention this corpus publishes under. Records are
    #: only ever merged inside one family, because comparing numbering across families
    #: is not proof of identity.
    id_family: str = "unknown"
    source_refs: dict[str, str] = field(default_factory=dict)
    image: str | None = None
    canvas: str | None = None
    iiif_region_url: str | None = None
    image_rights: dict[str, Any] | None = None
    text_rights: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Occurrence:
    """A character at a known offset in a known source text."""

    occurrence_id: str
    char: str
    codepoint: str
    char_class: OccurrenceClass
    tier: Literal["page_text", "line_text", "line_rect"]
    source: Source
    span_start: int
    span_end: int
    text_raw: str
    context: str
    rects: list[Rect] = field(default_factory=list)
    ocr_text: str | None = None
    ocr_agrees_with_ligature: bool | None = None
    review_state: str = "machine"
    review_events: list[dict[str, Any]] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------ derived
    @property
    def identity_key(self) -> str:
        """Stable key for one manuscript glyph, across overlapping imports.

        Two imports of the same page (``honkoku-lines``, ``honkoku-data`` and the Ainu
        record pass all read Honkoku) describe the same physical glyph, so the key is
        the *canonical upstream text identity*: document, page, line and the character
        offset inside that line.

        The line is part of the key and must be. Page plus offset is not unique — two
        different lines of one page routinely both have a character at offset 2 — and
        keying on that would silently merge two different glyphs into one row.

        The key is *strict*: it uses the raw page and line ids as published, under an
        id family that identifies the numbering convention. It deliberately does not
        try to reconcile a 0-based page in one import with a 1-based page in another;
        that reconciliation is a guess, and a guess must not be allowed to delete
        evidence. Records that merely *look* like the same glyph are linked through
        :attr:`soft_group_key` instead.
        """
        return "|".join(
            [
                self.source.id_family,
                _canonical_document(self.source.document_id),
                self.source.page_id or "-",
                self.source.line_id or "-",
                self.codepoint,
                str(self.span_start),
                self.char_class,
            ]
        )

    @property
    def soft_group_key(self) -> str:
        """A link between records that are *probably* the same manuscript glyph.

        Built from what both imports can be trusted to agree on: the upstream
        document, the exact line text, and the character's offset within it. Two
        imports of one page agree on all three even when they number the page
        differently, and a character at the same offset of a different line almost
        never has identical surrounding text.

        This is a *link*, not a merge key. Callers group by it to show "seen in 3
        imports" while every underlying record keeps its own provenance.
        """
        body = self.text_raw or self.context
        digest = hashlib.sha1(body.encode("utf-8")).hexdigest()[:12]
        return "|".join(
            [_canonical_document(self.source.document_id), digest, self.codepoint, str(self.span_start)]
        )

    @property
    def content_key(self) -> str:
        """Dedup key that keeps genuinely different readings of one glyph apart."""
        return f"{self.identity_key}|{self.char_class}"

    @property
    def has_crop(self) -> bool:
        """True only when a real, re-fetchable image rectangle exists."""
        return any(
            r.role in ("line", "glyph") and r.basis in ("upstream_bbox", "iiif_region", "machine_projection")
            for r in self.rects
        )

    @property
    def has_glyph_rect(self) -> bool:
        return any(r.role == "glyph" for r in self.rects)

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["identity_key"] = self.identity_key
        d["content_key"] = self.content_key
        d["has_crop"] = self.has_crop
        d["has_glyph_rect"] = self.has_glyph_rect
        return d


#: Line id shapes the importers produce, and how to read (page, line) out of each.
#: Normalising to a line *ordinal* is what lets the same glyph imported under two
#: different id schemes collapse to one row instead of two.
_LINE_PATTERNS = (
    re.compile(r"^(?P<doc>.+)_(?P<page>\d+)_(?P<line>\d+)$"),  # hl:DOC_1_000
    re.compile(r"^(?P<doc>.+):(?P<page>\d+):L(?P<line>\d+)$"),  # hk:DOC:62:L18
    re.compile(r"^(?P<doc>.+):(?P<page>\d+):(?P<line>\d+)$"),
)


def line_ordinal(line_id: str | None) -> str:
    """A page-and-line token that is comparable across importers.

    Falls back to the raw id when the shape is unfamiliar, which keeps occurrences
    distinct rather than merging them on a guess.
    """
    if not line_id:
        return "-"
    for pattern in _LINE_PATTERNS:
        match = pattern.match(line_id)
        if match:
            return f"p{match.group('page')}.l{match.group('line')}"
    return line_id


def _canonical_document(document_id: str) -> str:
    """Strip our own corpus prefix so the same upstream document collapses."""
    for prefix in ("hl:", "hk:", "cd:", "codh-omt:"):
        if document_id.startswith(prefix):
            return document_id[len(prefix) :]
    return document_id


def codepoint(char: str) -> str:
    return f"U+{ord(char):04X}"


def _is_blank(ch: str) -> bool:
    return ch in ("\u3000", " ", "\n", "\t", "\r")


def classify(text: str, index: int, char: str) -> OccurrenceClass:
    """Whether the transcription *writes* the character or *annotates* it.

    ``literal_text`` — the transcription commits to the character at this offset.
    ``annotated_ligature`` — the transcription writes the expansion and tags the
    manuscript glyph with 〖X：合字〗; a human transcriber decided the glyph is the
    ligature, which is stronger evidence about the manuscript but is *not* a literal
    reading of the text at this offset.
    """
    for m in annotation_pattern(char).finditer(text):
        if m.start() <= index < m.end():
            return "annotated_ligature"
    # 兵トモ〖𪜈：合字〗 — the expansion sits immediately before the annotation.
    tail = text[index:]
    m = annotation_pattern(char).match(tail)
    if m and m.start() == 0:
        return "annotated_ligature"
    return "literal_text"


def find_occurrences(
    text: str, char: str, *, context: int = 14
) -> list[tuple[int, int, OccurrenceClass, str]]:
    """Every occurrence of `char` in `text`, with its class and a context window."""
    found: list[tuple[int, int, OccurrenceClass, str]] = []
    if not text:
        return found
    for m in re.finditer(re.escape(char), text):
        i = m.start()
        found.append((i, m.end(), classify(text, i, char), text[max(0, i - context) : m.end() + context]))
    return found


def char_index_offset(text: str) -> int:
    """How many leading layout blanks precede the first real glyph of a column."""
    n = 0
    for ch in text:
        if _is_blank(ch):
            n += 1
        else:
            break
    return n


def advisory_char_rect(line: Rect, text: str, span_start: int) -> Rect | None:
    """Arithmetic guess at the glyph cell inside a line box.

    This exists because an evenly divided line box is *not* a trustworthy glyph
    crop — real columns have uneven leading, rubi and merged cursive strokes, and a
    probe of one landed on the neighbouring glyph. It is returned with
    ``basis="derived_char_index"`` so that callers can refuse it. Do not put the
    result in a glyph grid.
    """
    n = len(text)
    if not n or line.role != "line":
        return None
    lead = char_index_offset(text)
    cells = max(1, n - lead)
    rel = max(0, span_start - lead)
    if line.h >= line.w:
        cell = line.h / cells
        return Rect(
            x=line.x,
            y=round(line.y + rel * cell),
            w=line.w,
            h=round(cell),
            role="glyph",
            basis="derived_char_index",
            confidence=None,
            method="evenly_divided_line_box_advisory",
        )
    cell = line.w / cells
    return Rect(
        x=round(line.x + rel * cell),
        y=line.y,
        w=round(cell),
        h=line.h,
        role="glyph",
        basis="derived_char_index",
        confidence=None,
        method="evenly_divided_line_box_advisory",
    )


def deduplicate(occurrences: Iterable[Occurrence]) -> list[Occurrence]:
    """Collapse records that are *proven* to be the same occurrence.

    Proof here means the strict identity key: one id family, one document, one page id,
    one line id, one code point, one offset, one class. Records that merely look alike
    across numbering schemes are **kept**; they are linked by ``soft_group_key`` and
    each carries ``meta["possible_same_glyph_as"]`` naming the others.

    Erring toward keeping is deliberate. A duplicated row is a visible annoyance; a
    wrongly merged row is invisible and destroys evidence.
    """
    best: dict[str, Occurrence] = {}
    for occ in occurrences:
        key = occ.identity_key
        prior = best.get(key)
        if prior is None:
            best[key] = occ
            continue
        winner, loser = (occ, prior) if _rank(occ) > _rank(prior) else (prior, occ)
        seen = set(winner.meta.get("also_seen_in") or [])
        seen.add(loser.source.corpus)
        seen.update(loser.meta.get("also_seen_in") or [])
        seen.discard(winner.source.corpus)
        winner.meta["also_seen_in"] = sorted(seen)
        best[key] = winner

    kept = sorted(
        best.values(),
        key=lambda o: (o.source.corpus, o.source.document_id, o.source.page_id or "", o.span_start),
    )
    _link_probable_duplicates(kept)
    return kept


def _link_probable_duplicates(kept: list[Occurrence]) -> None:
    """Annotate records that probably describe the same glyph, without merging them."""
    groups: dict[str, list[Occurrence]] = {}
    for occ in kept:
        groups.setdefault(occ.soft_group_key, []).append(occ)
    for key, members in groups.items():
        if len(members) < 2:
            continue
        for occ in members:
            others = [m.occurrence_id for m in members if m.occurrence_id != occ.occurrence_id]
            occ.meta["possible_same_glyph_as"] = sorted(others)
            occ.meta["soft_group_key"] = key
            occ.meta["same_glyph_is_proven"] = False


def _rank(occ: Occurrence) -> tuple:
    """Which import to keep when the same glyph arrives twice."""
    return (
        1 if occ.has_glyph_rect else 0,
        1 if occ.has_crop else 0,
        {"line_rect": 2, "line_text": 1, "page_text": 0}[occ.tier],
        1 if occ.ocr_agrees_with_ligature else 0,
        len(occ.rects),
    )


def occurrence_id(source: Source, char: str, span_start: int, char_class: str) -> str:
    """A stable, content-addressed id for one occurrence.

    The code point is carried in the id next to the digest so a single-occurrence
    lookup is one file read (``occ/<codepoint>.parquet``) plus a scan of that file,
    instead of opening every per-character file to find one row.
    """
    raw = "|".join(
        [
            source.corpus,
            source.document_id,
            source.page_id or "",
            source.line_id or "",
            codepoint(char),
            str(span_start),
            char_class,
        ]
    )
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]
    return f"occ:{codepoint(char)}:{digest}"


#: ``occ:U+2A708:<digest>``. The plus is part of the id, and a query string turns it
#: into a space, so the id is normalised before use rather than trusted as received.
_OCCURRENCE_ID = re.compile(r"^(occ):([Uu])[+ ]([0-9A-Fa-f]{4,6}):")


def normalise_occurrence_id(occurrence_id: str) -> str:
    """Repair an occurrence id that came back through a form decoder.

    ``+`` decodes to a space in a query string, so ``occ:U+2A708:ab`` arrives as
    ``occ:U 2A708:ab``. Restoring it here means a caller does not have to know which
    decoder its transport used.
    """
    return _OCCURRENCE_ID.sub(lambda m: f"{m.group(1)}:U+{m.group(3)}:", occurrence_id)


def codepoint_of_occurrence_id(occurrence_id: str) -> str | None:
    """The code point embedded in an occurrence id, when it has one."""
    match = _OCCURRENCE_ID.match(occurrence_id)
    return f"U+{match.group(3).upper()}" if match else None


def name_of(char: str) -> str:
    try:
        return unicodedata.name(char)
    except ValueError:
        return ""


def as_json(occurrences: Iterable[Occurrence], **extra: Any) -> str:
    payload = {"occurrences": [o.as_dict() for o in occurrences]}
    payload.update(extra)
    return json.dumps(payload, ensure_ascii=False, indent=1)
