"""The books of a dataset, their page photos and the character boxes on the pages.

    GET  /atlas/books                    the books a query finds, with their page and box counts
    GET  /atlas/books/{book_id}          one book, its cover, its credit and its pages in order
    GET  /atlas/thumbnail/{sha256}        one photo of the image cache, shrunk to 320 pixels and as JPEG
    GET  /atlas/pages/{page_id}          one page, the URL of its photo and every active unit on it
    POST /atlas/pages/{page_id}/units     record a character box drawn on the photo

A book is shown by the first page of its copy whose photo is cached (`covering_page`). A page is
shown, in a list or on a filmstrip, by `/atlas/thumbnail/<its image's checksum>`: the cache file
shrunk to a small JPEG, held in `cache/images/thumbs` and served for a year under that checksum.

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

import io
import os
import re
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from PIL import Image

from .. import page_sources
from ..production import production_info
from ..schema import Document, Page, ReviewState, Unit
from .atlas import written_identity
from .characters import page_digest
from .store import DETECT, MANUAL, DrawRequest, Store

#: The longest side of a thumbnail, in pixels, and where its JPEG is kept under the image cache.
THUMBNAIL_EDGE = 320
THUMBNAIL_DIR = "thumbs"

#: How a `Dating.kind` is written for the interface, whose date keys are `date.kind.<name>`.
DATE_KINDS = {"composition": "composed", "copying": "copied", "publication": "printed", "impression": "printed"}


def dating_of(document: Document) -> dict[str, dict[str, Any]]:
    """The dates of a book by axis: the copy's on `witness`, its text's on `composed`. The
    interface places each on the axis its kind belongs to (`lib/dating.js`)."""
    axes: dict[str, dict[str, Any]] = {}
    for date in document.dating:
        axis = "composed" if date.kind == "composition" else "witness"
        found = {"kind": DATE_KINDS.get(date.kind, "produced"), "text": date.literal,
                 "start": date.start, "end": date.end}
        axes.setdefault(axis, found)
    return axes


def credit_of(document: Document | None) -> dict[str, Any]:
    """Who holds the copy and on what terms: the holder, the required attribution, the licence and
    the page that states it. Every part is None where the record gives none."""
    rights = document.image_rights if document else None
    return {"holder": document.holder if document else None,
            "attribution": rights.attribution if rights else None,
            "licence": str(rights.licence) if rights else None,
            "rights_url": rights.evidence if rights else None}


def cover_url(page: Page | None) -> str | None:
    """Where the photo that shows a page in small is: its thumbnail, under the checksum the image
    cache holds it by."""
    from .server import cached_image

    if page is None:
        return None
    digest = page_digest(page)
    return f"/atlas/thumbnail/{digest}" if digest and cached_image(digest) else None


def thumbnail(sha256: str) -> bytes:
    """A cached page photo shrunk to `THUMBNAIL_EDGE`: the JPEG in `thumbs/`, written on first use,
    so a list of pages reads small files as often as it reads the originals."""
    from .server import cached_image

    source = cached_image(sha256)
    if source is None:
        raise HTTPException(404, f"{sha256} is not in the image cache")
    folder = source.parent.parent / THUMBNAIL_DIR
    made = folder / f"{source.stem}.jpg"
    if made.exists():
        return made.read_bytes()
    with Image.open(source) as picture:
        small = picture.convert("RGB")
        small.thumbnail((THUMBNAIL_EDGE, THUMBNAIL_EDGE), Image.Resampling.LANCZOS)
        output = io.BytesIO()
        small.save(output, format="JPEG", quality=80)
    folder.mkdir(parents=True, exist_ok=True)
    # A reader asking at the same moment may be writing the same file; it lands whole or not at all.
    partial = folder / f".{source.stem}.{os.getpid()}.part"
    partial.write_bytes(output.getvalue())
    partial.replace(made)
    return output.getvalue()


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
            "manual": unit.method == MANUAL,
            "detected": unit.method == DETECT,
            "proposed": unit.method == DETECT and unit.review == ReviewState.MACHINE,
            "revision": revision,
        }

    def covering_page(pages: list[Page]) -> Page | None:
        """The page that shows a book: the first page of its copy whose photo is cached."""
        return next((page for page in pages if cover_url(page)), None)

    def book_item(document: Document, pages: list[Page], counts: dict[str, int]) -> dict[str, Any]:
        return {
            "id": document.id,
            "title": document.title,
            "shelfmark": document.shelfmark,
            "genre": document.genre,
            **production_info(document),
            **credit_of(document),
            "dating": dating_of(document),
            "cover_url": cover_url(covering_page(pages)),
            "page_count": len(pages),
            "box_count": sum(counts.get(page.id, 0) for page in pages),
        }

    def says(document: Document, word: str) -> bool:
        """Whether the book answers to a word of its title, holder, shelfmark, genre, upstream ids
        or the words its dates are written in.

        A Latin word starts one of the book's words (so `photog` finds Photographs and `a` alone
        finds no Calibration); Han, kana and Hangul run together without spaces, so a word in them
        is found anywhere inside.
        """
        fields = [document.title, document.holder, document.shelfmark, *document.genre,
                  *document.source_refs.values(), *(date.literal or "" for date in document.dating)]
        for field in fields:
            text = (field or "").casefold()
            if word.isascii():
                # A word boundary opens a Latin word; the rest of the word may follow as typed.
                if re.search(r"(?<!\w)" + re.escape(word), text):
                    return True
            elif word in text:
                return True
        return False

    @api.get("/atlas/books")
    def books(
        q: Annotated[str | None, Query(max_length=200)] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 48,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> dict[str, Any]:
        """The books a query finds, by title and in table order for equal titles.

        Every word of `q` has to be found, so a title can be searched by its holder and date too.
        """
        counts = store.unit_counts_by_page()
        by_document = ordered_pages()
        words = (q or "").casefold().split()
        found = [document for document in store.documents() if all(says(document, word) for word in words)]
        found.sort(key=lambda document: (document.title.casefold(), document.id))
        return {"query": q or None, "total": len(found), "limit": limit, "offset": offset,
                "books": [book_item(document, by_document.get(document.id, []), counts)
                          for document in found[offset:offset + limit]]}

    @api.get("/atlas/books/{book_id}")
    def book(book_id: str) -> dict[str, Any]:
        """One book with its pages in order: what a reader picks from to open a page."""
        document = store.document(book_id)
        if document is None:
            raise HTTPException(404, f"no book {book_id}")
        counts = store.unit_counts_by_page()
        pages = ordered_pages().get(book_id, [])
        return {**book_item(document, pages, counts),
                "pages": [{"id": page.id, "seq": page.seq, "units": counts.get(page.id, 0),
                           "width": page.width, "height": page.height, "thumb_url": cover_url(page)}
                          for page in pages]}

    @api.get("/atlas/pages/{page_id}")
    def page(page_id: str) -> dict[str, Any]:
        """One page: its photo, its size in the pixels boxes are given in, and its active units.

        `drawable` says whether a box can be drawn here: the photo is cached and the page records the
        size the boxes are measured against. The photo may be a different size than that record, so
        a client draws in page pixels and scales the photo to them.

        The page also names where it came from: the book it is in with the book's credit, the
        manifest it is a canvas of and its place among the canvases, and the holder's catalogue
        record of the book, so the page can be checked at its source.
        """
        record = store.page(page_id)
        if record is None:
            raise HTTPException(404, f"no page {page_id}")
        from .server import cached_image

        digest = page_digest(record)
        cached = bool(digest and cached_image(digest))
        document = store.document(record.document_id)
        catalogue = page_sources.catalogue_urls(
            (book.id, book.source_refs, book.meta) for book in store.documents()).get(record.document_id)
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
            **credit_of(document),
            "dating": dating_of(document) if document else {},
            **page_sources.page_sources(document.source_refs if document else None,
                                       record.canvas, record.image, catalogue),
            "units": [unit_item(unit, revisions.get(unit.id, 0)) for unit in units],
        }

    @api.get("/atlas/thumbnail/{sha256}")
    def thumb(sha256: str) -> Response:
        """A page photo as a small JPEG, from the image cache and shrunk on first use."""
        return Response(thumbnail(sha256), media_type="image/jpeg",
                        headers={"Cache-Control": "public, max-age=31536000, immutable",
                                 "ETag": f'"thumb-{sha256}"'})

    @api.post("/atlas/pages/{page_id}/units", status_code=201)
    def draw(page_id: str, request: DrawRequest) -> dict[str, Any]:
        """Record a box drawn on the page photo; the unit starts unidentified."""
        result = store.draw_unit(page_id, request)
        unit = store.unit(result["unit"]["target_id"])
        return {**result, "item": unit_item(unit, result["unit"]["revision"]) if unit else None}

    return api
