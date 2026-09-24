"""The gallery/search HTTP surface for corpus occurrences.

Framework-free by default: :class:`Router` maps ``(method, path)`` to a handler and
returns ``(status, content_type, body)``, so it can be driven from a test, a CLI or any
server. :mod:`glyph_atlas.corpus.fastapi_router` wraps the same handlers in a
FastAPI ``APIRouter`` for one-line mounting.

Properties the UI depends on:

* **Nothing loads the whole corpus.** A query reads the bounded character summary and
  at most one per-character occurrence file. A missing per-character file is *built*,
  not reported as empty.
* **A rectangle is labelled with what it actually is.** A thumbnail descriptor reports
  ``role`` from the rectangle it selected, never from what was asked for, and carries
  ``has_glyph`` / ``has_line`` so a glyph grid can refuse line rectangles.
* **A text hit is not a crop.** ``Occurrence.tier`` says what is known, ``has_crop`` is
  true only for a real re-fetchable rectangle, and a page-level hit returns no rect.
* **Exact and estimated numbers are labelled.** ``n_occurrences`` is exact;
  ``n_pages_approx`` is a sketch, and says so in its name.
* **Review survives re-import**: keyed by ``occurrence_id``, a hash of source identity.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from . import crops as crops_module
from . import index as index_module
from . import sources as corpus_sources
from .occurrence import (
    Occurrence,
    Rect,
    codepoint,
    normalise_occurrence_id,
)

#: Licences whose images may be proxied through this service. Anything else is
#: returned as a link to the holder's own viewer, so the atlas never re-serves an
#: image it has no right to serve. ``None`` (unresolved) is NOT proxyable.
#: Canonical values from :class:`glyph_atlas.schema.Licence` — hyphenated, not
#: spaced, so they match what the importers actually store. ND and NC are excluded:
#: an excerpt crop is not obviously permitted by either.
PROXYABLE = frozenset(
    {
        "CC0-1.0",
        "PD",
        "PDM-1.0",
        "CC-BY-4.0",
        "CC-BY-SA-3.0",
        "CC-BY-SA-4.0",
        "CC-BY-SA-2.1-JP",
        "Unicode-3.0",
        "bespoke-free",
    }
)

#: Licences we can name but must not redistribute crops for, with the reason the UI
#: shows instead of an image.
NOT_PROXYABLE_REASON = {
    "CC-BY-NC-4.0": "non-commercial only",
    "CC-BY-ND-4.0": "no derivatives",
    "CC-BY-NC-SA-4.0": "non-commercial only",
    "CC-BY-NC-ND-4.0": "non-commercial and no derivatives",
    "RS-NOC-CR": "rights reserved, no commercial redistribution",
    "restricted": "restricted by the holder",
    "unknown": "licence not resolved",
}

DEFAULT_LIMIT = 60
MAX_LIMIT = 500

#: The stable shape of one homepage sample item, published with the response so the
#: layer worker can render it without guessing.
SAMPLE_ITEM_SCHEMA = {
    "id": "unit id; pass to /api/corpus/crop",
    "char": "the character, or null when the unit has no text_source",
    "codepoint": "'U+XXXX', or null",
    "corpus": "source corpus id",
    "title": "source document title, or null",
    "holder": "holding institution, or null",
    "box": "{x,y,w,h} in source-image pixels, or null for a pre-cut crop",
    "render_available": "always true in this response",
    "render_capability": "local_crop | remote_iiif",
    "image_licence": "canonical licence id, or null",
    "thumbnail": {
        "available": "always true here",
        "role": "always 'glyph'",
        "mode": "local_crop | remote_iiif",
        "region": "'x,y,w,h' in source pixels, or null",
        "crop_url": "served by this API; present for local_crop",
        "iiif_url": "holder-hosted region URL; present for remote_iiif",
        "proxyable": "whether this atlas may serve the bytes",
        "licence": "canonical licence id",
    },
}

#: Ceiling on a batched count request, so one call cannot ask for the world.
MAX_COUNT_CHARS = 200

#: Rect bases that describe an actual measurement of an image rectangle.
LOCATED_BASES = ("upstream_bbox", "iiif_region", "machine_projection")


@dataclass
class Request:
    method: str
    path: str
    query: dict[str, list[str]] = field(default_factory=dict)

    def one(self, name: str, default: str | None = None) -> str | None:
        values = self.query.get(name)
        return values[0] if values else default

    def many(self, name: str) -> list[str]:
        return self.query.get(name, [])

    def int(self, name: str, default: int, *, low: int, high: int) -> int:
        raw = self.one(name)
        if raw is None:
            return default
        try:
            return max(low, min(high, int(raw)))
        except ValueError:
            return default


class CorpusAPI:
    """Handlers over a :class:`~glyph_atlas.corpus.index.CorpusIndex`."""

    def __init__(
        self,
        root: str | Path = corpus_sources.DEFAULT_ROOT,
        directory: str | Path = index_module.INDEX_DIR,
        review: Any = None,
        *,
        autobuild: bool = True,
        file_bases: Sequence[str | Path] | None = None,
    ):
        self.root = Path(root)
        self.directory = Path(directory)
        # The root must travel with the index: an on-demand build through the index
        # would otherwise fall back to the default root and read the wrong corpora.
        self.index = index_module.CorpusIndex(directory, self.root)
        self.review = review
        self.autobuild = autobuild
        # Crop bytes are resolved through an allow list, never from a caller's path.
        self.crops = crops_module.CropResolver(self.root, file_bases=file_bases or (self.root, Path.cwd()))

    def handle_get(self, target: str) -> tuple[int, str, bytes]:
        """Answer one GET offline — the same path the server mounts."""
        return Router(self).handle("GET", target)

    # ------------------------------------------------------------- endpoints
    def sources(self, request: Request) -> tuple[int, str, bytes]:
        found = corpus_sources.discover(self.root)
        return _json(
            200,
            {
                "index": _public_stats(self.index.stats()),
                "sources": [_public_source(c) for c in found],
                "proxyable_licences": sorted(PROXYABLE),
                "notes": [
                    "n_occurrences is exact; n_pages_approx is a sketch (~3% error).",
                    "tier=line_rect means a real rectangle exists; page_text does not.",
                    "confirmed is true only after a human review event.",
                ],
            },
        )

    def characters(self, request: Request) -> tuple[int, str, bytes]:
        if not self.index.exists():
            return self._not_built()
        limit = request.int("limit", DEFAULT_LIMIT, low=1, high=MAX_LIMIT)
        needle = (request.one("q") or "").strip()
        rows = self.index.characters()
        if needle:
            rows = [
                r
                for r in rows
                if needle == r["char"]
                or needle.upper() == (r["codepoint"] or "").upper()
                or needle in (r["name"] or "").upper()
                or needle in r["char"]
            ]
        rows = rows[:limit]
        for r in rows:
            r["sample_documents"] = json.loads(r.get("sample_documents") or "[]")
            # n_occurrences counts character occurrences in text, exact; it is not a
            # count of distinct manuscript glyphs, which only located units can give.
            r["counts_kind"] = "text_occurrences"
            r["counts_are_exact"] = True
            r["n_pages_is_estimate"] = True
            r["distinct_glyphs_known"] = False
        return _json(200, {"query": needle, "returned": len(rows), "characters": rows})

    def find(self, request: Request) -> tuple[int, str, bytes]:
        """Occurrences of one character in the source texts.

        Params: ``char`` (a character or ``U+XXXX``), ``corpus`` (repeatable),
        ``tier`` (``page_text|line_text|line_rect``), ``class``, ``located=1``,
        ``limit``, ``offset``, ``thumb=0``.

        **This never builds anything.** Building a per-character file means scanning
        every corpus, and a common character occurs over a million times; doing that
        inside a request is what exhausted the server. A character that has not been
        indexed answers with ``state="not_indexed"``, ``total=null`` and its exact
        known count, and indexing it is an explicit bounded off-line step
        (``python -m glyph_atlas.corpus build-char <char>``).
        """
        if not self.index.exists():
            return self._not_built()
        raw = (request.one("char") or request.one("q") or "").strip()
        char = _resolve(raw)
        if char is None:
            return _json(400, {"error": "char must be one character or a code point", "given": raw})
        summary = self.index.summary(char)
        if summary is None:
            # Not in the summary: either genuinely absent, or the summary predates a
            # later import. Say which rather than implying "there are none".
            return _json(
                200,
                {
                    "query": raw,
                    "char": char,
                    "codepoint": codepoint(char),
                    "total": 0,
                    "total_is_exact": True,
                    "returned": 0,
                    "items": [],
                    "summary": None,
                    "state": "not_present",
                    "reason": "not present in the character summary; rebuild with "
                    "`corpus build` if a corpus was imported since",
                },
            )
        if not self.index.has_occurrences(char):
            # A cold character is NOT built here. A common character occurs over a
            # million times; building it inside a request materialises megabytes and
            # blocks every other request behind it. The exact count is already known,
            # so answer with that and say plainly that the detailed rows are not
            # indexed yet. Building is an explicit, bounded, off-line step.
            located = 0
            return _json(
                200,
                {
                    "query": raw,
                    "char": char,
                    "codepoint": codepoint(char),
                    "state": "not_indexed",
                    # NOT zero. Zero is a claim that there are none, and a downstream view
                    # renders that as an empty result. This character has no indexed rows
                    # *yet*; the exact total is unknown from here, so it is null.
                    "total": None,
                    "total_is_exact": False,
                    "available": False,
                    "returned": 0,
                    "items": [],
                    "summary": summary,
                    "known_occurrences": summary.get("n_occurrences") or 0,
                    "known_occurrences_are_exact": True,
                    "known_located": summary.get("n_located") or 0,
                    "known_glyphs": (summary.get("n_units") or 0) + located,
                    "build_hint": {
                        "command": "python -m glyph_atlas.corpus build-char "
                        f"{summary.get('codepoint') or codepoint(char)}",
                        "bounded": True,
                        "max_records": index_module.DEFAULT_MAX_RECORDS,
                        "note": "bounded and off-line; never triggered by a request",
                    },
                    "reason": "detailed rows for this character are not indexed yet; the "
                    "counts above are exact",
                },
            )
        occurrences = self.index.occurrences(char)
        built_here = False
        total_before = len(occurrences)
        occurrences = _filter(occurrences, request)
        total = len(occurrences)
        offset = request.int("offset", 0, low=0, high=10_000_000)
        limit = request.int("limit", DEFAULT_LIMIT, low=1, high=MAX_LIMIT)
        # Drawable results first. Two inspected glyphs must not sit behind 700 text
        # hits whose page number happens to sort earlier.
        occurrences = sorted(occurrences, key=_result_order)
        page = occurrences[offset : offset + limit]
        thumb = request.one("thumb", "1") != "0"
        return _json(
            200,
            {
                "query": raw,
                "char": char,
                "codepoint": codepoint(char),
                # `total` counts surviving records, not confirmed distinct glyphs. Two
                # imports of one page may both describe one glyph and are deliberately NOT
                # merged unless identity is proven; `soft_groups` is how many glyphs they
                # probably describe, and it is labelled as probable.
                "total": total,
                "total_kind": "raw_hit_count",
                "total_is_exact": True,
                "unique_source_identities": len({o.identity_key for o in occurrences}),
                "unique_source_identities_are_exact": True,
                "probable_glyph_groups": len({o.soft_group_key for o in occurrences}),
                "probable_glyph_groups_are_estimate": True,
                "merges_are_proven_only": True,
                "total_before_filter": total_before,
                "offset": offset,
                "returned": len(page),
                "state": "ok",
                "available": True,
                "built_on_demand": built_here,
                "summary": self.index.summary(char),
                "items": [self._item(o, thumb=thumb) for o in page],
            },
        )

    def counts(self, request: Request) -> tuple[int, str, bytes]:
        """Batched per-character counts for autocomplete — never a corpus scan, never a build.

        Params: ``chars`` (comma-separated list, up to ``MAX_COUNT_CHARS``) and/or
        ``q`` (a substring matched against character, name and code point). Reads only
        the cached character summary, so a suggestion list costs dictionary lookups.

        The counts separate things a ligature list must not conflate:

        ``n_literal``    the character itself occurs in the text (a real ligature cell)
        ``n_annotated``  the character occurs inside a 〖X：合字〗 transcription marker
        ``n_located``    occurrences that carry a real image rectangle
        ``n_line_hits`` / ``n_page_hits``  line-level vs page-level text matches
        ``n_units``      located character units in the imported char datasets

        A ト followed by a モ is two characters and is counted under neither ``ト``'s
        nor ``モ``'s ligature total — it is not this character at all.
        """
        if not self.index.exists():
            return self._not_built()
        raw = request.one("chars") or ""
        wanted = [c for c in (raw.split(",") if raw else []) if c]
        if len(wanted) > MAX_COUNT_CHARS:
            return _json(400, {"error": f"at most {MAX_COUNT_CHARS} chars per request", "given": len(wanted)})
        needle = (request.one("q") or "").strip()
        limit = request.int("limit", DEFAULT_LIMIT, low=1, high=MAX_LIMIT)
        rows = []
        if wanted:
            for char in wanted:
                summary = self.index.summary(char) or self.index.summary_by_codepoint(char)
                rows.append(self._count_row(char, summary))
        if needle or not wanted:
            candidates = self.index.characters()
            if needle:
                upper = needle.upper()
                candidates = [
                    r
                    for r in candidates
                    if needle in r["char"]
                    or upper in (r["codepoint"] or "").upper()
                    or needle.upper() in (r["name"] or "").upper()
                ]
            rows.extend(self._count_row(r["char"], r) for r in candidates[:limit])
        reviews = getattr(self, "_atlas_reviews", None)
        if reviews:
            rows = reviews.adjust_counts(rows)
        return _json(
            200,
            {
                "query": needle or raw,
                "returned": len(rows),
                "counts_are_exact": True,
                "chars": rows,
                "count_semantics": {
                    "n_glyphs": "renderable tiles = n_glyph_rects + n_units",
                    "n_glyph_rects": "isolated glyph rectangles on text "
                    "occurrences (machine-located anchors)",
                    "n_units": "imported detector/alignment unit rows",
                    "n_line_hits": "line-level text matches, no glyph position",
                    "n_page_hits": "page-level text matches, no rectangle",
                    "n_located": "text occurrences with any real rectangle",
                },
                "note": "n_literal counts the character itself; a separate "
                "ト+モ sequence is not counted here",
            },
        )

    def glyphs(self, request: Request) -> tuple[int, str, bytes]:
        """Located character *units* — the only thing a glyph grid may show.

        Reads the imported char datasets (CODH, HI Lab, Kokatsuji) and the located
        Honkoku units, whose rows are real boxes on real pages. Text occurrences never
        appear here: a ``page_text`` match has no rectangle and must not be drawn as a
        tile.
        """
        if not self.index.exists():
            return self._not_built()
        raw = (request.one("char") or request.one("q") or "").strip()
        limit = request.int("limit", DEFAULT_LIMIT, low=1, high=MAX_LIMIT)
        offset = request.int("offset", 0, low=0, high=10_000_000)
        corpora = request.many("corpus") or None
        width = request.int("w", 240, low=32, high=2000)
        if not raw:
            # No character: a bounded, cached sample of located units. A homepage must
            # not scan the corpus, so this reads one small JSON file.
            rows, meta = index_module.sample_units(self.root, self.directory, corpora=corpora, rebuild=False)
            # Decorating decides what is actually renderable. A homepage must not
            # carry a tile that renders blank, so anything without a real render path
            # is dropped here even when a stale cache still lists it.
            decorated: list[dict[str, Any]] = []
            for row in rows:
                self._decorate_unit(row, width=width)
                if row.get("render_available"):
                    decorated.append(row)
            import random

            random.Random(request.int("seed", 0, low=0, high=2**53)).shuffle(decorated)
            page = decorated[offset : offset + limit]
            return _json(
                200,
                {
                    "query": None,
                    "char": None,
                    "codepoint": None,
                    "total": len(decorated),
                    "total_is_exact": False,
                    "total_is_sample": True,
                    "returned": len(page),
                    "offset": offset,
                    "items": page,
                    "sample": meta,
                    "grid_safe": True,
                    "render_available_all": True,
                    "schema": SAMPLE_ITEM_SCHEMA,
                    "note": "bounded sample of renderable imported units; not the "
                    "full corpus, and never a page or line rectangle",
                },
            )
        char = _resolve(raw)
        if char is None:
            return _json(400, {"error": "char must be one character or a code point", "given": raw})
        reviews = getattr(self, "_atlas_reviews", None)
        labels = reviews.identity_assignments() if reviews else {}
        from .identity import family_of, identity_fields
        scope = request.one("scope", "character")
        if scope not in ("character", "grapheme"):
            return _json(400, {"error": "scope must be character or grapheme"})
        visual_group = request.one("visual_group")
        family = family_of(codepoint(char))
        members = family["members"] if family else [{"char": char, "code_point": codepoint(char)}]
        family_chars = {member["char"] for member in members}
        identity_counts = {"assigned_count": 0, "unassigned_count": 0, "family_total": 0}
        anchors = []
        for entry in self.index.glyphs.entries.values():
            if labels.get(entry.identity_key, entry.char) not in family_chars:
                continue
            if corpora and (entry.source or {}).get("corpus") not in corpora:
                continue
            anchor = self._anchor_item(entry, width=width)
            identity = identity_fields(anchor, (entry.source or {}).get("corpus"),
                                       human_character=labels.get(entry.identity_key))
            written = identity["written_character"]
            identity_counts["family_total"] += 1
            identity_counts["assigned_count" if written else "unassigned_count"] += 1
            if visual_group == "unassigned" and written is not None:
                continue
            if visual_group and visual_group != "unassigned" and (identity.get("visual_group") or {}).get("id") != visual_group:
                continue
            if scope == "character" and written != char:
                continue
            anchor.update(identity)
            anchor["char"] = written or identity["source_label"]
            anchor["codepoint"] = codepoint(anchor["char"])
            anchors.append(anchor)
        # Anchors are pinned to the front, so the whole result is one ordered
        # sequence: the anchors, then the units in scan order. The window is taken
        # from that sequence, and the unit scan is asked only for the part of the
        # window that falls past the anchors.
        anchor_total = len(anchors)
        page_anchors = anchors[offset : offset + limit]
        unit_offset = max(0, offset - anchor_total)
        unit_limit = max(0, limit - len(page_anchors))
        units, unit_total = index_module.located_units(
            char, self.root, corpora=corpora, offset=unit_offset, limit=unit_limit, labels=labels,
            scope=scope, identity_counts=identity_counts, visual_group=visual_group,
        )
        rows = page_anchors + units
        total = anchor_total + unit_total
        for row in rows:
            if row.get("thumbnail") is None:
                self._decorate_unit(row, width=width)
        return _json(
            200,
            {
                "query": raw,
                "char": char,
                "codepoint": codepoint(char),
                "total": total,
                "total_is_exact": True,
                "returned": len(rows),
                "offset": offset,
                "items": rows,
                "scope": scope,
                "grapheme": family["code_point"] if family else codepoint(char),
                "family_members": members,
                **identity_counts,
                **_visual_analysis(codepoint(char)),
                "n_glyph_rects": len(anchors),
                "n_units": unit_total,
                "anchors_pinned_first": True,
                "grid_safe": True,
                "note": "every item carries an isolated glyph rectangle: a machine-located "
                "anchor or an imported detector/alignment unit. A line rectangle is "
                "never returned here.",
            },
        )

    def occurrence(self, request: Request, occurrence_id: str) -> tuple[int, str, bytes]:
        if not self.index.exists():
            return self._not_built()
        occurrence = self.index.occurrence(occurrence_id)
        if occurrence is None:
            return _json(404, {"error": "unknown occurrence", "occurrence_id": occurrence_id})
        return _json(200, self._item(occurrence, thumb=True))

    def thumb(self, request: Request) -> tuple[int, str, bytes]:
        """A descriptor for the rectangle to draw — never a re-hosted image.

        The caller (or the UI directly) fetches from the holder's IIIF service, which
        keeps the atlas out of redistributing images it has no right to serve.
        """
        occurrence_id = normalise_occurrence_id(request.one("occurrence_id") or "")
        wanted = request.one("role", "glyph")
        width = request.int("w", 320, low=32, high=2000)
        occurrence = self.index.occurrence(occurrence_id)
        if occurrence is None:
            return _json(404, {"error": "unknown occurrence", "occurrence_id": occurrence_id})
        return _json(200, self._thumb_descriptor(occurrence, wanted=wanted, width=width))

    def index_status(self, request: Request) -> tuple[int, str, bytes]:
        """Whether a per-character file exists, without building anything."""
        raw = (request.one("char") or "").strip()
        char = _resolve(raw) if raw else None
        if char is None:
            return _json(400, {"error": "char must be one character or a code point", "given": raw})
        summary = self.index.summary(char)
        path = self.index.occ_path(char)
        indexed = path.exists()
        return _json(
            200,
            {
                "char": char,
                "codepoint": codepoint(char),
                "indexed": indexed,
                "indexed_bytes": path.stat().st_size if indexed else 0,
                "indexed_is_truncated": bool(
                    summary and indexed and _count_rows(path) >= index_module.DEFAULT_MAX_RECORDS
                ),
                "known_occurrences": (summary or {}).get("n_occurrences") or 0,
                "known_occurrences_are_exact": True,
                "max_records_per_character": index_module.DEFAULT_MAX_RECORDS,
                "state": "ready" if indexed else ("known_but_not_indexed" if summary else "not_present"),
            },
        )

    def crop(self, request: Request) -> tuple[int, str, bytes]:
        """Serve the bytes of one registered glyph crop.

        The caller names a unit id; the path comes from the corpus row and is checked
        against the allow list. Nothing here accepts a path, so this cannot be turned
        into an arbitrary file read.
        """
        unit_id = (request.one("unit_id") or "").strip()
        if not unit_id:
            return _json(400, {"error": "unit_id is required", "hint": "ids come from /api/corpus/glyphs"})
        edge = request.int("w", crops_module.DEFAULT_CROP_EDGE, low=32, high=crops_module.MAX_CROP_EDGE)
        result = self.crops.for_unit(unit_id, edge=edge)
        if result.bytes_data:
            return 200, result.media_type or "application/octet-stream", result.bytes_data
        status = 404 if result.mode in ("none", "archive_member", "record_page") else 403
        return _json(status, result.as_dict())

    def stats(self, request: Request) -> tuple[int, str, bytes]:
        return _json(200, _public_stats(self.index.stats()))

    # -------------------------------------------------------------- helpers
    def _count_row(self, char: str, summary: dict[str, Any] | None) -> dict[str, Any]:
        """A count row with this character's isolated glyph rectangles folded in."""
        from .identity import NORMALIZED_CORPORA, family_of
        result = _count_row(char, summary, len(self.index.glyphs.for_char(char)))
        family = family_of(codepoint(char)) if len(char) == 1 else None
        ambiguous = bool(family and family["character_count"] > 1 and any(
            set(_count_row(member["char"], self.index.summary(member["char"]), 0).get("corpora") or []) & NORMALIZED_CORPORA
            for member in family["members"]
        ))
        result["requires_family_scope"] = ambiguous
        result["n_family_glyphs"] = sum(
            _count_row(member["char"], self.index.summary(member["char"]), len(self.index.glyphs.for_char(member["char"]))).get("n_glyphs") or 0
            for member in family["members"]
        ) if family else result.get("n_glyphs")
        result["counts_kind"] = "source_transcription_classes"
        return result

    def _not_built(self) -> tuple[int, str, bytes]:
        return _json(
            503,
            {
                "error": "corpus index not built",
                "hint": "python -m glyph_atlas.corpus build",
                "index_name": self.directory.name,
            },
        )

    def _decorate_unit(self, row: dict[str, Any], *, width: int) -> None:
        """Attach the thumbnail and the honest render path to a unit row.

        The cached sample already contains the unit and its source image. Check that
        row's local bytes or IIIF service directly; resolving an unrelated probe unit
        would scan the source corpus on a cold homepage request.
        """
        from .identity import identity_fields, production_fields
        if "written_character" not in row:
            row.update(identity_fields(row, row.get("corpus")))
        row["thumbnail"] = _unit_thumbnail(row, width=width)
        corpus = self._corpus(row.get("corpus"))
        if corpus is None:
            row["render_available"] = False
            row["thumbnail"]["available"] = False
            row["thumbnail"]["reason"] = "unknown corpus"
            return
        self.crops.capability(corpus)
        if "production" not in row:
            if not hasattr(self, "_production_contexts"):
                self._production_contexts = {}
            context = self._production_contexts.setdefault(corpus.name, index_module._MetaCache(corpus))
            row.update(production_fields(context.document(row.get("document_id") or "")))
        available, reason, mode = self.crops.row_availability(row, corpus.name)
        unit_id = row.get("unit_id")
        row["render_available"] = bool(available and unit_id)
        row["render_capability"] = mode
        row["thumbnail"]["available"] = bool(available and unit_id)
        row["thumbnail"]["mode"] = mode
        if available and unit_id:
            if mode == "local_crop":
                row["thumbnail"]["crop_url"] = self.crops.serve_url(unit_id, width)
            elif mode == "remote_iiif":
                row["thumbnail"]["iiif_url"] = row["thumbnail"].get("iiif_url") or _unit_iiif_url(row, width)
            media = getattr(self, "media", None)
            if media is not None:
                hosted = media.corpus_image(row, self.crops, edge=width)
                if hosted:
                    row["thumbnail"]["crop_url"] = hosted
        else:
            row["thumbnail"]["reason"] = reason

    def _corpus(self, name: str | None):
        if name is None:
            return None
        import time

        now = time.monotonic()
        if now < getattr(self, "_corpus_check_after", 0):
            return self._corpus_cache.get(name)
        generation = tuple(str((self.root / source / "current").resolve())
                           for source in ("honkoku-collection", "wikisource-collection"))
        if not hasattr(self, "_corpus_cache") or getattr(self, "_collection_generation", None) != generation:
            self._corpus_cache = {c.name: c for c in corpus_sources.discover(self.root)}
            self._collection_generation = generation
        # Resolving an external-drive symlink once per displayed crop can dominate a
        # gallery request. Published generations are immutable; a short refresh interval
        # keeps the request bounded while admitting newly collected works.
        self._corpus_check_after = now + 2
        return self._corpus_cache.get(name)

    def _anchor_item(self, entry: Any, *, width: int) -> dict[str, Any]:
        """A glyph-grid item built from a machine-located glyph rectangle."""
        rect = entry.as_rect()
        licence = (entry.source.get("image_rights") or {}).get("licence")
        region = rect.iiif_region()
        # Build the request at the width the caller asked for, from the service base,
        # rather than replaying a URL baked at registry-build time.
        iiif_url = (
            f"{entry.image_service}/{region}/{width},/0/default.jpg"
            if entry.image_service
            else entry.iiif_url
        )
        has_local_crop = bool(entry.crop_file and Path(entry.crop_file).exists())
        usable = bool(iiif_url or has_local_crop)
        item = {
            # `id` is what the layers unit shape keys on; `identity_key` is the strict
            # source identity and is kept alongside it.
            "id": entry.identity_key,
            "unit_id": None,
            "char": entry.char,
            "codepoint": entry.codepoint,
            "corpus": entry.source.get("corpus"),
            "document_id": entry.source.get("document_id"),
            "page_id": entry.source.get("page_id"),
            "line_id": entry.source.get("line_id"),
            "title": entry.source.get("title"),
            "holder": entry.source.get("holder"),
            "shelfmark": entry.source.get("shelfmark"),
            "box": {"x": entry.x, "y": entry.y, "w": entry.w, "h": entry.h},
            "kind": "glyph-rect",
            "method": entry.method,
            "review": "machine",
            "source_kind": "machine_located_anchor",
            "identity_key": entry.identity_key,
            "occurrence_id": None,
            "render_available": usable,
            "heuristic_score": entry.heuristic_score,
            "inspection_note": entry.inspection_note,
            "crop_file": entry.crop_file,
            "crop_sha256": entry.crop_sha256,
            "image_service": entry.image_service,
            "image_licence": licence,
            "image_rights": entry.source.get("image_rights"),
            "grid_safe": True,
            "note": "machine-located glyph rectangle on a text occurrence; not human-verified",
        }
        item["thumbnail"] = {
            # available means there is something to render, not merely that a box
            # exists. A descriptor with no URL and no local crop is not available.
            "available": usable,
            "role": "glyph",
            "has_glyph": True,
            "licence": licence,
            "proxyable": licence in PROXYABLE,
            "not_proxyable_reason": None
            if licence in PROXYABLE
            else NOT_PROXYABLE_REASON.get(licence or "", "licence not resolved"),
            "region": region,
            "width": width,
            "iiif_url": iiif_url,
            "crop": entry.crop_file if has_local_crop else None,
            "crop_sha256": entry.crop_sha256,
            "crop_bytes": entry.crop_bytes,
            "requires_review": not entry.confirmed,
        }
        if not usable:
            item["thumbnail"]["reason"] = (
                "no IIIF service and no local crop file; the rectangle is known but cannot be rendered"
            )
        return item

    def _item(self, occurrence: Occurrence, *, thumb: bool = True) -> dict[str, Any]:
        item = occurrence.as_dict()
        item["thumbnail"] = self._thumb_descriptor(occurrence, wanted="glyph", width=320) if thumb else None
        item["review"] = self._review_of(occurrence)
        return item

    def _review_of(self, occurrence: Occurrence) -> dict[str, Any]:
        events = list(occurrence.review_events)
        if self.review is not None:
            events.extend(self.review.events_for(occurrence.occurrence_id))
        return {
            "state": occurrence.review_state,
            "human_validated": any(e.get("actor_kind") == "human" for e in events),
            "events": events,
        }

    def _thumb_descriptor(self, occurrence: Occurrence, *, wanted: str, width: int) -> dict[str, Any]:
        rect = _pick_rect(occurrence, wanted)
        licence = _licence_of(occurrence)
        # `role` always reports the rectangle actually selected, never the request.
        descriptor: dict[str, Any] = {
            "occurrence_id": occurrence.occurrence_id,
            "requested_role": wanted,
            "role": rect.role if rect else None,
            "role_matched_request": bool(rect and rect.role == wanted),
            "has_glyph": occurrence.has_glyph_rect,
            "has_line": any(r.role == "line" for r in occurrence.rects),
            "available": rect is not None,
            "tier": occurrence.tier,
            "licence": licence,
            "proxyable": licence in PROXYABLE,
            "requires_review": bool(rect and not rect.confirmed),
            "not_proxyable_reason": None
            if licence in PROXYABLE
            else NOT_PROXYABLE_REASON.get(licence or "", "licence not resolved"),
        }
        if rect is None:
            descriptor["reason"] = f"no located rectangle; this is a {occurrence.tier} match, not a crop"
            descriptor["fallback"] = {
                "canvas": occurrence.source.canvas,
                "holder": occurrence.source.holder,
                "region_url": occurrence.source.iiif_region_url,
            }
            return descriptor
        descriptor["rect"] = rect.as_dict()
        descriptor["region"] = rect.iiif_region()
        service = _service_of(occurrence)
        if service:
            descriptor["iiif_url"] = f"{service}/{rect.iiif_region()}/{width},/0/default.jpg"
        descriptor["source_region_url"] = occurrence.source.iiif_region_url
        return descriptor


