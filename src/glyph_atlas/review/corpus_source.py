"""The corpus index, read in process through the corpus worker's own `CorpusAPI`.

One corpus, one index, one adapter. The corpus package exposes a tested HTTP-shaped interface —
`CorpusAPI(root, index_directory).handle_get("/api/corpus/counts?...")` returning
`(status, content_type, body)` — so the layer calls it in process and decodes the same JSON a client
would get, without a second server and without a second way to count anything.

The counts are kept apart by name, never by arithmetic on neighbouring fields:

- `n_glyphs` — located character units. Only these may be drawn as crops or counted as one.
- `n_line_hits`, `n_page_hits` — text matches. A line hit has the rectangle of the line, which is
  not a rectangle around the character, so it is shown as a source match and never as a crop.
- A ト followed by a モ counts under ト and under モ. It is not 𪜈, so it counts under no ligature.

An index the deployment does not have answers `None`; a count the index does not state stays unknown.
Neither is turned into a zero here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

#: Set once by the application: one `CorpusAPI` per app, shared by every request.
_api: Any | None = None


class CorpusUnavailable(RuntimeError):
    """There is no corpus index to read: no package, no index built, or the service says so."""


class CorpusError(RuntimeError):
    """The corpus index exists but could not answer. A fault, not an empty answer."""


def connect(api: Any | None) -> Any | None:
    """Register the corpus API the application built. `None` means this deployment has no index."""
    global _api
    _api = api
    return _api


def open_api(root: Path | str | None = None, index_directory: Path | str | None = None) -> Any | None:
    """Build a `CorpusAPI` over the corpus index, or `None` when the corpus package is not installed."""
    try:
        from ..corpus.api import CorpusAPI  # type: ignore[import-not-found]
    except Exception:  # noqa: BLE001 — the corpus package is optional and never copied
        return None
    try:
        return CorpusAPI(root, index_directory)
    except Exception:  # noqa: BLE001 — an index that is not built is not an error
        return None


def current() -> Any | None:
    return _api


def ready() -> bool:
    """Whether a corpus index is actually built behind the API.

    The API object can exist while its index does not — a deployment that has the corpus package and
    no scan — and that state must not read as "the corpus says zero". The index answers `exists()`.
    """
    index = getattr(_api, "index", None)
    exists = getattr(index, "exists", None)
    if callable(exists):
        try:
            return bool(exists())
        except Exception:  # noqa: BLE001 — an index that cannot answer is not a ready index
            return False
    return False


def _get(path: str, **params: Any) -> dict[str, Any]:
    """One read through the corpus API's own route.

    Raises `CorpusUnavailable` when there is no index to read and `CorpusError` when the index could
    not answer. Neither is turned into an empty result: a collection that shows no tiles because the
    corpus is broken is telling the reader something false, and the caller has to be able to tell the
    two apart to say which it is.
    """
    if _api is None:
        raise CorpusUnavailable("no corpus index is connected")
    query = urlencode({key: value for key, value in params.items() if value is not None},
                      quote_via=quote, safe="")
    try:
        status, _content_type, body = _api.handle_get(f"{path}?{query}")
    except Exception as error:  # the fault is reported to the caller, never swallowed
        raise CorpusError(f"the corpus API raised {type(error).__name__}") from error
    if status == 503:
        raise CorpusUnavailable("the corpus index is not built")
    if status != 200:
        raise CorpusError(f"the corpus API answered {status}")
    try:
        return json.loads(body.decode("utf-8") if isinstance(body, bytes) else body)
    except (ValueError, AttributeError) as error:
        raise CorpusError("the corpus API did not answer JSON") from error


def safe(call, *args: Any, **kwargs: Any) -> tuple[Any | None, str | None]:
    """`(payload, fault)`: `fault` is `not-loaded`, `error`, or `None` for a good answer.

    The one place the two failures are turned into a word, so that every caller names them the same
    way and none of them mistakes a fault for a corpus that holds nothing.
    """
    try:
        return call(*args, **kwargs), None
    except CorpusUnavailable:
        return None, "not-loaded"
    except CorpusError:
        return None, "error"


def gallery(limit: int = 24, seed: int = 0) -> dict[str, Any]:
    """A bounded sample of located glyphs with no character named: the homepage's corpus half.

    Corpus reads the cached sample it keeps for exactly this; nothing here scans. A fault raises like
    every other read, so a homepage can show its own rows and say the sample is missing rather than
    pretending the sample is empty.
    """
    payload = _get("/api/corpus/glyphs", limit=limit, seed=seed)
    return {"total": payload.get("total"), "items": [_unit_row(row) for row in payload.get("items", [])]}


def counts(chars: list[str]) -> dict[str, dict[str, Any]]:
    """Per-character counts keyed by the character, from one batched lookup."""
    wanted = [char for char in dict.fromkeys(chars) if char]
    if not wanted:
        return {}
    payload = _get("/api/corpus/counts", chars=",".join(wanted))
    if not payload:
        return {}
    rows = {row["char"]: row for row in payload.get("chars", []) if row.get("char")}
    return rows


def summary(row: dict[str, Any] | None) -> dict[str, Any]:
    """One counts row with the evidence kinds by name.

    `glyphs` is `n_glyphs` — isolated glyph rectangles, the only thing a grid may draw. `n_located`
    counts text occurrences with any real rectangle, a line rectangle included, and `n_units` is a
    part of `n_glyphs`; neither is used as a glyph count here. A count the index does not state stays
    `None`.
    """
    if not row:
        return {"known": False, "glyphs": None, "glyph_rects": None, "units": None, "lines": None,
                "pages": None, "located": None, "literal": None, "annotated": None,
                "renderable": None, "kind": None, "corpora": [], "documents": []}
    return {
        "known": bool(row.get("known", True)),
        "glyphs": _int_or_none(row.get("n_glyphs")),
        "family_glyphs": _int_or_none(row.get("n_family_glyphs")),
        "requires_family_scope": bool(row.get("requires_family_scope")),
        "counts_kind": row.get("counts_kind"),
        "glyph_rects": _int_or_none(row.get("n_glyph_rects")),
        "units": _int_or_none(row.get("n_units")),
        "lines": _int_or_none(row.get("n_line_hits")),
        "pages": _int_or_none(row.get("n_page_hits")),
        "located": _int_or_none(row.get("n_located")),
        "literal": _int_or_none(row.get("n_literal")),
        "annotated": _int_or_none(row.get("n_annotated")),
        "renderable": row.get("renderable"),
        "kind": row.get("kind"),
        "corpora": row.get("corpora") or [],
        "documents": row.get("sample_documents") or [],
    }


def glyphs(char: str, limit: int = 24, offset: int = 0, *, scope: str = "character", visual_group: str | None = None) -> dict[str, Any] | None:
    """Isolated glyph rectangles: the only source a grid may draw, anchors pinned first."""
    payload = _get("/api/corpus/glyphs", char=char, limit=limit, offset=offset or None,
                   scope=scope, visual_group=visual_group)
    if payload is None:
        return None
    return {"total": payload.get("total"), "total_is_exact": payload.get("total_is_exact"),
            "grid_safe": payload.get("grid_safe"),
            **{key: payload.get(key) for key in ("scope", "grapheme", "family_members", "family_total", "assigned_count", "unassigned_count", "visual_analysis", "visual_groups")},
            "items": [_unit_row(row) for row in payload.get("items", [])]}


def candidates(char: str, limit: int = 24, offset: int = 0, *, scope: str = "character", visual_group: str | None = None) -> dict[str, Any] | None:
    """Located glyph rectangles for one character: the only thing a gallery asks for by default.

    Deliberately **not** the text occurrences. Reading those walks every occurrence of the character
    in every indexed transcription, which is seconds of work for a single character and must never
    happen behind a keystroke or a tile. Text matches are a separate, explicit request:
    `source_hits`.
    """
    return glyphs(char, limit=limit, offset=offset, scope=scope, visual_group=visual_group)


def source_hits(char: str, limit: int = 24, offset: int = 0) -> dict[str, Any] | None:
    """Text matches for one character, on request and bounded by `limit`.

    The counts a view shows come from the cached character summary, not from this call, so this is
    only read when a reader asks to see the matches themselves.
    """
    found = _get("/api/corpus/find", char=char, limit=limit, offset=offset or None)
    if found is None:
        return None
    rows = [_occurrence_row(item) for item in found.get("items", [])]
    return {"total": found.get("total"), "total_kind": found.get("total_kind"),
            "items": [row for row in rows if not row["located"]],
            "located": [row for row in rows if row["located"]]}


def _occurrence_row(item: dict[str, Any]) -> dict[str, Any]:
    """An occurrence from `/api/corpus/find`, flattened to the few keys a view needs.

    `role` is what the rectangle actually is, taken from the corpus's own descriptor: a `glyph`
    rectangle is around the character and may be drawn as a crop, a `line` rectangle is around the
    line and may not. `located` is true only for the first.
    """
    thumb = item.get("thumbnail") if isinstance(item.get("thumbnail"), dict) else {}
    source = item.get("source") if isinstance(item.get("source"), dict) else {}
    rects = item.get("rects") or []
    role = thumb.get("role") or next((r.get("role") for r in rects), None)
    rect = thumb.get("rect") or next((r for r in rects if r.get("role") == role), None)
    fallback = thumb.get("fallback") if isinstance(thumb.get("fallback"), dict) else {}
    review = item.get("review") if isinstance(item.get("review"), dict) else {}
    return {
        "id": item.get("occurrence_id") or item.get("identity_key") or item.get("id"),
        "identity_key": item.get("identity_key"),
        "char": item.get("char"),
        "code_point": item.get("codepoint") or item.get("code_point"),
        "role": role,
        "tier": item.get("tier"),
        "located": bool(role == "glyph" and (thumb.get("available") or rect)),
        "text_raw": item.get("text_raw") or item.get("text"),
        "context": item.get("context"),
        "rect": rect,
        "image": thumb.get("iiif_url") or fallback.get("region_url"),
        "licence": thumb.get("licence"),
        "requires_review": bool(thumb.get("requires_review")),
        "reason": thumb.get("reason"),
        "source": {"corpus": source.get("corpus"), "document_id": source.get("document_id"),
                   "page_id": source.get("page_id"), "line_id": source.get("line_id"),
                   "title": source.get("title"), "holder": source.get("holder") or fallback.get("holder"),
                   "shelfmark": source.get("shelfmark"), "canvas": source.get("canvas") or fallback.get("canvas")},
        # The same facts at the top level, so a tile reads one shape whether it came from the glyph
        # reading or the occurrence reading.
        "title": source.get("title"), "holder": source.get("holder") or fallback.get("holder"),
        "shelfmark": source.get("shelfmark"),
        "canvas": source.get("canvas") or fallback.get("canvas"),
        "review": review.get("state") or item.get("review_state"),
        "human_validated": bool(review.get("human_validated")),
    }


def _is_glyph(item: dict[str, Any]) -> bool:
    """Whether an occurrence's own descriptor says its rectangle is around the character."""
    thumbnail = item.get("thumbnail") or {}
    if thumbnail.get("role"):
        return thumbnail["role"] == "glyph"
    return any(rect.get("role") == "glyph" for rect in (item.get("rects") or []))


