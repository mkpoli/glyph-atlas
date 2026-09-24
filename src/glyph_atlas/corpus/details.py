"""Registered corpus glyphs with source provenance and nearby image regions."""

from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from pathlib import Path
from threading import RLock
from typing import Any
from urllib.parse import urlencode

from ..unit_scope import unit_scope
from . import crops as crops_module
from . import sources as corpus_sources
from .api import PROXYABLE
from .index import (
    UNIT_CORPORA,
    _json,
    _MetaCache,
    _renderable_unit_row,
    _service_base,
    _unit_row,
)

#: Where the click-through image is cut from. Large enough to read a stroke, small
#: enough to stay a thumbnail request rather than a page transfer.
DETAIL_EDGE = 480

#: How many resolved identities to keep. Bounded, because a reader clicking through a
#: grid must not grow the process one row at a time.
DETAIL_CACHE = 128

#: Identities longer than this are not ids. Real ones are well under 200 characters.
MAX_IDENTITY = 300

#: Unit id prefixes, from the importers. This is the real bound on a lookup: a
#: well-formed id names its corpus, so exactly one unit table is opened instead of all.
UNIT_ID_PREFIXES = {
    "codh-omt:": "kokatsuji",
    "codh:": "codh-full",
    "hi:": "hilab",
    "hl:": "honkoku-lines",
    "hk:": "ainu-records",
    "ws:": "wikisource",
}

#: Tables whose content a resolved detail depends on. A change to any of them, or to
#: the glyph registry, invalidates the cache: the row a reader clicked may now describe
#: something else, and a stale detail is worse than a slower one.
PROVENANCE_TABLES = ("units", "pages", "documents", "lines")

#: Characters no identity contains. A ``/``, a scheme or a traversal means the caller
#: is passing a location, not an identity, and locations are not accepted.
_FORBIDDEN = ("/", "\\", "?", "#", "@", "~", " ", "\t", "\n", "\x00")

#: The CODH kuzushiji viewer. ``pos`` is the canvas index in the book's manifest, which
#: is 1-based; ``xywh`` is the box in source pixels.
CODH_VIEWER = "https://codh.rois.ac.jp/char-shape/app/icv-kuzushiji/"
CODH_MANIFEST = "https://codh.rois.ac.jp/char-shape/book/{book}/manifest.json"
CODH_IMAGE_MARKER = "/char-shape/iiif/"

#: How far the derived context viewport reaches beyond the character box, as a
#: multiple of the longer box edge. Enough for neighbouring strokes, not the page.
CONTEXT_PAD = 2.0


def _refuse(identity: str) -> None:
    """Raise :class:`KeyError` for anything that is not an identity.

    Deliberately strict and deliberately indistinguishable from "not found": a caller
    probing for path handling learns nothing from the error.
    """
    if not identity or not isinstance(identity, str):
        raise KeyError(identity)
    if len(identity) > MAX_IDENTITY:
        raise KeyError(identity)
    if any(bad in identity for bad in _FORBIDDEN):
        raise KeyError(identity)
    if ".." in identity or "://" in identity:
        raise KeyError(identity)


def _digest(payload: dict[str, Any]) -> str:
    """A stable digest of provenance, not of image bytes.

    Built from the canonical upstream identity, the label, the box and the image
    reference, so two rows describing the same glyph in the same place agree while a
    changed label or box does not. Named ``source_revision`` rather than a checksum so
    nobody mistakes it for a hash of the pixels — which is what makes a saved
    correction detectable as stale.
    """
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _public_box(box: Any) -> dict[str, int] | None:
    if not box:
        return None
    try:
        return {k: int(box[k]) for k in ("x", "y", "w", "h")}
    except (KeyError, TypeError, ValueError):
        return None


def _iiif_region(service: str | None, box: dict[str, int] | None, edge: int = DETAIL_EDGE) -> str | None:
    """A holder-hosted region URL, or None when there is no service to build one on."""
    if not service or not box:
        return None
    return f"{service}/{box['x']},{box['y']},{box['w']},{box['h']}/{edge},/0/default.jpg"


def _stem(image: Any) -> str | None:
    """The page stem of a page image: ``.../100241706_00027_1.tif`` -> that stem."""
    if not isinstance(image, str) or not image:
        return None
    tail = image.rsplit("/", 1)[-1]
    return tail.rsplit(".", 1)[0] or None