def _pick_rect(occurrence: Occurrence, wanted: str) -> Rect | None:
    """The rectangle to draw for `wanted`.

    A ``derived_char_index`` rect is arithmetic, not a measurement, and is refused
    here on purpose: a probe of an evenly divided line box landed on the neighbouring
    glyph, so it must never reach a glyph grid. If no glyph rectangle exists and a
    glyph was requested, the line is returned and the descriptor says ``role="line"``
    so the caller cannot mistake it for a glyph.
    """
    usable = [r for r in occurrence.rects if r.basis in LOCATED_BASES]
    glyphs = [r for r in usable if r.role == "glyph"]
    lines = [r for r in usable if r.role == "line"]
    if wanted == "glyph":
        if glyphs:
            return min(glyphs, key=lambda r: -(r.confidence or 0))
        return lines[0] if lines else None
    if wanted == "line":
        return lines[0] if lines else (glyphs[0] if glyphs else None)
    return (glyphs + lines + [None])[0]


def _unit_iiif_url(row: dict[str, Any], width: int) -> str | None:
    """A IIIF region URL for a unit, straight from its page service and box."""
    service = row.get("image_service")
    box = row.get("box")
    if not service or not box:
        return None
    region = f"{box['x']},{box['y']},{box['w']},{box['h']}"
    return f"{service}/{region}/{width},/0/default.jpg"


