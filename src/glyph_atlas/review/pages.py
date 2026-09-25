"""The page photos of a dataset and the character boxes on them, for drawing boxes by hand.

    GET  /atlas/pages                     every document with its pages and their active unit counts
    GET  /atlas/pages/{page_id}           one page, the URL of its photo and every active unit on it
    POST /atlas/pages/{page_id}/units     record a character box drawn on the photo

A drawn box is a unit with `method` `manual`, on the page's own line (`Store.draw_unit`). It starts
unidentified; the character is set through `POST /layers/units/{id}`, and a box drawn by mistake is
retired through `POST /reviews` with `field` `active` and `new` false. Every write goes through the
review log, so `atlas review apply` writes it to `lines.parquet` and `units.parquet`.

A box `atlas review propose-marks` proposed is a unit with `method` `detect` on the same line,
`proposed` while its review is still `machine`. The reviewer names it, leaves it, or retires it the
same way; naming it makes it reviewed.

These routes exist on the local review service only, which is how the interface knows it may offer
drawing.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from ..schema import Page, ReviewState, Unit
from .atlas import label, written_identity
from .characters import page_digest
from .store import DETECT, MANUAL, DrawRequest, Store


def router(store: Store) -> APIRouter:
    api = APIRouter()

    def ordered_pages() -> dict[str, list[Page]]:
        by_document: dict[str, list[Page]] = {}
        for page in store.pages().values():
            by_document.setdefault(page.document_id, []).append(page)
        for pages in by_document.values():
            pages.sort(key=lambda page: (page.seq, page.id))
        return by_document

    def unit_item(unit: Unit, revision: int) -> dict[str, Any]:
        return {
            "id": unit.id,
            "line_id": unit.line_id,
            "box": unit.box.model_dump() if unit.box else None,
            "character": written_identity(unit) or None,
            "code_point": unit.unicode,
            "reading": label(unit) or None,
            "manual": unit.method == MANUAL,
            "detected": unit.method == DETECT,
            "proposed": unit.method == DETECT and unit.review == ReviewState.MACHINE,
            "revision": revision,
        }

    @api.get("/atlas/pages")
    def pages() -> dict[str, Any]:
        """Every document with its pages in order, and the active units of each page."""
        counts = store.unit_counts_by_page()
        by_document = ordered_pages()
        documents = []
        for document in store.documents():
            own = by_document.get(document.id, [])
            documents.append({
                "id": document.id,
                "title": document.title,
                "units": sum(counts.get(page.id, 0) for page in own),
                "pages": [{"id": page.id, "seq": page.seq, "units": counts.get(page.id, 0)} for page in own],
            })
        return {"documents": documents}

    @api.get("/atlas/pages/{page_id}")
    def page(page_id: str) -> dict[str, Any]:
        """One page: its photo, its size in the pixels boxes are given in, and its active units.

        `drawable` says whether a box can be drawn here: the photo is cached and the page records the
        size the boxes are measured against. The photo may be a different size than that record, so
        a client draws in page pixels and scales the photo to them.
        """
        record = store.page(page_id)
        if record is None:
            raise HTTPException(404, f"no page {page_id}")
        from .server import cached_image

        digest = page_digest(record)
        cached = bool(digest and cached_image(digest))
        document = store.document(record.document_id)
        siblings = ordered_pages().get(record.document_id, [])
        index = next(i for i, page in enumerate(siblings) if page.id == page_id)
        units = sorted(store.units_of_page(page_id), key=lambda unit: unit.id)
        revisions = store.revisions([unit.id for unit in units])
        return {
            "id": record.id,
            "document_id": record.document_id,
            "document_title": document.title if document else None,
            "seq": record.seq,
            "width": record.width,
            "height": record.height,
            "image_url": f"/images/{digest}" if cached else None,
            "image_sha256": digest if cached else None,
            "drawable": cached and bool(record.width and record.height),
            "previous": siblings[index - 1].id if index > 0 else None,
            "next": siblings[index + 1].id if index + 1 < len(siblings) else None,
            "units": [unit_item(unit, revisions.get(unit.id, 0)) for unit in units],
        }

    @api.post("/atlas/pages/{page_id}/units", status_code=201)
    def draw(page_id: str, request: DrawRequest) -> dict[str, Any]:
        """Record a box drawn on the page photo; the unit starts unidentified."""
        result = store.draw_unit(page_id, request)
        unit = store.unit(result["unit"]["target_id"])
        return {**result, "item": unit_item(unit, result["unit"]["revision"]) if unit else None}

    return api
