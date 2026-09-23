"""Corpus-wide occurrence discovery and indexing.

The atlas already holds several broad corpora that are not character-segmented —
1.17M transcribed Honkoku lines, 246k page transcriptions, a million CODH units —
and none of them were reachable from the gallery, because the review layer only
knows about *units* that have a cached crop image. This package adds the missing
layer: a character **occurrence** in a source text, with an honest statement of what
is and is not located, plus a bounded index that makes the whole corpus searchable
without loading it.

Quick start::

    from glyph_atlas.corpus import CorpusIndex, build_chars, build_occurrences, TOMO

    build_chars("shared-work", "work/corpus-index")          # one bounded pass
    build_occurrences(TOMO, "shared-work", "work/corpus-index")  # bounded, off-line

    idx = CorpusIndex("work/corpus-index")
    for occ in idx.occurrences(TOMO):
        print(occ.tier, occ.source.corpus, occ.context, [r.iiif_region() for r in occ.rects])

Evidence rules that the rest of the atlas depends on:

* A text hit is never a crop. ``Occurrence.tier`` says ``page_text`` / ``line_text`` /
  ``line_rect``, and ``has_crop`` is true only for a real, re-fetchable rectangle.
* An arithmetic rectangle (``basis="derived_char_index"``) is advisory and is refused
  by the API's thumbnail path. An evenly divided line box is not a glyph crop.
* ``confirmed`` is true only when a *human* review event covers that rectangle.
  Machine localisation and AI visual inspection both stay ``confirmed=False``.

Each name is imported on first access rather than at package import. The
collection queues need the occurrence layer and each other and nothing else; an
eager import here would drag every subpackage into every collector run.
"""

from __future__ import annotations

import importlib
from typing import Any

__all__ = [
    "CHARS_FILE",
    "DEFAULT_ROOT",
    "INDEX_DIR",
    "OCC_DIR",
    "TOMO",
    "Corpus",
    "CorpusAPI",
    "CorpusIndex",
    "CorpusReview",
    "GlyphEntry",
    "GlyphRegistry",
    "IndexStats",
    "Occurrence",
    "Rect",
    "Router",
    "Source",
    "advisory_char_rect",
    "build_chars",
    "build_glyph_registry",
    "build_occurrences",
    "build_query",
    "classify",
    "codepoint",
    "corpus_api",
    "corpus_router",
    "deduplicate",
    "discover",
    "find_occurrences",
]

#: Where each name is defined, as `name -> module` or `name -> (module, attribute)`.
_SOURCES: dict[str, str | tuple[str, str]] = {
    "CHARS_FILE": ".index",
    "DEFAULT_ROOT": ".sources",
    "INDEX_DIR": ".index",
    "OCC_DIR": ".index",
    "TOMO": ".occurrence",
    "Corpus": ".sources",
    "CorpusAPI": ".api",
    "CorpusIndex": ".index",
    "CorpusReview": ".review",
    "GlyphEntry": ".glyphs",
    "GlyphRegistry": ".glyphs",
    "IndexStats": ".index",
    "Occurrence": ".occurrence",
    "Rect": ".occurrence",
    "Router": ".api",
    "Source": ".occurrence",
    "advisory_char_rect": ".occurrence",
    "build_chars": ".index",
    "build_glyph_registry": (".glyphs", "build"),
    "build_occurrences": ".index",
    "build_query": ".fastapi_router",
    "classify": ".occurrence",
    "codepoint": ".occurrence",
    "corpus_api": ".fastapi_router",
    "corpus_router": ".fastapi_router",
    "deduplicate": ".occurrence",
    "discover": ".sources",
    "find_occurrences": ".occurrence",
}


def __getattr__(name: str) -> Any:
    where = _SOURCES.get(name)
    if where is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute = where if isinstance(where, tuple) else (where, name)
    try:
        module = importlib.import_module(module_name, __name__)
    except ModuleNotFoundError as error:
        raise ImportError(
            f"{__name__}.{name} is defined in {module_name.lstrip('.')}, "
            "which this repository does not hold yet"
        ) from error
    return getattr(module, attribute)


def __dir__() -> list[str]:
    return sorted(__all__)