def _unit_thumbnail(row: dict[str, Any], *, width: int) -> dict[str, Any]:
    """A thumbnail descriptor for an imported unit row."""
    licence = row.get("image_licence")
    box = row.get("box")
    descriptor: dict[str, Any] = {
        "available": bool(box),
        "role": "glyph" if box else None,
        "has_glyph": bool(box),
        "licence": licence,
        "proxyable": licence in PROXYABLE,
        "crop": row.get("crop"),
        "crop_sha256": row.get("crop_sha256"),
    }
    if not box:
        # HI Lab publishes one pre-cut crop per character and no coordinates: the
        # crop is the renderable thing, so report it rather than an empty descriptor.
        if row.get("crop"):
            descriptor["role"] = "glyph"
            descriptor["available"] = True
            descriptor["reason"] = None
            descriptor["note"] = "pre-cut character crop; the holder publishes no box"
            return descriptor
        descriptor["reason"] = "unit row has neither a box nor a crop"
        return descriptor
    descriptor["region"] = f"{box['x']},{box['y']},{box['w']},{box['h']}"
    service = row.get("image_service")
    if service:
        descriptor["iiif_url"] = f"{service}/{descriptor['region']}/{width},/0/default.jpg"
    return descriptor


def _licence_of(occurrence: Occurrence) -> str | None:
    rights = occurrence.source.image_rights
    if isinstance(rights, dict):
        return rights.get("licence")
    return None


