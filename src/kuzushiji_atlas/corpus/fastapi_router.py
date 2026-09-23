"""FastAPI adapter for :mod:`kuzushiji_atlas.corpus.api`.

The layers worker uses the framework-free object directly, in-process::

    from kuzushiji_atlas.corpus import CorpusAPI
    api = CorpusAPI(root, index_directory)
    status, content_type, body = api.handle_get("/api/corpus/counts?chars=𪜈")

This module is only for mounting the *same* object on an existing FastAPI app::

    from kuzushiji_atlas.corpus import CorpusAPI
    from kuzushiji_atlas.corpus.fastapi_router import corpus_router

    api = CorpusAPI(root, index_directory)      # built once, at startup
    app.include_router(corpus_router(api=api))  # shares that instance

There is one corpus object per process and no separate corpus web server. Every
handler closes over the instance passed in, so a mount never builds a second index
reader, a second character cache or a second glyph registry.

Query strings are built with :func:`urllib.parse.urlencode`, not by concatenation:
``U+2A708`` must arrive as ``U%2B2A708`` and a character containing ``&`` must not
split the query.

FastAPI is imported lazily, so the corpus package stays usable without it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

from . import index as index_module
from . import sources as corpus_sources
from .api import CorpusAPI
from .crops import DEFAULT_CROP_EDGE

PREFIX = "/api/corpus"


def build_query(**params: Any) -> str:
    """A query string that survives characters, code points and repetition.

    ``doseq=True`` expands a list into repeated keys (``corpus=a&corpus=b``), which is
    what the framework-free layer expects, and every value is percent-encoded so a
    ``+`` in a code point is not read back as a space.
    """
    clean = {k: v for k, v in params.items() if v is not None and v != [] and v != ""}
    return urlencode(clean, doseq=True, encoding="utf-8", errors="strict")


def corpus_api(
    root: str | Path = corpus_sources.DEFAULT_ROOT,
    directory: str | Path = index_module.INDEX_DIR,
    review: Any = None,
    *,
    autobuild: bool = True,
) -> CorpusAPI:
    """Build the handler object. Call once at startup."""
    return CorpusAPI(root, directory, review, autobuild=autobuild)


def corpus_router(
    api: CorpusAPI | None = None,
    root: str | Path = corpus_sources.DEFAULT_ROOT,
    directory: str | Path = index_module.INDEX_DIR,
    review: Any = None,
    *,
    autobuild: bool = True,
    prefix: str = PREFIX,
):
    """Mount the corpus routes on an existing app.

    Pass ``api`` to share the object the rest of the process already holds; otherwise
    one is built from ``root``/``directory``, which are read once here and reused for
    every request.
    """
    from fastapi import APIRouter, Query, Response

    if api is None:
        api = corpus_api(root, directory, review, autobuild=autobuild)
    router = APIRouter(prefix=prefix, tags=["corpus"])

    def _send(target: str) -> Response:
        status, content_type, body = api.handle_get(target)
        return Response(content=body, status_code=status, media_type=content_type)

    @router.get("/sources", summary="Indexed corpora, their shape and their rights")
    def sources() -> Response:
        return _send(f"{prefix}/sources")

    @router.get("/stats", summary="Index size, on-disk bytes and RAM estimate")
    def stats() -> Response:
        return _send(f"{prefix}/stats")

    @router.get("/chars", summary="Characters present in the corpora, by frequency")
    def chars(q: str | None = None, limit: int = 60) -> Response:
        return _send(f"{prefix}/chars?" + build_query(q=q, limit=limit))

    @router.get("/counts", summary="Batched per-character counts for autocomplete")
    def counts(chars: str | None = None, q: str | None = None, limit: int = 60) -> Response:
        return _send(f"{prefix}/counts?" + build_query(chars=chars, q=q, limit=limit))

    @router.get("/find", summary="Occurrences of a character in the source texts")
    def find(
        char: str = Query(..., description="a character, or U+XXXX"),
        corpus: list[str] | None = Query(None),  # noqa: B008
        tier: list[str] | None = Query(None),  # noqa: B008
        char_class: str | None = Query(None, alias="class"),
        located: int = 0,
        limit: int = 60,
        offset: int = 0,
        thumb: int = 1,
    ) -> Response:
        return _send(
            f"{prefix}/find?"
            + build_query(
                char=char,
                corpus=corpus,
                tier=tier,
                **{"class": char_class},
                located=located or None,
                limit=limit,
                offset=offset,
                thumb=thumb,
            )
        )

    @router.get("/glyphs", summary="Isolated glyph rectangles, or a bounded homepage sample")
    def glyphs(
        char: str | None = Query(None, description="a character or U+XXXX; omit for the homepage sample"),
        corpus: list[str] | None = Query(None),  # noqa: B008
        limit: int = 60,
        offset: int = 0,
        w: int = 240,
        scope: str = "character",
        visual_group: str | None = None,
    ) -> Response:
        return _send(
            f"{prefix}/glyphs?" + build_query(char=char, corpus=corpus, limit=limit, offset=offset, w=w,
                                             scope=scope, visual_group=visual_group)
        )

    @router.get("/thumb", summary="The rectangle to draw, and whether it may be shown")
    def thumb(occurrence_id: str, role: str = "glyph", w: int = 320) -> Response:
        return _send(f"{prefix}/thumb?" + build_query(occurrence_id=occurrence_id, role=role, w=w))

    @router.get("/crop", summary="Serve the bytes of one registered glyph crop")
    def crop(
        unit_id: str = Query(..., description="a unit id from /glyphs"), w: int = DEFAULT_CROP_EDGE
    ) -> Response:
        # Bytes or a JSON refusal, exactly as the framework-free layer decided.
        return _send(f"{prefix}/crop?" + build_query(unit_id=unit_id, w=w))

    @router.get("/occurrence/{occurrence_id}", summary="One occurrence in full")
    def occurrence(occurrence_id: str) -> Response:
        # A path segment, so it is quoted rather than form-encoded.
        return _send(f"{prefix}/occurrence/{quote(occurrence_id, safe='')}")

    return router