def _unit_row(row: dict[str, Any]) -> dict[str, Any]:
    """A located glyph row from `/api/corpus/glyphs`, flattened to what a tile needs.

    `thumbnail` carries the image facts and is read as the authority on whether there is a picture:
    `available` false means there is no usable URL or local crop, and `proxyable` false means the
    licence allows a link to the holder and not a copy of the crop. `box` is the rectangle in source
    pixels. Nothing here is treated as human-confirmed: the corpus states `review: machine` and
    `requires_review: true` until a person has looked.
    """
    from ..corpus.identity import IDENTITY_FIELDS
    thumb = row.get("thumbnail") if isinstance(row.get("thumbnail"), dict) else {}
    source = row.get("source") if isinstance(row.get("source"), dict) else {}
    box = row.get("box") or row.get("rect") or thumb.get("rect") or {}
    available = bool(thumb.get("available"))
    item = {
        "id": row.get("identity_key") or row.get("id") or row.get("unit_id"),
        "identity_key": row.get("identity_key"),
        "unit_id": row.get("unit_id"),
        "char": row.get("char"),
        "code_point": row.get("codepoint") or row.get("code_point"),
        "role": thumb.get("role") or "glyph",
        "source_kind": row.get("source_kind"),
        "method": row.get("method"),
        "score": row.get("heuristic_score"),
        "located": True,
        "render_available": bool(row.get("render_available")) and available,
        "grid_safe": bool(row.get("grid_safe", True)),
        "proxyable": bool(thumb.get("proxyable")),
        "requires_review": bool(thumb.get("requires_review")),
        "box": box or None,
        "image": (thumb.get("crop_url") or thumb.get("iiif_url")) if available else None,
        "crop": thumb.get("crop") if available else None,
        "region": thumb.get("region"),
        "licence": thumb.get("licence"),
        "licence_note": thumb.get("not_proxyable_reason"),
        "source": source,
        "holder": row.get("holder") or source.get("holder"),
        "title": row.get("title") or source.get("title"),
        "shelfmark": row.get("shelfmark"),
        "text_raw": row.get("text") or row.get("reading"),
        "corpus": row.get("corpus"),
        "document_id": row.get("document_id"),
        "page_id": row.get("page_id"),
        "line_id": row.get("line_id"),
        "review": row.get("review") or "machine",
        "human_validated": bool(row.get("human_validated")),
        **{key: row[key] for key in IDENTITY_FIELDS if key in row},
    }
    item["label"] = item["char"]
    item.setdefault("source_label", item["char"])
    item["state"] = "pending"
    reviews = getattr(_api, "_atlas_reviews", None)
    return reviews.overlay_rows([item])[0] if reviews else item


def _int_or_none(value: Any) -> int | None:
    return None if value is None else int(value)
