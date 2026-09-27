"""Old books of the 국립중앙도서관 (National Library of Korea) digital library, from its PDF viewer.

A record is named by its content id, `CNTS-…`. Two pages of the library's own search site state its
bibliography: the viewer's own metadata endpoint (`getBookInfo.jsp`), which is what a viewer session
requests when a reader opens a book, and the search site's detail popup (`nl_detail_online_view.ajax`),
which carries the fuller catalogue statement (판사항, 발행사항, 주기사항) and, for a record that has a
physical counterpart in the catalogue, a link naming its `KOL…` control number.

The digitised pages themselves are one PDF per record, served at `viewer.nl.go.kr/conv/<name>.pdf` and
built when the viewer form is posted for the record, as the viewer does when a reader opens it. Most
records are named by their own content id; some (the older 고문헌 records that already had a KOL
catalogue entry before they were digitised) are named by that KOL id instead, so a `.pdf` request
under the content id is tried first and the KOL id only when that answers 404. Each
page of the PDF holds one embedded JPEG, which is extracted as it stands: PyMuPDF reads the compressed
image bytes straight out of the page's image directory, without re-encoding.

The library states no 공공누리 label on these records; `[관외이용-무료]` means the item can be read from
outside the library because its copyright has expired or its rightsholder gave permission, not that
reuse beyond viewing and printing is licensed (`docs/licensing.md`, and the library's own copyright
notice, `POLICY`). Every image is recorded with `Licence.RESTRICTED`; `Document._public_domain_reproduction`
turns that into `PD` with `holder_terms` restricted, for a record whose date is 1900 or earlier or
undated, which is the pattern this collector expects for a National Library 고문헌 collection.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Callable, Iterable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from .. import images, net, tables
from ..schema import Dating, Document, Licence, Page, PageText, Rights

SOURCE = "nlk"
HOLDER = "국립중앙도서관"
BOOKINFO_API = "https://viewer.nl.go.kr/nlviewer/pdf/web/getBookInfo.jsp?contents_id={cno}"
DETAIL_API = "https://www.nl.go.kr/NL/search/nl_detail_online_view.ajax"
PDF_URL = "https://viewer.nl.go.kr/conv/{name}.pdf"
#: The form the viewer page posts when a reader opens a record; it is what starts the PDF build.
VIEWER_OPEN = "https://viewer.nl.go.kr/main.wviewer"
SEARCH_PAGE = "https://www.nl.go.kr/NL/contents/search.do?kwd={cno}"
POLICY = "https://www.nl.go.kr/NL/contents/N70600000000.do"

ACCESS_WORDING = "[관외이용-무료]"
ATTRIBUTION = f"{HOLDER}, {ACCESS_WORDING}"

CNTS = re.compile(r"^CNTS-\d+$")
CONTROL_NO = re.compile(r"controlNo=(KOL\d+)")
STATED_PAGES = re.compile(r"^PDF\s*\|\s*(\d+)\s*p\.?$")
BOOKINFO_ROW = re.compile(
    r'<span class="label">\s*(?P<label>[^<]*?)\s*</span>\s*<span class="text">\s*(?P<value>.*?)\s*</span>',
    re.DOTALL,
)
DETAIL_FIELD = re.compile(
    r'<span class="mark">\s*(?P<label>[^<]*?)\s*</span>\s*(?P<value>.*?)\s*</p>', re.DOTALL
)
TAG = re.compile(r"<[^>]+>")
YEAR = re.compile(r"(\d{4})")

#: 판사항 substrings mapped to `data/vocab/production.yaml` nodes; matched in order, first hit wins.
PRODUCTION = (
    ("木板本", "printed/woodblock"),
    ("木版本", "printed/woodblock"),
    ("活字本", "printed/type"),
    ("筆寫本", "handwritten"),
    ("寫本", "handwritten"),
)


class RecordError(RuntimeError):
    """A record the library did not answer in the expected shape."""


def content_id(value: str) -> str:
    text = str(value).strip()
    if not CNTS.match(text):
        raise ValueError(f"{value!r}: a content id is CNTS-<digits>, e.g. CNTS-00092710493")
    return text


def _clean(value: str) -> str:
    """HTML markup and entities removed, whitespace collapsed."""
    text = TAG.sub(" ", value)
    text = text.replace("&amp;", "&").replace("&nbsp;", " ").replace("&lt;", "<").replace("&gt;", ">")
    return re.sub(r"\s+", " ", text).strip()


def _bookinfo(payload: bytes, cno: str) -> dict[str, str]:
    text = payload.decode("utf-8", errors="replace")
    fields = {_clean(m["label"]): _clean(m["value"]) for m in BOOKINFO_ROW.finditer(text)}
    if "표제" not in fields:
        raise RecordError(f"{cno}: getBookInfo answered with no 표제 field")
    return fields


def _detail(payload: bytes, cno: str) -> tuple[dict[str, str], str | None]:
    """The 서지정보 fields of the detail popup, and the KOL control number if it states one."""
    text = payload.decode("utf-8", errors="replace")
    if "popup_contents" not in text and "more_info_wrap" not in text:
        raise RecordError(f"{cno}: the detail view answered with no record")
    fields = {_clean(m["label"]): _clean(m["value"]) for m in DETAIL_FIELD.finditer(text)}
    title_match = re.search(r'<h3 class="detail_tit">(.*?)</h3>', text, re.DOTALL)
    if title_match:
        fields.setdefault("표제/저자사항", _clean(title_match.group(1)))
    found = CONTROL_NO.search(text)
    return fields, found.group(1) if found else None


def _production(statement: str) -> str:
    for needle, node in PRODUCTION:
        if needle in statement:
            return node
    return "unknown"


def _dating(statement: str) -> Dating | None:
    """The 발행사항 statement as a `Dating`, or None when it states no date at all."""
    statement = statement.strip()
    if not statement or not re.search(r"[0-9一-鿿]", statement):
        return None
    years = [int(y) for y in YEAR.findall(statement)]
    if not years:
        return Dating(literal=statement, kind="unknown", evidence="발행사항")
    start, end = min(years), max(years)
    if "이후" in statement:
        end = None
    elif "이전" in statement:
        start = None
    return Dating(literal=statement, start=start, end=end, kind="publication", evidence="발행사항")


def _pdf_names(cno: str, kol: str | None) -> list[str]:
    """PDF basenames to try in order: the content id first, then the KOL control number if known."""
    names = [cno]
    if kol:
        names.append(kol)
    return names


#: A PDF is built when a reader opens the record in the viewer (`VIEWER_OPEN`), and the build runs in
#: the background: until it finishes, and for a while on some of the servers behind the host, the PDF
#: answers 404. Each candidate name is retried on a 404 with a growing pause, rather than treated as
#: a name that has no PDF at all.
BUILD_RETRIES = 10
BUILD_BACKOFF = 5.0
BUILD_MAX_BACKOFF = 60.0


def _fetch_pdf(
    cno: str,
    kol: str | None,
    dest: Path,
    *,
    retries: int,
    fetch_options: dict[str, Any],
    build_retries: int = BUILD_RETRIES,
    stated_pages: int | None = None,
) -> tuple[Path, str]:
    """Download the record's PDF, trying each candidate name, and return the path and the URL used.

    Which name answered is kept in a sidecar file next to `dest`, written only once the file opens as
    a PDF with `stated_pages` pages (when the record states a count), so that a rerun reuses only a
    file known to be whole and knows which name it came under. A cached file that fails the check, or
    has no sidecar, is fetched again. A body that is not such a PDF (an error page, or a file served
    mid-build) is retried like a 404.
    """
    marker = dest.with_name(dest.name + ".url")
    if dest.exists() and marker.exists():
        if _pdf_problem(dest, stated_pages) is None:
            return dest, marker.read_text(encoding="utf-8").strip()
        marker.unlink()
    dest.unlink(missing_ok=True)
    sleeper = fetch_options.get("sleeper") or net.SLEEP
    net.download(
        VIEWER_OPEN, dest.with_name(f"{cno}.viewer.html"), retries=retries, refresh=True,
        method="POST", data={"cno": cno, "ax": "Y", "sysid": "homepage"},
        referer=f"{VIEWER_OPEN}?cno={cno}&sysid=homepage", **fetch_options,
    )
    last_error = "no request was made"
    for name in _pdf_names(cno, kol):
        url = PDF_URL.format(name=name)
        for attempt in range(1, build_retries + 1):
            try:
                path = net.download(url, dest, retries=retries, **fetch_options)
            except net.DownloadError as error:
                last_error = str(error)
                if "HTTP 404" not in last_error:
                    raise
            else:
                problem = _pdf_problem(path, stated_pages)
                if problem is None:
                    marker.write_text(url, encoding="utf-8")
                    return path, url
                path.unlink()
                last_error = f"{url}: {problem}"
            if attempt < build_retries:
                sleeper(min(BUILD_BACKOFF * attempt, BUILD_MAX_BACKOFF))
    raise RecordError(
        f"{cno}: no whole PDF at any of {_pdf_names(cno, kol)} after waiting for it to build "
        f"({last_error})"
    )


def _pdf_problem(path: Path, stated_pages: int | None) -> str | None:
    """Why the file at `path` is not the record's whole PDF, or None when it is."""
    import pymupdf

    try:
        doc = pymupdf.open(path, filetype="pdf")
    except (RuntimeError, ValueError) as error:
        return f"not a readable PDF ({error.__class__.__name__}: {error})"
    try:
        if stated_pages is not None and doc.page_count != stated_pages:
            return f"the PDF has {doc.page_count} pages, the record states {stated_pages}"
        return None
    finally:
        doc.close()