def _codh_book(joined: dict[str, Any], document_id: Any) -> str | None:
    """The CODH book identifier, from the image service path or the document id."""
    service = joined.get("image_service")
    if isinstance(service, str) and CODH_IMAGE_MARKER in service:
        book = service.split(CODH_IMAGE_MARKER, 1)[1].split("/", 1)[0]
        if book:
            return book
    if isinstance(document_id, str) and document_id.startswith("codh:"):
        parts = document_id.split(":")
        if len(parts) >= 2 and parts[1]:
            return parts[1]
    return None


def _codh_viewer(book: str, *, canvas: str | None = None, box: dict[str, int] | None = None) -> str:
    """The CODH viewer URL for a book, optionally opened at one box.

    The registered canvas selects the page even for a partial corpus import.
    """
    query: list[tuple[str, str]] = [("manifest", CODH_MANIFEST.format(book=book))]
    if canvas:
        query.append(("canvas", canvas))
        query.append(("xywh_highlight", "border"))
        if box:
            query.append(("xywh", f"{box['x']},{box['y']},{box['w']},{box['h']}"))
    return CODH_VIEWER + "?" + urlencode(query)


def _is_crop_url(url: Any) -> bool:
    """Whether a URL is this API's own crop endpoint. Never a source link."""
    return isinstance(url, str) and ("/api/corpus/crop" in url or "/default." in url
                                     or url.lower().endswith((".jpg", ".png", ".tif", ".jpeg")))


