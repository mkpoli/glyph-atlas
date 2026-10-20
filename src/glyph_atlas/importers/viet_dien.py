"""Import Việt Điển's page OCR through its public API.

Responses are cached by URL and fingerprinted in the output. Page numbers can have
holes; each page carries the upstream number, correction flag and citation.
The API supplies no character rectangles. Its image URLs are retained at their
stated resolution, with unknown dimensions until the image pipeline measures them.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode, urljoin

import httpx

from .. import net, tables
from ..schema import Document, Licence, Page, PageText, Rights

SOURCE = "viet-dien"
BASE = "https://viet-dien.com"
TERMS = f"{BASE}/data"
ATTRIBUTION = "Việt Điển, Albert Errickson; source collections: Digitizing Vietnam and holding libraries"
REUSE = "The OCR text and the catalogue compilation are offered for research use with attribution."


class API:
    """A replayable API snapshot with the upstream's five-second crawl interval."""

    def __init__(self, cache: Path, *, base: str = BASE, client: httpx.Client | None = None):
        self.cache = Path(cache)
        self.base = base.rstrip("/")
        self.client = client
        self.responses: dict[str, str] = {}

    def get(self, path: str, **params: Any) -> tuple[dict, str]:
        url = f"{self.base}/api/v1/{path}"
        if params:
            url += "?" + urlencode(params)
        target = self.cache / f"{hashlib.sha256(url.encode()).hexdigest()}.json"
        net.download(url, target, pause=5.0, expected="json", client=self.client)
        body = target.read_bytes()
        payload = json.loads(body)
        if not isinstance(payload, dict):
            raise TypeError(f"{url}: expected a JSON object")
        digest = hashlib.sha256(body).hexdigest()
        self.responses[url] = digest
        return payload, digest

    def texts(self, limit: int) -> list[dict]:
        """Read the catalogue in upstream order; zero means all texts."""
        result: list[dict] = []
        seen: set[int] = set()
        total = None
        while total is None or len(result) < total:
            count = min(100, limit - len(result)) if limit else 100
            payload, _ = self.get("texts", limit=count, offset=len(result))
            if total is not None and payload["total"] != total:
                raise ValueError("Việt Điển catalogue total changed during pagination; use a new cache")
            total = payload["total"]
            rows = payload["texts"]
            if not rows and len(result) < total:
                raise ValueError("Việt Điển catalogue ended before its advertised total")
            for row in rows:
                if row["id"] in seen:
                    raise ValueError("Việt Điển catalogue repeated a text; use a new cache")
                seen.add(row["id"])
                result.append(row)
            if limit and len(result) >= limit:
                return result[:limit]
        if len(result) != total:
            raise ValueError("Việt Điển catalogue count differs from its advertised total")
        return result


def records(detail: dict, listing: dict, pages: list[tuple[dict, str]], revision: str):
    """Convert a work without inferring dates, origin, geometry or scan permissions."""
    item = detail.get("item_id")
    key = quote(item, safe="") if isinstance(item, str) and item.strip() else None
    identity = key if key else f"api:{detail['id']}"
    document_id = f"{SOURCE}:{identity}"
    citation = f"{BASE}/t/{key}" if key else f"{BASE}/read/{detail['id']}"
    viewer = citation if key else urljoin(BASE + "/", detail.get("viewer_url") or f"{citation}/{min(detail['page_numbers'], default=1)}")
    refs = {"viet-dien-api-id": str(detail["id"]), "viewer": viewer}
    if key:
        refs[SOURCE] = item
    for field, name in (("source_url", "source"), ("manifest_url", "iiif-manifest")):
        if detail.get(field):
            refs[name] = detail[field]
    document = Document(
        id=document_id,
        title=detail.get("title_han") or detail["title"],
        source_refs=refs,
        text_rights=Rights(licence=Licence.RESTRICTED, attribution=ATTRIBUTION, evidence=TERMS),
        meta={"viet_dien": {**listing, **detail}, "revision": revision,
              "text_reuse_terms": REUSE, "image_reuse_terms": "Follow the holding library's terms.",
              "title_romanized": detail["title"], "identity_basis": "item_id" if key else "api_id"},
    )
    page_records, texts = [], []
    for row, digest in pages:
        number = row["page_number"]
        if row["text_id"] != detail["id"] or number not in detail["page_numbers"]:
            raise ValueError("Việt Điển page identity does not match its text")
        if type(row.get("corrected")) is not bool:
            raise ValueError("Việt Điển page has no boolean correction status")
        page_id = f"{document_id}:{number}"
        page_records.append(Page(
            id=page_id, document_id=document_id, seq=number - 1,
            image=urljoin(BASE + "/", row["image_url"]) if row.get("image_url") else "", width=0, height=0,
            transcription={"source": SOURCE, "entry": item if key else str(detail["id"]), "revision": digest},
            meta={"viewer_url": f"{citation}/{number}", "upstream_viewer_url": row.get("viewer_url"),
                  "source_url": row.get("source_url"), "page_number": number,
                  "corrected": row["corrected"], "text_status": "corrected" if row["corrected"] else "ocr"},
        ))
        texts.append(PageText(page_id=page_id, source=SOURCE, revision=digest, text_raw=row["raw_text"]))
    return document, page_records, texts