def _stated_pages(bookinfo: dict[str, str]) -> int | None:
    """The page count the record states in its 형태사항, as `PDF | N p.`, if it states one."""
    stated = STATED_PAGES.match(bookinfo.get("형태사항", ""))
    return int(stated.group(1)) if stated else None


def collect(
    ids: Iterable[str],
    out: Path,
    *,
    client: httpx.Client | None = None,
    retries: int = 5,
    cache: Path | None = None,
    clock: Callable[[], float] | None = None,
    sleeper: Callable[[float], None] | None = None,
    checked: date | None = None,
    command: str | None = None,
    build_retries: int = BUILD_RETRIES,
) -> dict[str, Any]:
    """Collect the National Library records `ids` into the dataset directory `out`.

    The bookinfo and detail HTML, and the PDF, are kept in `out/upstream/` and reused on a rerun.
    Each page's embedded image goes through `images.register`, keyed by the PDF's URL with a
    `#page=<n>` fragment. A record whose metadata or PDF cannot be fetched, or whose PDF holds a page
    with no embedded image, is left out whole and listed under `unavailable`. `build_retries` bounds
    how many times a PDF that is still 404 (still converting) is asked for again before giving up.
    The tables written hold exactly the records of `ids`: a rerun with fewer ids drops the others.
    """
    out = Path(out)
    upstream = out / "upstream"
    ids = [content_id(value) for value in ids]
    checked = checked or datetime.now(UTC).date()
    own_client = client is None
    if own_client:
        client = httpx.Client(timeout=120.0, follow_redirects=True)
    fetch_options = {"client": client, "clock": clock, "sleeper": sleeper}
    documents: list[Document] = []
    pages: list[Page] = []
    collected, unavailable = [], []
    try:
        for cno in ids:
            try:
                bookinfo_path = net.download(
                    BOOKINFO_API.format(cno=cno), upstream / f"{cno}.bookinfo.html",
                    retries=retries, **fetch_options,
                )
                bookinfo = _bookinfo(bookinfo_path.read_bytes(), cno)
                detail_path = net.download(
                    DETAIL_API, upstream / f"{cno}.detail.html", retries=retries,
                    method="POST", data={"viewKey": cno, "viewType": "C", "category": "고문헌",
                                         "pageIdx": "1", "jourId": ""},
                    **fetch_options,
                )
                detail, kol = _detail(detail_path.read_bytes(), cno)
            except (net.DownloadError, RecordError) as error:
                # An answer that did not parse is not kept, so a rerun asks again.
                for name in ("bookinfo", "detail"):
                    (upstream / f"{cno}.{name}.html").unlink(missing_ok=True)
                unavailable.append({"id": cno, "error": str(error)})
                continue
            try:
                pdf_path, pdf_url = _fetch_pdf(
                    cno, kol, upstream / f"{cno}.pdf", retries=retries, fetch_options=fetch_options,
                    build_retries=build_retries, stated_pages=_stated_pages(bookinfo),
                )
            except net.DownloadError as error:
                unavailable.append({"id": cno, "title": bookinfo.get("표제"), "error": str(error)})
                continue
            except RecordError as error:
                unavailable.append({"id": cno, "title": bookinfo.get("표제"), "error": str(error)})
                continue
            try:
                document, record_pages = build(
                    cno, bookinfo, detail, kol=kol, pdf_path=pdf_path, pdf_url=pdf_url,
                    cache=cache, checked=checked,
                )
            except RecordError as error:
                # A PDF that could not be built into pages is not kept, so a rerun fetches it again.
                pdf_path.unlink(missing_ok=True)
                pdf_path.with_name(pdf_path.name + ".url").unlink(missing_ok=True)
                unavailable.append({"id": cno, "title": bookinfo.get("표제"), "error": str(error)})
                continue
            documents.append(document)
            pages.extend(record_pages)
            collected.append({"id": cno, "title": document.title, "pages": len(record_pages),
                              "pdf": pdf_url, "kol": kol})
    finally:
        if own_client:
            client.close()

    out.mkdir(parents=True, exist_ok=True)
    counts = {"documents": len(documents), "pages": len(pages), "page_texts": 0}
    for name, records, model in (("documents", documents, Document), ("pages", pages, Page),
                                 ("page_texts", [], PageText)):
        staging = out / f".{name}.parquet"
        tables.write(staging, records, model)
        os.replace(staging, out / f"{name}.parquet")
    summary = {"source": SOURCE, "holder": HOLDER, "attribution": ATTRIBUTION,
               "licence_evidence": POLICY, "collected": collected, "unavailable": unavailable}
    manifest = {"schema_version": tables.SCHEMA_VERSION, "tables": counts,
                "command": command or "collect 국립중앙도서관 old books", "collection": summary}
    staging = out / ".MANIFEST.json"
    staging.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(staging, out / "MANIFEST.json")
    return summary


