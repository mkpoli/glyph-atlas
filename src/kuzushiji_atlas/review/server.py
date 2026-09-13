"""The review service: FastAPI endpoints over one dataset directory.

    atlas review serve work/codh --port 8770

The service reads the tables of the directory, keeps the reviews in `<directory>/review.sqlite` and
serves the page images of the local cache by checksum.

    GET  /documents                     every document, paginated, with its counts
    GET  /pages/{page_id}               one page and the URL its image is served from
    GET  /images/{sha256}               a page image from `cache/images`
    GET  /pages/{page_id}/lines         the lines of a page, in reading order
    GET  /lines/{line_id}/units         the active units of a line, with their revisions
    GET  /units/{unit_id}/candidates    the code points the unit's reading may have been written
                                        with, with 字母, NINJAL reference glyphs and T31 scores
    GET  /queue                         the lines to review, by strategy, document and page
    POST /reviews                       record editorial decisions
    POST /lines                         record a line the detector missed
    POST /units                         record a unit drawn on a line

A review carries the revision it was made from; a `base_revision` older than the target's current
revision answers 409 with the current state, and so does a review of a retired unit. Repeating an
`idempotency_key` answers the earlier result instead of recording a second event.

The collection endpoints answer `{"total", "limit", "offset", "items"}`. A conflict answers
`{"detail": {"error", "revision", "state", …}}`; anything else answers `{"detail": "…"}`.
"""

from __future__ import annotations

import re
from functools import cache
from pathlib import Path
from typing import Annotated, Any, Literal

import uvicorn
from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .. import __version__, ainu_source, images, refs
from ..schema import Unit
from . import corrections, status
from .store import (
    BadRequest,
    Conflict,
    CorrectionRequest,
    LineRequest,
    NotFound,
    RetractRequest,
    ReviewRequest,
    Store,
    StoreError,
    UnitRequest,
)

#: The checksum of a cached file, which is also its name under `cache/images/<first two>/`.
SHA256 = re.compile(r"[0-9a-f]{64}")


def _opened(source: str | None) -> Any | None:
    """The publishing project's tree, or None when no usable checkout was named.

    A path that is not a checkout is not an error for the dashboard: the atlas is useful without the
    bridge, and refusing to serve the page because a sibling repository moved would be the wrong
    failure. What the bridge adds is shown when it can be read, and absent when it cannot.
    """
    if not source:
        return None
    try:
        return ainu_source.AinuSource(Path(source))
    except ainu_source.AinuSourceError:
        return None


def _placement(source: str, document: Any) -> dict[str, Any] | None:
    """Where one document sits in the publishing project, by entry id."""
    opened = _opened(source)
    if opened is None:
        return None
    found = opened.map_document(document)
    if found is None:
        return None
    return {
        "unit": found.unit, "work": found.work, "witness": found.witness,
        "part": found.part.label if found.part else None, "entry": found.entry,
        "holder": found.holder, "catalogue": found.catalogue,
    }


def _source_summary(source: str) -> dict[str, Any] | None:
    """How much of the publishing project this dataset is.

    The atlas imports what its own curation lists, which is a subset of what the source publishes.
    Saying so is the difference between an honest dashboard and one that implies the collection is
    nine witnesses when twenty works are published upstream.
    """
    opened = _opened(source)
    if opened is None:
        return {"readable": False, "path": Path(source).name}
    works = opened.works()
    return {
        "readable": True,
        "path": Path(source).name,
        "works": len(works),
        "witnesses": sum(len(work.witnesses) for work in works),
        "parts": len(opened.entries()),
        "corrections": len(opened.corrections()),
    }