def _service_of(occurrence: Occurrence) -> str | None:
    try:
        from ..images import service_of
    except ImportError:  # pragma: no cover
        service_of = None
    for candidate in (occurrence.source.iiif_region_url, occurrence.source.image):
        if not candidate:
            continue
        if service_of is not None:
            base = service_of(candidate)
            if base:
                return base
        cut = re.sub(r"/\d+,\d+,\d+,\d+/[^/]*/\d+/[^/]*$", "", candidate)
        if cut != candidate:
            return cut
    return None


def _count_row(char: str, summary: dict[str, Any] | None, n_glyph_rects: int = 0) -> dict[str, Any]:
    """One autocomplete row.

    ``n_glyphs`` is the number a gallery may render as character tiles, and it is the
    only number that may be. It is ``n_glyph_rects`` (isolated glyph rectangles
    measured on text occurrences, including the inspected anchors) **plus** ``n_units``
    (imported detector/alignment units). The two are disjoint — a unit row is not a
    text occurrence — so nothing is counted twice.

    ``n_line_hits`` and ``n_page_hits`` are text evidence and are never renderable as
    glyphs: a line hit has no glyph position, a page hit has no rectangle at all.
    ``n_located`` counts text occurrences with any real rectangle (line or glyph);
    it is deliberately *not* the renderable count, which is why ``n_glyphs`` exists.
    """
    if summary is None:
        return {
            "char": char,
            "codepoint": codepoint(char),
            "known": False,
            "n_literal": 0,
            "n_annotated": 0,
            "n_located": 0,
            "n_units": 0,
            "n_glyph_rects": 0,
            "n_glyphs": 0,
            "n_line_hits": 0,
            "n_page_hits": 0,
            "n_occurrences": 0,
            "corpora": [],
            "block": None,
            "name": None,
            "renderable": False,
            "kind": _kind_of(char),
        }
    return {
        "char": summary.get("char", char),
        "codepoint": summary.get("codepoint") or codepoint(char),
        "name": summary.get("name"),
        "block": summary.get("block"),
        "known": True,
        "n_occurrences": summary.get("n_occurrences") or 0,
        "n_literal": summary.get("n_literal") or 0,
        "n_annotated": summary.get("n_annotated") or 0,
        "n_located": summary.get("n_located") or 0,
        "n_line_hits": summary.get("n_line_hits") or 0,
        "n_page_hits": summary.get("n_page_hits") or 0,
        "n_units": summary.get("n_units") or 0,
        "n_glyph_rects": n_glyph_rects,
        "n_glyphs": n_glyph_rects + (summary.get("n_units") or 0),
        "renderable": bool(n_glyph_rects or (summary.get("n_units") or 0)),
        "n_documents": summary.get("n_documents") or 0,
        "n_pages_approx": summary.get("n_pages_approx") or 0,
        "n_pages_is_estimate": True,
        "corpora": (summary.get("corpora") or "").split(",") if summary.get("corpora") else [],
        "sample_documents": json.loads(summary.get("sample_documents") or "[]"),
        "kind": _kind_of(summary.get("char", char)),
    }


