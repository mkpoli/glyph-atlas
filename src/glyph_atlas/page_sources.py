"""Where a page of the atlas can be opened at its source: its book's IIIF manifest, the canvas the
page is in that manifest, and the holder's own catalogue record of the book.

The manifest is the one the document's importer recorded (`source_refs["iiif-manifest"]`), read from
the manifest cache (`cache/manifests/<sha256 of url>.json`) without fetching anything; a manifest the
cache does not hold was never read and is not offered. The page is
placed in it by the canvas its importer recorded or, failing that, by the canvas whose image service
is the page's own; a page found in neither way keeps the manifest and has no canvas, since a page
number is no proof of a canvas position.

The catalogue record comes, in this order, from the record the importer read (`source_refs
["catalogue"]`, `meta.kokusho.record_url`), the manifest's `related` page, or a manifest metadata
entry labelled URL or Source that holds one link. A link the manifests of two different books both
give is a page of the holder's site and no record of either book, so `catalogue_urls` drops it.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from .importers.honkoku_data import canvas_page, canvases, identifier, manifest_path

MANIFEST_REF = "iiif-manifest"
#: Labels of manifest metadata entries whose single link is the item's record.
RECORD_LABELS = frozenset({"url", "source", "ソース"})
LINK = re.compile(r"https?://[^\s\"'<>]+")


def manifest_url(source_refs: Mapping[str, Any] | None) -> str | None:
    url = (source_refs or {}).get(MANIFEST_REF)
    return url if isinstance(url, str) and url.startswith(("https://", "http://")) else None


def read_manifest(url: str | None, cache: Path | None = None) -> dict | None:
    """The cached manifest of `url`, or None when it is not cached or is no manifest."""
    if not url:
        return None
    path = manifest_path(url, cache)
    try:
        found = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    return found if isinstance(found, dict) else None


def locate(manifest: dict | None, canvas: str | None, service: str | None) -> tuple[int, str] | None:
    """The 0-based position and id of the page's canvas in `manifest`: the canvas its importer
    recorded, else the one whose image service is the page's."""
    if not manifest:
        return None
    pages = [canvas_page(node)[:2] for node in canvases(manifest)]
    if canvas:
        for index, (found, _) in enumerate(pages):
            if found == canvas:
                return index, found
    wanted = (service or "").rstrip("/")
    if wanted:
        matches = [(index, found) for index, (found, image) in enumerate(pages) if found and image.rstrip("/") == wanted]
        if len(matches) == 1:
            return matches[0]
    return None


def _text(value: Any) -> list[str]:
    """Every string of a IIIF label or value, in Presentation 2 or 3 form."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [text for item in value for text in _text(item)]
    if isinstance(value, dict):
        if "@value" in value:
            return _text(value["@value"])
        return [text for item in value.values() for text in _text(item)]
    return []


def _html(node: Any) -> bool:
    if isinstance(node, dict):
        kind = node.get("format")
        return kind is None or str(kind).lower().startswith("text/html")
    return isinstance(node, str)


def manifest_record(manifest: dict | None) -> str | None:
    """The record a manifest links its item to: its `related` web page, or the one link of a
    metadata entry labelled URL or Source."""
    if not manifest:
        return None
    related = manifest.get("related")
    for node in related if isinstance(related, list) else [related]:
        url = identifier(node) if _html(node) else None
        if url and url.startswith(("https://", "http://")):
            return url
    for entry in manifest.get("metadata") or []:
        if not isinstance(entry, dict) or not {label.strip().casefold() for label in _text(entry.get("label"))} & RECORD_LABELS:
            continue
        links = {link.rstrip(".,)") for text in _text(entry.get("value")) for link in LINK.findall(text)}
        if len(links) == 1:
            return links.pop()
    return None


def stated_record(source_refs: Mapping[str, Any] | None, meta: Mapping[str, Any] | None) -> str | None:
    """The catalogue record the document's importer read from the holder."""
    for url in ((source_refs or {}).get("catalogue"), ((meta or {}).get("kokusho") or {}).get("record_url")):
        if isinstance(url, str) and url.startswith(("https://", "http://")):
            return url
    return None


def catalogue_urls(documents: Iterable[tuple[str, Mapping[str, Any] | None, Mapping[str, Any] | None]],
                   cache: Path | None = None) -> dict[str, str]:
    """The catalogue record of each document `(id, source_refs, meta)` that has one."""
    stated, found, books = {}, {}, defaultdict(set)
    for identity, refs, meta in documents:
        if url := stated_record(refs, meta):
            stated[identity] = url
            continue
        manifest = manifest_url(refs)
        if url := manifest_record(read_manifest(manifest, cache)):
            found[identity] = url
            books[url].add(manifest)
    return stated | {identity: url for identity, url in found.items() if len(books[url]) == 1}


def page_sources(source_refs: Mapping[str, Any] | None, canvas: str | None, service: str | None,
                 catalogue: str | None = None, cache: Path | None = None) -> dict[str, Any]:
    """What a page record says of where the page can be opened: `manifest`, `canvas` and its 0-based
    `canvas_index` in the manifest, and the book's `catalogue_url`."""
    manifest = manifest_url(source_refs)
    read = read_manifest(manifest, cache)
    placed = locate(read, canvas, service)
    return {"manifest": manifest if read else None, "canvas": placed[1] if placed else canvas,
            "canvas_index": placed[0] if placed else None, "catalogue_url": catalogue}