def _viewport(box: dict[str, int] | None, width: Any, height: Any) -> dict[str, int] | None:
    """A padded window around a character box, clamped to the registered page.

    Only produced when the page size is known: without it there is nothing to clamp
    to, and an unclamped window would be a guess about where the page ends. This is a
    viewport for showing neighbouring strokes — it never replaces the character box.
    """
    if not box:
        return None
    try:
        page_w, page_h = int(width), int(height)
    except (TypeError, ValueError):
        return None
    if page_w <= 0 or page_h <= 0:
        return None
    pad = round(max(box["w"], box["h"]) * CONTEXT_PAD)
    x0 = max(0, box["x"] - pad)
    y0 = max(0, box["y"] - pad)
    x1 = min(page_w, box["x"] + box["w"] + pad)
    y1 = min(page_h, box["y"] + box["h"] + pad)
    if x1 <= x0 or y1 <= y0:
        return None
    if (x0, y0, x1, y1) == (box["x"], box["y"], box["x"] + box["w"], box["y"] + box["h"]):
        return None
    return {"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0}


def _char_of(code_point: Any) -> str | None:
    if not isinstance(code_point, str) or not code_point.startswith("U+"):
        return None
    try:
        return chr(int(code_point.removeprefix("U+"), 16))
    except (ValueError, IndexError):
        return None


class DetailResolver:
    """Resolves one identity, with a bounded cache and a bounded lookup."""

    def __init__(self, api: Any, cache_size: int = DETAIL_CACHE):
        self.api = api
        self._lock = RLock()
        self.cache_size = cache_size
        self._cache: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._corpora = {c.name: c for c in corpus_sources.discover(api.root)}
        self._lines: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._page_order: OrderedDict[str, list[str]] = OrderedDict()
        self._stamp = self._provenance_stamp()

    # ------------------------------------------------------- cache validity
    def _provenance_files(self) -> list[Path]:
        """The files a resolved detail is derived from, plus the glyph registry."""
        files: list[Path] = []
        directory = Path(getattr(self.api, "directory", "."))
        for name in ("glyphs.json", "chars.parquet"):
            candidate = directory / name
            if candidate.is_file():
                files.append(candidate)
        for corpus in self._corpora.values():
            for table in PROVENANCE_TABLES:
                files.extend(corpus.parquet_files(table))
        from ..production import OVERRIDES
        from ..visual_families import directory as visual_directory
        files.extend((OVERRIDES, visual_directory() / "assignments.json"))
        return files

    def _provenance_stamp(self) -> tuple:
        """Modification time and size of everything a detail depends on.

        ``stat`` only, so checking it before every answer costs a few dozen syscalls
        rather than a re-read. Paths stay inside this tuple and are never returned.
        """
        parts: list[tuple[str, int, int]] = []
        for path in self._provenance_files():
            try:
                info = path.stat()
            except OSError:
                continue
            parts.append((path.name, info.st_mtime_ns, info.st_size))
        return tuple(sorted(parts))

    def _stamp_digest(self) -> str:
        return hashlib.sha256(repr(self._stamp).encode("utf-8")).hexdigest()[:16]

    def invalidate_if_changed(self) -> bool:
        """Drop every cached detail when an underlying file changed.

        Returns True when cleared. The crop resolver's own caches go with it: it
        memoises ``CropResult`` and resolved page paths by unit id, so after a rebuilt
        table it would otherwise keep handing back the old row's bytes.
        """
        current = self._provenance_stamp()
        if current == self._stamp:
            return False
        self._stamp = current
        self._cache.clear()
        self._lines.clear()
        self.api.index._glyphs = None
        self._corpora = {c.name: c for c in corpus_sources.discover(self.api.root)}
        crops = getattr(self.api, "crops", None)
        if crops is not None:
            for name in ("_cache", "_page_paths", "_members", "_capability", "_verified"):
                store = getattr(crops, name, None)
                if isinstance(store, dict):
                    store.clear()
        return True

    # ------------------------------------------------------------------ entry
    def get(self, identity: str) -> dict[str, Any]:
        with self._lock:
            return self._get(identity)

    def _get(self, identity: str) -> dict[str, Any]:
        self.invalidate_if_changed()
        # The registry is consulted before the shape check: its keys are ours and some
        # legitimately contain separators. An exact registry hit is safe by definition,
        # because a caller cannot invent a key that is not already in it.
        anchors = getattr(self.api.index, "glyphs", None)
        entry = anchors.get(identity) if anchors is not None and len(anchors) else None
        if entry is None:
            # Only an id that is *not* ours has to look like one. A registry key is a
            # key we wrote, and some of them legitimately contain separators, so an
            # exact match is accepted as it stands.
            _refuse(identity)
        if identity in self._cache:
            self._cache.move_to_end(identity)
            return self._cache[identity]
        found = self._from_anchor(entry) if entry is not None else self._from_unit(identity)
        if found is None:
            raise KeyError(identity)
        if len(self._cache) >= self.cache_size:
            self._cache.popitem(last=False)
        self._cache[identity] = found
        return found

    # ------------------------------------------------------------------ anchor
    def _from_anchor(self, entry: Any) -> dict[str, Any]:
        """A machine-located glyph rectangle registered on top of a text occurrence."""
        source = entry.source or {}
        box = {"x": entry.x, "y": entry.y, "w": entry.w, "h": entry.h}
        service = entry.image_service
        image = _iiif_region(service, box)
        rights = source.get("image_rights") or {}
        licence = rights.get("licence") if isinstance(rights, dict) else None
        line_box = _public_box(entry.line_rect)
        return self._assemble(
            identity=entry.identity_key,
            unit_id=None,
            label=entry.char,
            source_label=entry.char,
            reading=None,
            code_point=entry.codepoint,
            box=box,
            image=image,
            image_reason=None if image else "no IIIF service is recorded for this glyph",
            crop_sha256=entry.crop_sha256,
            proxyable=licence in PROXYABLE,
            licence=licence,
            source={
                "corpus": source.get("corpus"),
                "document_id": source.get("document_id"),
                "page_id": source.get("page_id"),
                "line_id": source.get("line_id"),
                "title": source.get("title"),
                "holder": source.get("holder"),
                "shelfmark": source.get("shelfmark"),
                "source_url": source.get("iiif_region_url"),
                "image_service": service,
            },
            context_box=line_box,
            context_image=_iiif_region(service, line_box, edge=900) if line_box else None,
            context_basis="line_rect" if line_box else None,
            crop_box=box,
            # A viewer page, never the image. The registry's own `iiif_url` is an image
            # request, so it is not used here; a page link is derived the same way a
            # unit's is, and is None when no viewer can be built honestly.
            record_url=self._record_url(
                self._corpora.get(source.get("corpus") or ""),
                {"document_id": source.get("document_id"), "image_service": service, "image": service},
                box,
            ),
            extra={
                "kind": "glyph-rect",
                "method": entry.method,
                "basis": entry.basis,
                "review": "machine",
                "confirmed_by_human": bool(entry.confirmed),
                "human_confirmed": False,
                "inspection_note": entry.inspection_note,
            },
        )

    # -------------------------------------------------------------------- unit
    def _from_unit(self, identity: str) -> dict[str, Any] | None:
        """An imported detector/alignment unit, or a pre-cut crop."""
        located = self._find_unit(identity)
        if located is None:
            return None
        corpus, row = located
        # Encoded classes and source transcriptions remain provenance. CODH merges
        # some written forms into one class, so this alone cannot identify the ink.
        cp = row.get("unicode")
        encoded = _char_of(cp)
        source_label = row.get("text_source")
        reading = joined_reading = row.get("reading")
        label = encoded if encoded is not None else source_label
        context = _MetaCache(corpus)
        joined = _unit_row(corpus, context, row, label or "", cp or "")
        box = _public_box(joined.get("box"))
        page = context.page(joined.get("page_id") or "")
        resolution = self.api.crops.for_unit(identity, edge=DETAIL_EDGE)
        image, reason = self._image_for(resolution, box)
        if getattr(self.api, "media", None) is not None:
            self.api._decorate_unit(joined, width=DETAIL_EDGE)
            image = joined["thumbnail"].get("crop_url") or image
        context_box, context_image, context_basis = self._context_for(corpus, joined, page, box)
        return self._assemble(
            identity=identity,
            unit_id=identity,
            label=label,
            source_label=source_label,
            reading=reading or joined_reading,
            code_point=cp,
            box=box,
            image=image,
            image_reason=reason,
            crop_sha256=joined.get("crop_sha256"),
            source_crop=row.get("crop"),
            proxyable=bool(resolution.proxyable),
            licence=resolution.licence or joined.get("image_licence"),
            source={
                "corpus": corpus.name,
                "document_id": joined.get("document_id"),
                "page_id": joined.get("page_id"),
                "line_id": joined.get("line_id"),
                "title": joined.get("title"),
                "holder": joined.get("holder"),
                "shelfmark": joined.get("shelfmark"),
                "source_url": joined.get("canvas"),
                "image_service": joined.get("image_service"),
                **{key: joined.get(key) for key in ("production", "production_label", "production_evidence")},
            },
            context_box=context_box,
            context_image=context_image,
            context_basis=context_basis,
            crop_box=box,
            record_url=self._record_url(corpus, joined, box),
            extra={
                "kind": "sequence" if unit_scope(row)["needs_segmentation"] else joined.get("kind"),
                **unit_scope(row),
                "method": joined.get("method"),
                "basis": "upstream_bbox" if box else "upstream_crop",
                "review": joined.get("review") or "machine",
                "confirmed_by_human": False,
                "crop_mode": resolution.mode,
                "render_available": bool(resolution.render_available),
                **{key: joined.get(key) for key in ("production", "production_label", "production_evidence")},
            },
        )

    def _image_for(self, resolution: Any, box: dict[str, int] | None) -> tuple[str | None, str | None]:
        """The click-through image URL, and why there is none when there is not.

        Only two things are ever returned: a URL this API serves from bytes it has
        verified, or a holder's own IIIF region URL. A local file that is present but
        unservable is described by its reason, never by its path.
        """
        if not resolution.render_available:
            return None, resolution.reason or "this glyph has no renderable crop"
        if resolution.mode == "local_crop" and resolution.unit_id:
            return crops_module.CropResolver.serve_url(resolution.unit_id, DETAIL_EDGE), None
        if resolution.iiif_url:
            return resolution.iiif_url, None
        if box and resolution.meta.get("served_by") == "holder":
            return None, "the holder serves this image; this API does not proxy it"
        return None, resolution.reason or "no image URL can be built for this glyph"

    def _context_for(
        self,
        corpus: corpus_sources.Corpus,
        joined: dict[str, Any],
        page: dict[str, Any],
        box: dict[str, int] | None,
    ) -> tuple[dict[str, int] | None, str | None, str | None]:
        """The nearby region to show beside the glyph, and what it is based on.

        Two honest sources, in order:

        ``line_rect``
            the containing line, when the corpus recorded one.
        ``derived_viewport``
            a padded window around the *actual* character box, clamped to the page the
            corpus registered. CODH records no line, so this stands in for one: it is a
            viewport, clearly labelled, and it never replaces the character box.

        Nothing else. A page with no known size and no IIIF service yields ``None``
        rather than a URL guessed from an id.
        """
        line = self._line(corpus, joined["line_id"]) if joined.get("line_id") else None
        if line is not None:
            line_box = _public_box(line.get("box"))
            if line_box is not None:
                region_url = line.get("iiif_region_url")
                if region_url:
                    return line_box, region_url, "line_rect"
                service = _service_base(page.get("image"))
                return line_box, _iiif_region(service, line_box, edge=900), "line_rect"

        service = _service_base(page.get("image"))
        viewport = _viewport(box, page.get("width"), page.get("height"))
        if viewport is None or not service:
            return None, None, None
        return viewport, _iiif_region(service, viewport, edge=900), "derived_viewport"

    def _record_url(
        self, corpus: corpus_sources.Corpus, joined: dict[str, Any], box: dict[str, int] | None
    ) -> str | None:
        """Link to the registered canvas, without guessing page numbering."""
        if corpus is not None and corpus.name == "codh-full":
            book = _codh_book(joined, joined.get("document_id"))
            if book:
                canvas = joined.get("canvas")
                if not canvas:
                    stem = _stem(joined.get("image_service") or joined.get("image"))
                    if stem:
                        canvas = f"http://codh.rois.ac.jp/char-shape/iiif/{book}/canvas/{stem}"
                return _codh_viewer(book, canvas=canvas, box=box)
        url = joined.get("record_url")
        if url and not _is_crop_url(url):
            return url
        return None

    def _line(self, corpus: corpus_sources.Corpus, line_id: str) -> dict[str, Any] | None:
        key = f"{corpus.name}|{line_id}"
        if key in self._lines:
            self._lines.move_to_end(key)
            return self._lines[key]
        import pyarrow.dataset as ds

        found: dict[str, Any] | None = None
        files = corpus.parquet_files("lines")
        if files:
            dataset = ds.dataset([str(f) for f in files], format="parquet")
            columns = [c for c in ("id", "box", "meta") if c in dataset.schema.names]
            if "id" in columns:
                scanner = dataset.scanner(columns=columns, filter=ds.field("id") == line_id, batch_size=64)
                for batch in scanner.to_batches():
                    for row in batch.to_pylist():
                        meta = _json(row.get("meta"))
                        found = {"box": row.get("box"), "iiif_region_url": meta.get("iiif_region_url")}
                        break
                    if found is not None:
                        break
        if len(self._lines) >= self.cache_size:
            self._lines.popitem(last=False)
        self._lines[key] = found
        return found

    # ---------------------------------------------------------------- lookup
    def _find_unit(self, identity: str) -> tuple[corpus_sources.Corpus, dict[str, Any]] | None:
        """The unit row with this id, scanned under an Arrow filter.

        The scan is bounded by *routing*, not by a counter: a well-formed id names its
        corpus through its prefix, so exactly one unit table is opened. Only an
        unfamiliar prefix falls back to walking them all, which is the honest cost of
        not knowing where to look. A row budget was considered and rejected — the Arrow
        filter already excludes non-matching rows, so counting them would bound nothing
        while looking like a safeguard.
        """
        import pyarrow.dataset as ds

        for name in self._corpus_order(identity):
            corpus = self._corpora.get(name)
            if corpus is None:
                continue
            files = corpus.parquet_files("units")
            if not files:
                continue
            dataset = ds.dataset([str(f) for f in files], format="parquet")
            if "id" not in dataset.schema.names:
                continue
            columns = [
                c
                for c in (
                    "id",
                    "document_id",
                    "page_id",
                    "line_id",
                    "seq",
                    "box",
                    "crop",
                    "crop_sha256",
                    "kind",
                    "granularity",
                    "text_source",
                    "reading",
                    "unicode",
                    "method",
                    "review",
                    "active",
                    "upstream",
                )
                if c in dataset.schema.names
            ]
            scanner = dataset.scanner(columns=columns, filter=ds.field("id") == identity, batch_size=64)
            for batch in scanner.to_batches():
                for row in batch.to_pylist():
                    if not _renderable_unit_row(row):
                        continue
                    return corpus, row
        return None

    def _corpus_order(self, identity: str) -> list[str]:
        """The corpora to try.

        A registered prefix names one corpus, and only that one is opened. Anything
        else falls back to the full list, because guessing would be worse than looking.
        """
        for prefix, name in UNIT_ID_PREFIXES.items():
            if identity.startswith(prefix) and name in self._corpora:
                return [name]
        return list(UNIT_CORPORA)

    # -------------------------------------------------------------- assembly
    def _assemble(
        self,
        *,
        identity: str,
        unit_id: str | None,
        label: Any,
        source_label: Any,
        reading: Any,
        code_point: Any,
        box: dict[str, int] | None,
        image: str | None,
        image_reason: str | None,
        crop_sha256: Any,
        proxyable: bool,
        licence: Any,
        source: dict[str, Any],
        context_box: dict[str, int] | None,
        context_image: str | None,
        context_basis: str | None,
        crop_box: dict[str, int] | None,
        record_url: Any,
        extra: dict[str, Any],
        source_crop: str | None = None,
    ) -> dict[str, Any]:
        media = getattr(self.api, "media", None)
        if media is not None and proxyable:
            image = (media.remote(image) or image) if image else image
            context_image = (media.remote(context_image) or context_image) if context_image else context_image
        payload = {
            "id": identity,
            "unit_id": unit_id,
            "char": _char_of(code_point) or label,
            "label": label,
            "label_is_verified": False,
            "label_note": "display label; written_character records an assigned form, "
            "while source_label retains the source transcription; neither implies human confirmation",
            "source_label": source_label,
            "source_label_note": "the source corpus's own transcription, imported "
            "unverified; it may be a reading rather than the glyph",
            "source_label_is_verified": False,
            "reading": reading,
            "code_point": code_point,
            "box": box,
            "crop_box": crop_box,
            "image": image,
            "image_is_served_by_this_api": bool(image and image.startswith(("/api/corpus/crop", "/atlas/media/"))),
            "image_unavailable_reason": image_reason,
            "image_edge": DETAIL_EDGE,
            "proxyable": proxyable,
            "licence": licence,
            "source": source,
            "context_box": context_box,
            "context_image": context_image,
            "context_basis": context_basis,
            "record_url": None if _is_crop_url(record_url) else record_url,
            "source_revision": _digest(
                {
                    "identity": identity,
                    "corpus": source.get("corpus"),
                    "document_id": source.get("document_id"),
                    "page_id": source.get("page_id"),
                    "line_id": source.get("line_id"),
                    "label": label,
                    "source_label": source_label,
                    "code_point": code_point,
                    "box": box,
                    "image_service": source.get("image_service"),
                    "source_url": source.get("source_url"),
                    "crop_sha256": crop_sha256,
                    **({"segmentation": {k: extra.get(k) for k in ("granularity", "character_count")}}
                       if extra.get("needs_segmentation") else {}),
                }
            ),
            "source_revision_note": "a digest of canonical provenance, not of image bytes",
            "provenance_stamp": self._stamp_digest(),
            "provenance_stamp_note": "changes when the underlying tables or the glyph "
            "registry change, so a saved correction can be "
            "detected as stale",
        }
        payload.update(extra)
        from ..visual_families import evidence_signature
        from .identity import identity_fields
        payload["source_signature"] = evidence_signature(identity, code_point, source.get("page_id"), box, source_crop)
        payload.update(identity_fields(payload, source.get("corpus"), source_revision=payload["source_revision"]))
        if payload["written_character"]:
            payload["label"] = payload["char"] = payload["written_character"]
            payload["code_point"] = " ".join(f"U+{ord(c):04X}" for c in payload["written_character"])
        return payload


def resolver_for(api: Any) -> DetailResolver:
    """Keep the resolver and its bounded caches for this API's lifetime."""
    found = getattr(api, "_detail_resolver", None)
    if found is None:
        found = DetailResolver(api)
        api._detail_resolver = found
    return found


def detail(api: Any, identity: str, *, cache: DetailResolver | None = None) -> dict[str, Any]:
    """Everything the viewer needs about one corpus glyph row.

    ``identity`` is the ``id`` the gallery already has — a registered unit id, or a
    glyph anchor's ``identity_key``. An exact glyph-registry key is accepted as it
    stands; anything else that looks like a path or a URL raises :class:`KeyError`, as
    does an id no corpus carries.

    The resolver is shared per API object, so repeated clicks reuse its caches and a
    change to the underlying tables invalidates them. Pass ``cache`` to control which
    resolver is used.
    """
    return (cache if cache is not None else resolver_for(api)).get(identity)