#: Characters whose shape is a ligature of two kana, and the kana they join.
LIGATURE_READINGS = {
    "\U0002a708": ("トモ", "katakana"),
    "\u30ff": ("コト", "katakana"),
    "\u309f": ("より", "hiragana"),
}


def _kind_of(char: str) -> dict[str, Any]:
    """What sort of character this is, for a candidate list.

    A ligature is offered under the reading a typist would actually type, which is
    the whole point of the list: typing トモ should surface 𪜈.
    """
    entry = LIGATURE_READINGS.get(char)
    if not entry:
        return {"is_ligature": False, "reading": None, "script": None}
    reading, script = entry
    return {"is_ligature": True, "reading": reading, "script": script, "typed_as": reading}


def _count_rows(path: Path) -> int:
    import pyarrow.parquet as pq

    try:
        return pq.ParquetFile(path).metadata.num_rows
    except (OSError, ValueError):  # pragma: no cover - bad file
        return 0


def _public_stats(stats: Any) -> dict[str, Any]:
    """Index stats with no filesystem detail: a name, not a path."""
    payload = stats.as_dict()
    payload.pop("root", None)
    payload["directory"] = Path(str(payload.get("directory") or "")).name
    return payload


def _public_source(corpus: corpus_sources.Corpus) -> dict[str, Any]:
    """A corpus record with no local filesystem detail."""
    payload = corpus.as_dict()
    payload.pop("directory", None)
    return payload