def build(
    cno: str,
    bookinfo: dict[str, str],
    detail: dict[str, str],
    *,
    kol: str | None,
    pdf_path: Path,
    pdf_url: str,
    cache: Path | None,
    checked: date,
) -> tuple[Document, list[Page]]:
    """The document and pages of one record whose PDF is on disk at `pdf_path`."""
    import pymupdf  # imported lazily so the module loads without the optional extra

    document_id = f"{SOURCE}:{cno}"
    pdf_bytes = pdf_path.read_bytes()
    revision = hashlib.sha256(pdf_bytes).hexdigest()

    title = detail.get("표제/저자사항") or bookinfo.get("표제", cno)
    title = re.split(r"\s*/\s*", title)[0].strip()
    production_statement = detail.get("판사항", "")
    publication_statement = detail.get("발행사항") or (
        f"{bookinfo.get('발행처', '')}, {bookinfo.get('발행년도', '')}".strip(", ")
    )
    dating = _dating(publication_statement)

    doc = pymupdf.open(pdf_path)
    try:
        pages: list[Page] = []
        for index in range(doc.page_count):
            seq = index + 1
            page = doc[index]
            candidates = page.get_images(full=True)
            if not candidates:
                raise RecordError(f"{cno}: page {seq} of the PDF holds no embedded image")
            best_xref, best_area = None, -1
            for entry in candidates:
                xref = entry[0]
                width, height = entry[2], entry[3]
                area = width * height
                if area > best_area:
                    best_xref, best_area = xref, area
            info = doc.extract_image(best_xref)
            image_url = f"{pdf_url}#page={seq}"
            scratch = images.images_root(cache) / ".tmp"
            scratch.mkdir(parents=True, exist_ok=True)
            temp_path = scratch / f"{uuid4().hex}.{info['ext']}"
            temp_path.write_bytes(info["image"])
            try:
                record = images.register(
                    temp_path, image_url, root=cache, width=info["width"], height=info["height"],
                    fetched_at=datetime.now(UTC),
                )
            finally:
                temp_path.unlink(missing_ok=True)
            pages.append(Page(
                id=f"{document_id}:{seq}", document_id=document_id, seq=seq,
                image=record.url, width=record.width, height=record.height, sha256=record.sha256,
                meta={"pdf_page": seq},
            ))
    finally:
        doc.close()

    shelfmark = detail.get("청구기호") or None
    rights = Rights(licence=Licence.RESTRICTED, holder=HOLDER, attribution=ATTRIBUTION, evidence=POLICY,
                    checked=checked)
    meta: dict[str, Any] = {
        "language": "ko",
        "author": detail.get("표제/저자사항", "").split("/", 1)[1].strip() if "/" in detail.get(
            "표제/저자사항", "") else bookinfo.get("저자") or None,
        "collation": detail.get("형태사항") or bookinfo.get("형태사항") or None,
        "notes": detail.get("주기사항") or None,
        "standard_number": detail.get("표준번호/부호") or bookinfo.get("표준번호") or None,
        "classification": detail.get("분류기호") or None,
        "subject": detail.get("주제명") or None,
        "access": ACCESS_WORDING,
        "copyright_note": bookinfo.get("저작권") or None,
        "kol_control_number": kol,
        "record_sha256": revision,
        "pdf_url": pdf_url,
        "search_page": SEARCH_PAGE.format(cno=cno),
    }
    meta = {key: value for key, value in meta.items() if value not in (None, [], "")}

    document = Document(
        id=document_id,
        title=title,
        origin="korea",
        source_refs={SOURCE: cno, "catalogue": SEARCH_PAGE.format(cno=cno), "pdf": pdf_url,
                     **({"kol": kol} if kol else {})},
        holder=HOLDER,
        shelfmark=shelfmark,
        production=_production(production_statement),
        dating=[dating] if dating else [],
        image_rights=rights,
        meta=meta,
    )
    return document, pages
