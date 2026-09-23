"""Corpus-wide occurrence discovery and indexing.

The atlas already holds several broad corpora that are not character-segmented —
1.17M transcribed Honkoku lines, 246k page transcriptions, a million CODH units —
and none of them were reachable from the gallery, because the review layer only
knows about *units* that have a cached crop image. This package adds the missing
layer: a character **occurrence** in a source text, with an honest statement of what
is and is not located, plus a bounded index that makes the whole corpus searchable
without loading it.

Quick start::

    from kuzushiji_atlas.corpus import CorpusIndex, build_chars, build_occurrences, TOMO

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
"""

from .api import CorpusAPI, Router
from .fastapi_router import build_query, corpus_api, corpus_router
from .glyphs import GlyphEntry, GlyphRegistry
from .glyphs import build as build_glyph_registry
from .index import (
    CHARS_FILE,
    INDEX_DIR,
    OCC_DIR,
    CorpusIndex,
    IndexStats,
    build_chars,
    build_occurrences,
)
from .occurrence import (
    TOMO,
    Occurrence,
    Rect,
    Source,
    advisory_char_rect,
    classify,
    codepoint,
    deduplicate,
    find_occurrences,
)
from .review import CorpusReview
from .sources import DEFAULT_ROOT, Corpus, discover

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