def import_all(
    out: Path, *, cache: Path = Path("cache/viet-dien"), text_ids: list[int] | None = None,
    limit: int = 1, page_limit: int = 10, base: str = BASE, client: httpx.Client | None = None,
) -> dict[str, int]:
    """Write one selected snapshot; zero limits select all works/pages.

    Explicit text ids override the work limit. Rerunning uses the cached responses;
    a new cache directory selects a new snapshot. Output tables replace the previous
    selection, so separate samples should use separate output directories.
    """
    if limit < 0 or page_limit < 0 or any(type(i) is not int or i < 1 for i in (text_ids or [])):
        raise ValueError("limits must be nonnegative and text ids must be positive integers")
    api = API(cache, base=base, client=client)
    if text_ids:
        catalogue = {row["id"]: row for row in api.texts(0)}
        missing = set(text_ids) - catalogue.keys()
        if missing:
            raise ValueError(f"Việt Điển catalogue lacks text ids {sorted(missing)}; use a new cache if it is stale")
        selected = [catalogue[i] for i in dict.fromkeys(text_ids)]
    else:
        selected = api.texts(limit)
    documents, pages, texts = [], [], []
    expected_pages = 0
    for listing in selected:
        detail, revision = api.get(f"texts/{listing['id']}")
        if detail["id"] != listing["id"]:
            raise ValueError("Việt Điển returned a different text id")
        numbers = detail["page_numbers"]
        if (any(type(n) is not int or n < 1 for n in numbers)
                or len(set(numbers)) != len(numbers) or len(numbers) != detail["page_count"]):
            raise ValueError("Việt Điển page list differs from its advertised count or has invalid numbers")
        expected_pages += len(numbers)
        rows = []
        for number in sorted(numbers)[:page_limit or None]:
            row, digest = api.get(f"texts/{detail['id']}/pages/{number}")
            if row["page_number"] != number:
                raise ValueError("Việt Điển returned a different page number")
            rows.append((row, digest))
        document, work_pages, work_texts = records(detail, listing, rows, revision)
        documents.append(document)
        pages.extend(work_pages)
        texts.extend(work_texts)
    if len({d.id for d in documents}) != len(documents):
        raise ValueError("Việt Điển texts share a stable item_id")
    command = "atlas import viet-dien"
    if text_ids:
        command += "".join(f" --text-id {i}" for i in dict.fromkeys(text_ids))
    command += f" --limit {limit} --page-limit {page_limit}"
    snapshot = {"source": SOURCE, "terms_url": TERMS, "text_reuse_terms": REUSE,
                "responses": api.responses, "expected_pages": expected_pages,
                "imported_pages": len(pages), "page_limited": len(pages) < expected_pages}
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with tables.locked(out), tempfile.TemporaryDirectory(prefix=f".{out.name}-", dir=out.parent) as scratch:
        stage = Path(scratch)
        # An importer must not replace a derived corpus or another source's documents.
        if out.is_symlink():
            raise ValueError("output is a symlink; select a separate output directory")
        existing = tables.Dataset(out)
        if any(path for name, path in existing.tables.items() if name not in {"documents", "pages", "page_texts"}):
            raise ValueError("output contains derived tables; select a separate output directory")
        if existing.tables["documents"] and any(not d.id.startswith(SOURCE + ":") for d in existing.read("documents")):
            raise ValueError("output contains another source; select a separate output directory")
        allowed = {"MANIFEST.json", "UPSTREAM.json"}
        for name in ("documents", "pages", "page_texts"):
            allowed.update({f"{name}.parquet", f".{name}.parquet.lock"})
        if out.exists() and any(p.name not in allowed for p in out.iterdir()):
            raise ValueError("output contains unrelated files; select a separate output directory")
        for name, values, model in (("documents", documents, Document), ("pages", pages, Page),
                                    ("page_texts", texts, PageText)):
            tables.write(stage / f"{name}.parquet", values, model)
        counts = tables.Dataset(stage).merge([], stage, command=command)
        (stage / "UPSTREAM.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        problems = tables.Dataset(stage).validate()
        if problems:
            raise ValueError("; ".join(problems))
        backup = stage.with_name(stage.name + "-previous")
        if out.exists():
            out.replace(backup)
        try:
            stage.replace(out)
        except OSError:
            if backup.exists():
                backup.replace(out)
            raise
        if backup.exists():
            shutil.rmtree(backup)
    return counts
