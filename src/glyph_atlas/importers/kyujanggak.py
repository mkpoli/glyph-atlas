"""Collect pinned Kyujanggak land-register scans in the viewer's order."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlencode
from uuid import uuid4

import httpx
import yaml

from .. import images, net, tables
from ..schema import Dating, Document, Licence, Page, Rights
from .honkoku_queue import publish_directory

SOURCE = "kyujanggak"
HOLDER = "서울대학교 규장각한국학연구원"
TERMS = "https://kyu.snu.ac.kr/news/faq/"
IMAGE_LIST = "https://kyudb.snu.ac.kr/pf01/viewImgList.do"
IMAGE = "https://kyudb.snu.ac.kr/ImageServlet.do"


def image_list(payload: bytes, book: dict) -> list[dict]:
    """Validate the pinned viewer list, retaining inserted leaves in source order."""
    if len(payload) != book["image_list_bytes"] or hashlib.sha256(payload).hexdigest() != book["image_list_sha256"]:
        raise ValueError(f"Image list snapshot mismatch: {book['book_cd']}")
    rows = json.loads(payload)["list"]
    if len(rows) != book["pages"]:
        raise ValueError(f"Unexpected image count: {book['book_cd']}")
    seen = set()
    pattern = re.compile(re.escape(book["book_cd"]) + r"_IL_" + re.escape(book["vol_no"]) + r"_[0-9]{3}[ab](?:_[0-9]+)?\.jpg")
    for row in rows:
        filename = row.get("FILE_NM", "")
        if (row.get("BOOK_CD") != book["book_cd"] or row.get("ITEM_CD") != book["item_cd"]
                or not pattern.fullmatch(filename) or filename in seen):
            raise ValueError(f"Invalid or duplicate image entry: {filename}")
        seen.add(filename)
    return rows


def build(book: dict, rows: list[dict], held: list[images.ImageRecord]) -> tuple[Document, list[Page]]:
    doc_id = f"{SOURCE}:{book['book_cd']}:{book['vol_no']}"
    document = Document(
        id=doc_id, title=book["title"], source_refs={SOURCE: book["book_cd"], "catalogue": book["catalogue"]},
        holder=HOLDER, shelfmark=book["shelfmark"], production="handwritten", origin="korea",
        dating=[Dating(literal=book["date_literal"], start=book["date"], end=book["date"],
                       kind="copying", evidence=book["catalogue"])],
        image_rights=Rights(licence=Licence.BESPOKE_FREE, holder=HOLDER, attribution=HOLDER,
                            evidence=TERMS, checked=datetime.now(UTC).date()),
        meta={"collection": SOURCE, "volume": book["vol_no"], "image_list_sha256": book["image_list_sha256"]},
    )
    pages = [Page(id=f"{doc_id}:{row['FILE_NM']}", document_id=doc_id, seq=seq,
                  image=image.url, width=image.width, height=image.height, sha256=image.sha256,
                  meta={"page_label": row["DISP_PAGE"], "inserted": row["ADD_PAGE_YN"] == "Y",
                        "filename": row["FILE_NM"]})
             for seq, (row, image) in enumerate(zip(rows, held, strict=True))]
    return document, pages


def collect(source: Path, out: Path, cache: Path, image_cache: Path | None = None) -> dict:
    config = yaml.safe_load(source.read_text(encoding="utf-8"))
    if out.exists() and not out.is_symlink():
        raise ValueError("Output must be absent or a snapshot symlink")
    if any((out / name).exists() for name in ("review.sqlite", "reviews.jsonl")):
        raise ValueError("Output has review data; collect into a new directory to preserve it")
    documents, pages = [], []
    with httpx.Client(timeout=60, follow_redirects=True) as client:
        for book in config["collections"]:
            list_path = net.download(IMAGE_LIST, cache / (book["image_list_sha256"] + ".json"),
                                     method="POST", data={k: book[k] for k in ("item_cd", "book_cd", "vol_no")},
                                     expected="json", client=client)
            rows = image_list(list_path.read_bytes(), book)
            held = []
            for row in rows:
                filename = row["FILE_NM"]
                url = IMAGE + "?" + urlencode({"imgFileNm": filename, "path": book["image_path"] + filename})
                held.append(images.fetch(url, client=client, root=image_cache))
            doc, scans = build(book, rows, held)
            documents.append(doc)
            pages.extend(scans)
    summary = {"documents": len(documents), "pages": len(pages), "inserted_pages": sum(p.meta["inserted"] for p in pages),
               "page_texts": 0, "character_crops": 0}
    versions = out.parent / f".{out.name}-versions"
    versions.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".staging-", dir=versions) as tmp:
        staging = Path(tmp)
        for name, records, model in (("documents", documents, Document), ("pages", pages, Page)):
            tables.write(staging / f"{name}.parquet", records, model)
        errors = tables.Dataset(staging).validate()
        if errors:
            raise ValueError("Invalid import: " + "; ".join(errors[:10]))
        tables.Dataset(staging).merge([], staging, command="collect kyujanggak")
        manifest_path = staging / "MANIFEST.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["collection"] = summary
        manifest["source_config_sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        with tables.locked(out):
            if any((out / name).exists() for name in ("review.sqlite", "reviews.jsonl")):
                raise ValueError("Output has review data; collect into a new directory to preserve it")
            generation = versions / uuid4().hex
            publish_directory(staging, generation)
            link = out.with_name(f".{out.name}-{uuid4().hex}.next")
            try:
                link.symlink_to(generation.relative_to(out.parent), target_is_directory=True)
                os.replace(link, out)
            except BaseException:
                shutil.rmtree(generation, ignore_errors=True)
                raise
            finally:
                link.unlink(missing_ok=True)
    return summary
