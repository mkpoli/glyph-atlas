"""Import Việt Điển's page OCR through its public API.

Responses are cached by URL and fingerprinted in the output. Page numbers can have
holes; each page carries the upstream number and citation. The per-page API also
supplies correction flags and scan URLs; whole-book exports leave these unknown.
The API supplies no character rectangles. Its image URLs are retained at their
stated resolution, with unknown dimensions until the image pipeline measures them.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urljoin

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

    def __init__(self, cache: Path, *, base: str = BASE, client: httpx.Client | None = None, offline: bool = False):
        self.cache = Path(cache)
        self.base = base.rstrip("/")
        self.client = client
        self.offline = offline
        self.responses: dict[str, str] = {}

    def get(self, path: str, **params: Any) -> tuple[dict, str]:
        body, digest = self.fetch(path, "json", **params)
        return json.loads(body), digest

    def fetch(self, path: str, kind: str, **params: Any) -> tuple[bytes, str]:
        """A cached response, fetched first unless offline. A response of the wrong shape is not kept."""
        url = f"{self.base}/api/v1/{path}"
        if params:
            url += "?" + urlencode(params)
        suffix = "json" if kind == "json" else "txt"
        target = self.cache / f"{hashlib.sha256(url.encode()).hexdigest()}.{suffix}"
        if self.offline and not target.exists():
            raise FileNotFoundError(f"{url} is not cached ({target}); run without --offline to fetch it")
        net.download(url, target, pause=5.0, expected=kind, client=self.client)
        body = target.read_bytes()
        problem = _shape_problem(body, kind)
        if problem:
            target.unlink()
            raise ValueError(f"{url}: {problem}; removed the cached response {target}")
        digest = hashlib.sha256(body).hexdigest()
        self.responses[url] = digest
        return body, digest

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


def _shape_problem(body: bytes, kind: str) -> str | None:
    """Why a response is not a JSON object or a full.txt export, or None."""
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return "not UTF-8"
    if kind == "json":
        try:
            payload = json.loads(text)
        except ValueError:
            return "not JSON"
        return None if isinstance(payload, dict) else "not a JSON object"
    return None if re.search(r"^# Việt Điển text [1-9][0-9]* ", text, re.MULTILINE) else "not a full.txt export"


def export_pages(body: bytes, detail: dict) -> list[dict]:
    """Split the full.txt format, preserving page text and empty pages.

    The exporter inserts one newline before each separator. The final page has
    no added newline. Page-level correction and image metadata are not supplied.
    """
    text = body.decode("utf-8")
    markers = list(re.finditer(r"^--- page ([1-9][0-9]*) ---\n", text, re.MULTILINE))
    numbers = [int(m[1]) for m in markers]
    if numbers != sorted(detail["page_numbers"]):
        raise ValueError("Việt Điển export page markers differ from the text's page list")
    header = text[:markers[0].start()] if markers else text
    expected = f"# Việt Điển text {detail['id']} — {detail['page_count']} pages"
    if expected not in header.splitlines():
        raise ValueError("Việt Điển export header differs from the text's identity/count")
    rows = []
    for index, marker in enumerate(markers):
        end = markers[index + 1].start() if index + 1 < len(markers) else len(text)
        raw = text[marker.end():end]
        if index + 1 < len(markers):
            if not raw.endswith("\n"):
                raise ValueError("Việt Điển export lacks a page separator newline")
            raw = raw[:-1]
        rows.append({"text_id": detail["id"], "page_number": int(marker[1]), "raw_text": raw})
    return rows


def records(detail: dict, listing: dict, pages: list[tuple[dict, str]], revision: str, *, bulk_text: bool = False):
    """Convert a work without inferring dates, origin, geometry or scan permissions."""
    item = detail.get("item_id")
    # Several volumes can share one holding-library item_id and page numbering.
    # Only the upstream text id identifies a particular volume and its viewer.
    document_id = f"{SOURCE}:api:{detail['id']}"
    citation = f"{BASE}/read/{detail['id']}"
    viewer = f"{citation}/{min(detail['page_numbers'], default=1)}"
    refs = {"viet-dien-api-id": str(detail["id"]), "viewer": viewer}
    if isinstance(item, str) and item.strip():
        refs[SOURCE] = item
    for field, name in (("source_url", "source"), ("manifest_url", "iiif-manifest")):
        if detail.get(field):
            refs[name] = detail[field]
    document = Document(
        id=document_id,
        # Volumes can share title_han (道教源流 in three volumes); the romanised title names the volume.
        title=" · ".join(dict.fromkeys(t for t in (detail.get("title_han"), detail.get("title")) if t)),
        source_refs=refs,
        text_rights=Rights(licence=Licence.RESTRICTED, attribution=ATTRIBUTION, evidence=TERMS),
        meta={"viet_dien": {**listing, **detail}, "revision": revision,
              "text_reuse_terms": REUSE, "image_reuse_terms": "Follow the holding library's terms.",
              "title_romanized": detail["title"], "identity_basis": "api_id"},
    )
    page_records, texts = [], []
    for row, digest in pages:
        number = row["page_number"]
        if row["text_id"] != detail["id"] or number not in detail["page_numbers"]:
            raise ValueError("Việt Điển page identity does not match its text")
        if not bulk_text and type(row.get("corrected")) is not bool:
            raise ValueError("Việt Điển page has no boolean correction status")
        page_id = f"{document_id}:{number}"
        page_records.append(Page(
            id=page_id, document_id=document_id, seq=number - 1,
            image=urljoin(BASE + "/", row["image_url"]) if row.get("image_url") else "", width=0, height=0,
            transcription={"source": SOURCE, "entry": str(detail["id"]), "revision": digest},
            meta={"viewer_url": f"{citation}/{number}", "upstream_viewer_url": row.get("viewer_url"),
                  "source_url": row.get("source_url"), "page_number": number,
                  "corrected": None if bulk_text else row["corrected"],
                  "text_status": "unknown" if bulk_text else ("corrected" if row["corrected"] else "ocr"),
                  "text_transport": "full.txt" if bulk_text else "page-api"},
        ))
        texts.append(PageText(page_id=page_id, source=SOURCE, revision=digest, text_raw=row["raw_text"]))
    return document, page_records, texts


def staging_dir(out: Path) -> Path:
    """Where a snapshot is staged: beside the corpus root, so a killed run leaves no second corpus in it.

    The staged folder is renamed into place, so it stays inside the root when the root's parent is
    on another filesystem; corpus discovery skips its dot-folder name there.
    """
    root = out.resolve().parent
    above = root.parent
    if above != root and os.access(above, os.W_OK) and os.stat(above).st_dev == os.stat(root).st_dev:
        return above
    return root


def import_all(
    out: Path, *, cache: Path = Path("cache/viet-dien"), text_ids: list[int] | None = None,
    limit: int = 1, page_limit: int = 10, base: str = BASE, client: httpx.Client | None = None,
    bulk_text: bool = False, offline: bool = False,
) -> dict[str, int]:
    """Write one selected snapshot; zero limits select all works/pages.

    Explicit text ids override the work limit. Rerunning uses the cached responses;
    a new cache directory selects a new snapshot. Output tables replace the previous
    selection, so separate samples should use separate output directories.
    """
    if limit < 0 or page_limit < 0 or any(type(i) is not int or i < 1 for i in (text_ids or [])):
        raise ValueError("limits must be nonnegative and text ids must be positive integers")
    api = API(cache, base=base, client=client, offline=offline)
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
        if bulk_text:
            body, digest = api.fetch(f"texts/{detail['id']}/full.txt", "text")
            rows = [(row, digest) for row in export_pages(body, detail)[:page_limit or None]]
        else:
            for number in sorted(numbers)[:page_limit or None]:
                row, digest = api.get(f"texts/{detail['id']}/pages/{number}")
                if row["page_number"] != number:
                    raise ValueError("Việt Điển returned a different page number")
                rows.append((row, digest))
        document, work_pages, work_texts = records(detail, listing, rows, revision, bulk_text=bulk_text)
        documents.append(document)
        pages.extend(work_pages)
        texts.extend(work_texts)
    if len({d.id for d in documents}) != len(documents):
        raise ValueError("Việt Điển texts share an API identity")
    command = "atlas import viet-dien"
    if text_ids:
        command += "".join(f" --text-id {i}" for i in dict.fromkeys(text_ids))
    command += f" --limit {limit} --page-limit {page_limit}"
    if bulk_text:
        command += " --bulk-text"
    snapshot = {"source": SOURCE, "terms_url": TERMS, "text_reuse_terms": REUSE,
                "responses": api.responses, "expected_pages": expected_pages,
                "imported_pages": len(pages), "page_limited": len(pages) < expected_pages,
                "text_transport": "full.txt" if bulk_text else "page-api"}
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with tables.locked(out), tempfile.TemporaryDirectory(prefix=f".{out.name}-", dir=staging_dir(out)) as scratch:
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
