"""Collect the Western Xia coin images from Andrew West's "Tangut Coins".

The article prints a photograph of a specimen for four coin types and an editorial drawing of the
inscription for all six. Both kinds are images of three-dimensional coins: a photograph has its own
rights whatever the age of the coin, and a drawing is a modern work. The documents therefore take
`image_kind` `object-photograph` or `editorial-diagram`, not `flat-reproduction`, so the public-domain
reproduction rule leaves their stated terms alone (`docs/licensing.md`).

One document is one coin type, with its images as pages in the article's order. A type whose images
include a photograph takes `object-photograph`; a type known only from a drawing takes
`editorial-diagram`. Each page's `meta` keeps its own kind, label and the reading the drawing sets.
The readings and the reported reign periods are quoted from the article; the classifications there
are disputed ones and stay in `meta` as reported.

The images are fetched into `cache/images` and the pages record the fetched checksum, width and
height, which must equal the values the source record pins; a changed image stops the import.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from .. import images, net, tables
from ..schema import Document, Licence, Page, Rights

SOURCE_ID = "babelstone-tangut-coins"
DEFAULT_SOURCE = Path("data/sources/babelstone-tangut-coins.yaml")
ATTRIBUTION = "Andrew West, “Tangut Coins”, BabelStone Blog, 29 January 2007 (revised 1 January 2017)"
CHECKED = date(2026, 11, 7)


class CoinSourceError(RuntimeError):
    """A source record or an image that does not match what the import expects."""


def collect(
    source: Path = DEFAULT_SOURCE,
    out: Path = Path("work/tangut-coins"),
    *,
    cache: Path | None = None,
    fetch: Callable[..., images.ImageRecord] = images.fetch,
    command: str | None = None,
) -> dict[str, Any]:
    """Fetch the coin images and write the documents and pages of the dataset."""
    source = Path(source)
    config = yaml.safe_load(source.read_text(encoding="utf-8"))
    if not isinstance(config, dict) or config.get("id") != SOURCE_ID:
        raise CoinSourceError(f"{source}: not the {SOURCE_ID} source record")
    coins, all_images = config.get("coins") or [], config.get("images") or []
    if not coins or not all_images:
        raise CoinSourceError(f"{source}: no coins or images")

    documents: list[Document] = []
    pages: list[Page] = []
    unavailable: list[dict[str, str]] = []
    for coin in coins:
        coin_images = [image for image in all_images if image.get("coin") == coin["id"]]
        if not coin_images:
            raise CoinSourceError(f"{source}: {coin['id']} has no image")
        held: list[tuple[dict[str, Any], images.ImageRecord]] = []
        for image in coin_images:
            try:
                record = fetch(image["image"], root=cache)
            except (net.DownloadError, images.ImageError) as error:
                unavailable.append({"image": image["id"], "error": str(error)})
                continue
            _check_pin(source, image, record)
            held.append((image, record))
        if not held:
            continue
        document = _document(config, coin, held)
        documents.append(document)
        pages.extend(_page(document, image, record, seq) for seq, (image, record) in enumerate(held))

    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    counts = {"documents": len(documents), "pages": len(pages)}
    for name, records, model in (("documents", documents, Document), ("pages", pages, Page)):
        staging = out / f".{name}.parquet"
        tables.write(staging, records, model)
        os.replace(staging, out / f"{name}.parquet")
    summary = {
        "source": SOURCE_ID,
        "article": config["url"],
        "article_sha256": config.get("article_sha256"),
        "documents": len(documents),
        "pages": len(pages),
        "photographs": sum(1 for page in pages if page.meta.get("kind") == "object-photograph"),
        "diagrams": sum(1 for page in pages if page.meta.get("kind") == "editorial-diagram"),
        "coins": [{"id": document.id, "title": document.title, "status": document.meta.get("status"),
                   "pages": sum(1 for page in pages if page.document_id == document.id)}
                  for document in documents],
        "unavailable": unavailable,
    }
    manifest = {
        "schema_version": tables.SCHEMA_VERSION,
        "tables": counts,
        "command": command or "collect the Tangut coin images",
        "collection": summary,
    }
    staging = out / ".MANIFEST.json"
    staging.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(staging, out / "MANIFEST.json")
    return summary


def create_id(coin: dict[str, Any]) -> str:
    """A coin type's document id, from the source and the type it names."""
    return f"{SOURCE_ID}:{coin['id']}"


def _check_pin(source: Path, image: dict[str, Any], record: images.ImageRecord) -> None:
    """The fetched file must be the one the source record pins."""
    expected = (image.get("sha256"), image.get("width"), image.get("height"), image.get("bytes"))
    found = (record.sha256, record.width, record.height, record.bytes)
    if expected != found:
        raise CoinSourceError(
            f"{source}: {image['id']} is not the image the record pins: pinned {expected}, fetched {found}"
        )


def _document(config: dict[str, Any], coin: dict[str, Any],
              held: list[tuple[dict[str, Any], images.ImageRecord]]) -> Document:
    kinds = [image["kind"] for image, _ in held]
    kind = "object-photograph" if "object-photograph" in kinds else "editorial-diagram"
    meta = {
        "source": SOURCE_ID,
        "article": config["url"],
        "article_sha256": config.get("article_sha256"),
        "reign_period_as_reported": coin.get("reign_period_as_reported"),
        "status": coin.get("status"),
        "note": coin.get("note"),
        "readings": coin.get("readings") or [],
        "images": [{"id": image["id"], "kind": image["kind"], "label": image.get("label"),
                    "linked_from_image": image.get("linked_from_image"),
                    "reading_index": image.get("reading_index")}
                   for image, _ in held],
        "rights_note": "No reuse licence or photographer credit is stated in the article.",
    }
    rights = Rights(licence=Licence.UNKNOWN, attribution=ATTRIBUTION, evidence=config["url"], checked=CHECKED)
    return Document(
        id=create_id(coin),
        title=coin.get("title") or coin.get("label") or coin["id"],
        source_refs={SOURCE_ID: coin["id"]},
        production="inscribed/metal",
        origin="china",
        image_kind=kind,
        image_rights=rights,
        text_rights=rights.model_copy(),
        meta=meta,
    )


def _page(document: Document, image: dict[str, Any], record: images.ImageRecord, seq: int) -> Page:
    meta = {key: value for key, value in {
        "kind": image["kind"],
        "label": image.get("label"),
        "linked_from_image": image.get("linked_from_image"),
        "reading_index": image.get("reading_index"),
    }.items() if value is not None}
    return Page(
        id=f"{document.id}:{image['id']}",
        document_id=document.id,
        seq=seq,
        image=image["image"],
        width=record.width,
        height=record.height,
        sha256=record.sha256,
        meta=meta,
    )