def create_app(directory: Path) -> FastAPI:
    """Build the review service over one dataset directory."""
    store = Store(Path(directory))
    app = FastAPI(
        title="kuzushiji-atlas review",
        version=__version__,
        summary="Pages, lines and units of one dataset, and the reviews that change them.",
    )
    app.state.store = store
    app.state.directory = store.directory

    @app.exception_handler(NotFound)
    async def not_found(request: Request, exc: NotFound) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(Conflict)
    async def conflict(request: Request, exc: Conflict) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": exc.detail})

    @app.exception_handler(BadRequest)
    async def bad_request(request: Request, exc: BadRequest) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(StoreError)
    async def store_error(request: Request, exc: StoreError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.get("/project")
    def project(source: Annotated[str | None, Query(help="path to an ainu-records checkout")] = None) -> dict[str, Any]:
        """The project as a whole: what is imported, how far it is reviewed, and what it belongs to.

        `reviewed` here is not the store's `revisions` count. A revision is written by every event,
        including a note or a dwell time, so it measures activity rather than decisions; the counts
        below come from `review.status`, which asks what the record currently says. `quality` reports
        a rate only when an audit sample has been scored, and otherwise says `unmeasured` — an
        unchecked page has no measured precision, and printing 0% for it would invent one.
        """
        units = list(store.iter_units())
        standing = status.unit_reviews(units, store.events(), exported=store.exported_ids())
        per_document = status.by_document(standing.values())
        items = []
        for document in store.documents():
            counts = per_document.get(document.id, status.summarize([]))
            item = {
                "id": document.id,
                "title": document.title,
                "holder": document.holder,
                "shelfmark": document.shelfmark,
                "source_refs": document.source_refs,
                "counts": counts,
                "pages": sum(1 for page in store.pages().values() if page.document_id == document.id),
            }
            if source:
                placement = _placement(source, document)
                if placement is not None:
                    item["source"] = placement
            items.append(item)
        totals = status.summarize(standing.values())
        imported = {
            "documents": len(items),
            "pages": len(store.pages()),
            "units": totals["total"],
        }
        return {
            "imported": imported,
            "counts": totals,
            "quality": status.quality(totals["checked"], None),
            "documents": items,
            "source": _source_summary(source) if source else None,
        }

    @app.get("/pages/{page_id}/corrections")
    def page_corrections(page_id: str) -> dict[str, Any]:
        """One page's editorial corrections, with the text they produce and the reason for any that
        cannot be placed as the page now stands."""
        if store.page(page_id) is None:
            raise HTTPException(status_code=404, detail=f"no page {page_id}")
        base = store.page_text(page_id) or ""
        page = corrections.page_corrections(store, page_id, base)
        return {
            "page_id": page_id,
            "base": base,
            "text": page.text(),
            "total": len(page.judgements),
            "items": [
                {
                    **item.correction.payload(),
                    "status": item.status,
                    "reason": item.reason,
                    "actor": item.correction.actor,
                    "diff": corrections.diff(item.correction, base) if item.applicable else [],
                }
                for item in page.judgements
            ],
        }

    @app.post("/corrections", status_code=201)
    def add_correction(request: Request, correction: CorrectionRequest) -> dict[str, Any]:
        """Record a correction, refusing one that cannot be placed on the page it names.

        A correction that does not place is worth less than none: it looks like work and would fail
        the publishing project's build, which validates the same rule. The refusal names the reason so
        a reviewer can fix it rather than guess.
        """
        try:
            return corrections.record(
                store,
                corrections.Correction(
                    id=correction.id,
                    page_id=correction.target_id,
                    line=correction.line,
                    original=correction.original,
                    corrected=correction.corrected,
                    note=correction.note,
                    kind=correction.kind,
                    ruby_field=correction.ruby_field,
                    ruby_base=correction.ruby_base,
                    entry=correction.entry,
                ),
                client_id=request.headers.get("x-atlas-client"),
                base_revision=correction.base_revision,
            )
        except corrections.CorrectionError as exc:
            # A placement problem is the client's to fix and the answer names which: the store's
            # `BadRequest` means "this field does not fit this target" and carries a 422, while a
            # correction that cannot be placed is an ordinary 400 with the reason.
            raise StoreError(str(exc)) from exc

    @app.post("/corrections/{correction_id}/retract")
    def retract_correction(
        correction_id: str, request: Request, retraction: RetractRequest
    ) -> dict[str, Any]:
        """Take a correction back. The journal keeps both the record and the retraction."""
        if store.page(retraction.page_id) is None:
            raise NotFound(f"no page {retraction.page_id}")
        return corrections.retract(
            store, retraction.page_id, correction_id,
            client_id=request.headers.get("x-atlas-client"), reason=retraction.reason,
        )

    @app.get("/documents")
    def documents(
        limit: Annotated[int, Query(ge=1, le=500)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> dict[str, Any]:
        """Every document with its page, line, unit and reviewed-unit counts."""
        records = store.documents()
        counts = store.counts()
        items = []
        for document in records[offset : offset + limit]:
            item = document.model_dump(mode="json")
            item.update(counts.get(document.id, {"pages": 0, "lines": 0, "units": 0, "reviewed": 0}))
            items.append(item)
        return {"total": len(records), "limit": limit, "offset": offset, "items": items}

    @app.get("/pages")
    def pages_list(
        document: Annotated[str | None, Query(help="only this document")] = None,
        pending: Annotated[bool, Query(help="only pages with something left to do")] = False,
        limit: Annotated[int, Query(ge=1, le=2000)] = 200,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> dict[str, Any]:
        """Every page with its counts, so a page with no boxes is still reachable.

        A page whose alignment found nothing has no lines and no units, and before this listing there
        was no way to browse to it: every other route starts from a line or a unit. The counts come
        from `review.status`, so `checked` counts decisions and `machine` counts accepted output that
        nobody has looked at — a reviewer's queue, in other words, rather than a progress bar.
        """
        units = list(store.iter_units())
        standing = status.by_page(status.unit_reviews(units, store.events(),
                                                      exported=store.exported_ids()).values())
        records = store.pages()
        items = []
        for page_id, page in records.items():
            if document is not None and page.document_id != document:
                continue
            counts = standing.get(page_id, status.summarize([]))
            if pending and counts["machine"] == 0 and counts["draft"] == 0:
                continue
            lines = store.lines_of_page(page_id)
            items.append({
                "id": page_id,
                "document_id": page.document_id,
                "seq": page.seq,
                "image_url": f"/images/{page.sha256}" if page.sha256 else page.image,
                "transcribed": bool(store.page_text(page_id)),
                "lines": len(lines),
                "counts": counts,
            })
        return {"total": len(items), "limit": limit, "offset": offset,
                "items": items[offset : offset + limit]}

    @app.get("/pages/{page_id}")
    def page(page_id: str) -> dict[str, Any]:
        """One page, with the counts of its lines and the URL its image is served from."""
        record = store.page(page_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"no page {page_id}")
        item = record.model_dump(mode="json")
        item.update(store.page_counts(page_id))
        item["image_url"] = f"/images/{record.sha256}" if record.sha256 else record.image
        return item

    @app.get("/images/{sha256}")
    def image(sha256: str) -> FileResponse:
        """A page image from the local cache, looked up by checksum."""
        path = cached_image(sha256)
        if path is None:
            raise HTTPException(status_code=404, detail=f"{sha256} is not in the image cache")
        return FileResponse(path)

    @app.get("/pages/{page_id}/lines")
    def page_lines(
        page_id: str,
        limit: Annotated[int, Query(ge=1, le=2000)] = 500,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> dict[str, Any]:
        """The lines of a page in reading order, with the number of active units of each."""
        if store.page(page_id) is None:
            raise HTTPException(status_code=404, detail=f"no page {page_id}")
        summaries = store.line_summaries(store.lines_of_page(page_id))
        items = [
            {**summary["line"].model_dump(mode="json"), "units": summary["units"], "revision": summary["revision"]}
            for summary in summaries[offset : offset + limit]
        ]
        return {"page_id": page_id, "total": len(summaries), "limit": limit, "offset": offset, "items": items}

    @app.get("/lines/{line_id}/units")
    def line_units(
        line_id: str,
        limit: Annotated[int, Query(ge=1, le=2000)] = 500,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> dict[str, Any]:
        """The active units of a line, each with the number of events recorded on it."""
        if store.line(line_id) is None:
            raise HTTPException(status_code=404, detail=f"no line {line_id}")
        units = store.units_of_line(line_id)
        revisions = store.revisions([unit.id for unit in units])
        items = [
            {**unit.model_dump(mode="json"), "revision": revisions.get(unit.id, 0)}
            for unit in units[offset : offset + limit]
        ]
        return {"line_id": line_id, "total": len(units), "limit": limit, "offset": offset, "items": items}

    @app.get("/units/{unit_id}/candidates")
    def unit_candidates(unit_id: str) -> dict[str, Any]:
        """The code points a unit's reading may have been written with.

        The ordinary kana of the reading comes first, then every hentaigana with that 音価 from
        `data/vocab/mj-hentaigana.tsv`, each with its 字母, its NINJAL reference glyph URL where the
        table has one, and the T31 score where the unit carries one.
        """
        unit = store.unit(unit_id)
        if unit is None:
            raise HTTPException(status_code=404, detail=f"no unit {unit_id}")
        return candidates_for(unit)

    @app.get("/queue")
    def queue(
        strategy: Literal["random", "disagreement", "unreviewed"] = "unreviewed",
        document: str | None = None,
        seed: int = 0,
        limit: Annotated[int, Query(ge=1, le=500)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> dict[str, Any]:
        """The lines to review.

        `unreviewed` keeps the lines no event has touched, `disagreement` the lines the classifier
        left open or labelled against its own top candidate, and `random` shuffles every line with
        `seed`. `document` keeps one document.
        """
        summaries = store.queue(strategy, document=document, seed=seed)
        items = []
        for summary in summaries[offset : offset + limit]:
            item = summary["line"].model_dump(mode="json")
            item.update(
                {
                    "document_id": summary["document_id"],
                    "page_id": summary["page_id"],
                    "units": summary["units"],
                    "reviewed": summary["reviewed"],
                    "revision": summary["revision"],
                    "disagreements": summary["disagreements"],
                }
            )
            items.append(item)
        return {
            "strategy": strategy,
            "document": document,
            "total": len(summaries),
            "limit": limit,
            "offset": offset,
            "items": items,
        }

    @app.post("/reviews")
    def post_reviews(reviews: Annotated[list[ReviewRequest] | ReviewRequest, Body()]) -> dict[str, Any]:
        """Record one decision or a list of them; each result carries the new revision and state."""
        requests = reviews if isinstance(reviews, list) else [reviews]
        return {"results": [store.record(request) for request in requests]}

    @app.post("/lines", status_code=201)
    def post_line(request: LineRequest) -> dict[str, Any]:
        """Record a line the detector missed. The id is `{page_id}:l{n}`."""
        return store.create_line(request)

    @app.post("/units", status_code=201)
    def post_unit(request: UnitRequest) -> dict[str, Any]:
        """Record a unit drawn on a line. The id is `{line_id}:m{n}`."""
        return store.create_unit(request)

    # The built review interface (`apps/review`, `bun run build`), mounted last so that every API
    # path above keeps its own route; without a build the service is the API alone.
    interface = Path(__file__).resolve().parents[3] / "apps" / "review" / "dist"
    if (interface / "index.html").is_file():
        app.mount("/", StaticFiles(directory=interface, html=True), name="review-interface")

    return app


def serve(directory: Path, *, port: int = 8770, host: str = "127.0.0.1") -> None:
    """Run the review service over one dataset directory, in the foreground."""
    uvicorn.run(create_app(Path(directory)), host=host, port=port, log_level="info")


def cached_image(sha256: str) -> Path | None:
    """The cached file of a checksum: `cache/images/<sha256[:2]>/<sha256>.*`, or None."""
    digest = sha256.strip().lower()
    if not SHA256.fullmatch(digest):
        return None
    folder = images.images_root() / digest[:2]
    if not folder.is_dir():
        return None
    for candidate in sorted(folder.glob(f"{digest}.*")):
        if candidate.is_file():
            return candidate
    return None


def candidates_for(unit: Unit) -> dict[str, Any]:
    """The candidates of a unit's reading, with 字母, reference glyphs and scores."""
    reading = unit.reading or unit.text_source or ""
    code_points = refs.candidates(reading)
    scored = {candidate.unicode: candidate for candidate in unit.candidates}
    extras = [candidate.unicode for candidate in unit.candidates if candidate.unicode not in code_points]
    entries = []
    for code_point in [*code_points, *extras]:
        entry: dict[str, Any] = {"unicode": code_point}
        char = _char(code_point)
        if char is not None:
            entry["char"] = char
        jibo = refs.jibo(code_point)
        if jibo:
            entry["jibo"] = jibo
        entry.update(references().get(code_point, {}))
        candidate = scored.get(code_point)
        if candidate is not None:
            entry["score"] = candidate.p
            if candidate.jibo and "jibo" not in entry:
                entry["jibo"] = candidate.jibo
        if unit.unicode is not None and unit.unicode == code_point:
            entry["current"] = True
        entries.append(entry)
    return {
        "unit_id": unit.id,
        "reading": reading or None,
        "unicode": unit.unicode,
        "jibo": unit.jibo,
        "classification": unit.classification.value,
        "candidates": entries,
    }


@cache
def references() -> dict[str, dict[str, str]]:
    """The reference glyph data of `data/vocab/mj-hentaigana.tsv`, by code point.

    A row contributes the MJ figure name, the 学術用変体仮名番号, the 戸籍統一文字番号 and the
    国語研 URL of the glyph; a row that has none of them contributes nothing.
    """
    table: dict[str, dict[str, str]] = {}
    for row in refs.hentaigana():
        entry = {key: row[key] for key in ("mj", "gakujutsu", "koseki") if row.get(key)}
        if row.get("ninjal_url"):
            entry["reference_url"] = row["ninjal_url"]
        if entry:
            table[row["code_point"]] = entry
    return table


def _char(code_point: str) -> str | None:
    try:
        return refs.to_char(code_point)
    except (TypeError, ValueError):
        return None
