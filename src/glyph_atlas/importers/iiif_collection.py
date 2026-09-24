"""Collect an explicitly listed IIIF book set without inventing transcriptions."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import yaml

from .. import net, tables
from ..schema import Document, Licence, Page, Rights
from .honkoku_data import canvas_page, canvases, fetch_manifest


def collect(source: Path, out: Path, *, cache: Path | None = None) -> dict:
    config = yaml.safe_load(source.read_text(encoding="utf-8"))
    out.mkdir(parents=True, exist_ok=True)
    documents, pages, unavailable = [], [], []
    for book in config["books"]:
        try:
            manifest = fetch_manifest(book["manifest"], cache=cache)
        except net.DownloadError as error:
            unavailable.append({"id": book["id"], "title": book["title"], "error": str(error)})
            continue
        meta = {str(v.get("label")): v.get("value") for v in manifest.get("metadata", [])}
        licence = Licence.PDM if meta.get("Access Restrictions") == "PDM" else Licence.UNKNOWN
        document = Document(
            id=book["id"], title=str(manifest.get("label") or book["title"]),
            holder=config["holder"],
            source_refs={"iiif-manifest": book["manifest"], "catalogue": book["source_url"]},
            image_rights=Rights(licence=licence, holder=config["holder"],
                                attribution=str(manifest.get("attribution") or config["holder"]),
                                evidence=book["manifest"]),
            meta={"collection": config["id"], "catalogue": meta,
                  "manifest_sha256": hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest(),
                  "extraction_status": "pending"},
        )
        documents.append(document)
        for seq, canvas in enumerate(canvases(manifest), 1):
            canvas_id, image, width, height = canvas_page(canvas)
            pages.append(Page(id=f"{document.id}:{seq}", document_id=document.id, seq=seq,
                              canvas=canvas_id, image=image, width=width, height=height))
    # Only complete tables become visible. A rerun reuses the manifest cache.
    for name, records, model in [("documents", documents, Document), ("pages", pages, Page)]:
        staging = out / f".{name}.parquet"
        tables.write(staging, records, model)
        os.replace(staging, out / f"{name}.parquet")
    summary = {"title": config["name"], "volumes": len(documents), "pages": len(pages),
               "source_url": config["url"], "stage": "scan references collected",
               "transcribed_pages": 0, "character_crops": 0,
               "unavailable": unavailable}
    payload = {"schema_version": tables.SCHEMA_VERSION, "tables": {"documents": len(documents), "pages": len(pages)},
               "command": "collect IIIF book manifests", "collection": summary,
               "references": config.get("references", [])}
    staging = out / ".MANIFEST.json"
    staging.write_text(json.dumps(payload, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    os.replace(staging, out / "MANIFEST.json")
    return summary
