"""Collect the Western Xia coin images from Andrew West's "Tangut Coins".

The article prints a photograph of a specimen for four coin types and an editorial drawing of the
inscription for all six. Both kinds are images of three-dimensional coins: a photograph has its own
rights whatever the age of the coin, and a drawing is a modern work. The documents therefore take
`image_kind` `object-photograph`, `editorial-diagram` or `mixed`, not `flat-reproduction`, so the
public-domain reproduction rule leaves their stated terms alone (`docs/licensing.md`).

One document is one coin type, with its images as pages in the article's order. Each page records its
own `image_kind`; the document records the aggregate, and `mixed` when its pages differ. The readings
and the reported reign periods are quoted from the article; the classifications there are disputed
ones and stay in `meta` as reported.

The images are fetched into `cache/images` and the pages record the fetched checksum, width and
height, which must equal the values the source record pins. Any failed fetch or changed image stops
the import before a table is written, so a dataset is never replaced by a partial one.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from .. import images, net, registry, tables
from ..schema import Document, Licence, Page, Rights

SOURCE_ID = "babelstone-tangut-coins"
DEFAULT_SOURCE = registry.SOURCES / "babelstone-tangut-coins.yaml"
COMMAND = "atlas import tangut-coins"


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
    for coin in coins:
        coin_images = [image for image in all_images if image.get("coin") == coin["id"]]
        if not coin_images:
            raise CoinSourceError(f"{source}: {coin['id']} has no image")
        held: dict[str, images.ImageRecord] = {}
        for image in coin_images:
            try:
                record = fetch(image["image"], root=cache)
            except (net.DownloadError, images.ImageError) as error:
                raise CoinSourceError(f"{source}: {image['id']}: {error}") from error
            _check_pin(source, image, record)
            held[image["id"]] = record
        document = _document(config, coin, coin_images)
        documents.append(document)
        pages.extend(_page(document, image, held[image["id"]], seq)
                     for seq, image in enumerate(coin_images))

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
        "photographs": sum(1 for page in pages if page.image_kind == "object-photograph"),
        "diagrams": sum(1 for page in pages if page.image_kind == "editorial-diagram"),
        "coins": [{"id": document.id, "title": document.title, "status": document.meta.get("status"),
                   "kind": document.image_kind,
                   "pages": sum(1 for page in pages if page.document_id == document.id)}
                  for document in documents],
    }
    manifest = {
        "schema_version": tables.SCHEMA_VERSION,
        "tables": counts,
        "command": command or COMMAND,
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


def _checked(config: dict[str, Any]) -> date | None:
    """The date the source was read, as YAML parses it: an ISO string or a date."""
    value = config.get("checked")
    if isinstance(value, date):
        return value
    return date.fromisoformat(value) if isinstance(value, str) else None


def _rights(config: dict[str, Any]) -> Rights:
    return Rights(
        licence=Licence(config["licence"]),
        attribution=config["attribution"],
        evidence=config.get("licence_evidence") or config["url"],
        checked=_checked(config),
    )


def _document(config: dict[str, Any], coin: dict[str, Any],
              coin_images: list[dict[str, Any]]) -> Document:
    kinds = [image["kind"] for image in coin_images]
    kind = kinds[0] if len(set(kinds)) == 1 else "mixed"
    rights = _rights(config)
    meta = {
        "source": SOURCE_ID,
        "article": config["url"],
        "article_sha256": config.get("article_sha256"),
        "article_sha256_note": "Read by hand on the source's checked date; the import does not refetch the article.",
        "reign_period_as_reported": coin.get("reign_period_as_reported"),
        "status": coin.get("status"),
        "note": coin.get("note"),
        "example_url": coin.get("example_url"),
        "readings": coin.get("readings") or [],
        "reading_order": config.get("reading_order"),
        "reading_system": config.get("reading_system"),
        "source_notes": config.get("notes") or [],
        "images": [{"id": image["id"], "kind": image["kind"], "label": image.get("label"),
                    "page": f"{create_id(coin)}:{image['id']}",
                    "linked_from_image": image.get("linked_from_image"),
                    "reading_index": image.get("reading_index")}
                   for image in coin_images],
        "rights_note": "No reuse licence or photographer credit is stated in the article.",
    }
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
        image_kind=image["kind"],
        meta=meta,
    )