def _resolve(raw: str) -> str | None:
    """A character from a character or a code point.

    ``+`` decodes to a space in a query string, so ``U+2A708`` arrives as ``U 2A708``
    unless the caller percent-encodes it. Both spellings are accepted rather than
    making correctness depend on the caller's encoder.
    """
    if not raw:
        return None
    text = raw.strip()
    m = re.fullmatch(r"[Uu][+ ]([0-9A-Fa-f]{4,6})", text)
    if m:
        try:
            return chr(int(m.group(1), 16))
        except ValueError:
            return None
    if re.fullmatch(r"[0-9A-Fa-f]{4,6}", text) and len(text) >= 4 and not text.isdigit():
        try:
            return chr(int(text, 16))
        except ValueError:
            return None
    if len(text) == 1:
        return text
    return None


def _result_order(occurrence: Occurrence) -> tuple:
    """Sort key that puts renderable results first, then located, then the rest."""
    return (
        0
        if occurrence.has_glyph_rect
        else 1
        if occurrence.has_crop
        else 2
        if occurrence.tier == "line_text"
        else 3,
        occurrence.source.corpus,
        occurrence.source.document_id,
        occurrence.source.page_id or "",
        occurrence.span_start,
    )


def _filter(occurrences: list[Occurrence], request: Request) -> list[Occurrence]:
    corpora = request.many("corpus")
    tiers = request.many("tier")
    class_ = request.one("class")
    located = request.one("located")
    out = occurrences
    if corpora:
        wanted = set(corpora)
        out = [o for o in out if o.source.corpus in wanted]
    if tiers:
        wanted = set(tiers)
        out = [o for o in out if o.tier in wanted]
    if class_:
        out = [o for o in out if o.char_class == class_]
    if located in ("1", "true", "yes"):
        out = [o for o in out if o.has_crop]
    return out


def _json(status: int, payload: Any) -> tuple[int, str, bytes]:
    return (
        status,
        "application/json; charset=utf-8",
        json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8"),
    )


def _visual_analysis(cp: str) -> dict[str, Any]:
    try:
        from ..visual_families import family_analysis
    except ImportError:
        return {}
    analysis = family_analysis(cp)
    return {"visual_analysis": analysis, "visual_groups": (analysis or {}).get("groups", [])}


class Router:
    """A minimal path router. Mount :meth:`handle` wherever the app needs it."""

    def __init__(self, api: CorpusAPI):
        self.api = api
        self.routes: list[tuple[str, re.Pattern[str], Callable[..., Any]]] = [
            ("GET", re.compile(r"^/api/corpus/sources/?$"), api.sources),
            ("GET", re.compile(r"^/api/corpus/stats/?$"), api.stats),
            ("GET", re.compile(r"^/api/corpus/index-status/?$"), api.index_status),
            ("GET", re.compile(r"^/api/corpus/chars/?$"), api.characters),
            ("GET", re.compile(r"^/api/corpus/counts/?$"), api.counts),
            ("GET", re.compile(r"^/api/corpus/find/?$"), api.find),
            ("GET", re.compile(r"^/api/corpus/glyphs/?$"), api.glyphs),
            ("GET", re.compile(r"^/api/corpus/thumb/?$"), api.thumb),
            ("GET", re.compile(r"^/api/corpus/crop/?$"), api.crop),
            ("GET", re.compile(r"^/api/corpus/occurrence/(?P<occurrence_id>[^/]+)/?$"), api.occurrence),
        ]

    def handle(self, method: str, target: str) -> tuple[int, str, bytes]:
        parsed = urlparse(target)
        path = parsed.path
        query = parse_qs(parsed.query)
        for route_method, pattern, handler in self.routes:
            if route_method != method.upper():
                continue
            match = pattern.match(path)
            if not match:
                continue
            request = Request(method=method.upper(), path=path, query=query)
            return handler(request, **{k: unquote(v) for k, v in match.groupdict().items()})
        return _json(404, {"error": "no such route", "method": method, "path": path})
