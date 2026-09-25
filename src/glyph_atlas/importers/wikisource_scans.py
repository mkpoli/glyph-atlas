"""Proofread Wikisource scans: each PDF page's image from Commons, its text from the Page namespace.

A Wikisource index (`Index:`, `색인:` on ko) proofreads one Commons file page by page. One index
becomes one document. Every page of the file becomes a dataset page whose image is Commons' rendering
of that page. A page the index has transcribed gets a page text: the body of its `Page:` wikitext,
without the header and footer sections. A page the index has not created stays in the dataset with no
text. Each page's proofreading level (0 to 4) is kept in its meta.

The scan's licence tags are read from the Commons file page and kept verbatim; the transcription's
licence comes from the wiki's own `rightsinfo`. Neither is assumed.

Requests go out one at a time, `pause` seconds apart per host, with a User-Agent that names the client,
its version and the repository. A 429 or a 5xx is retried after `Retry-After`
(`net.download` does the retrying). Page images go into the project's image cache.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode, urlsplit, urlunsplit
from uuid import uuid4

import httpx

from .. import images, net, rights, tables
from ..schema import Dating, Document, Page, PageText, Rights

USER_AGENT = "GlyphAtlas/0.1 (+https://github.com/mkpoli/glyph-atlas)"
PAUSE = 1.0
COMMONS = "commons.wikimedia.org"
UPLOAD = "https://upload.wikimedia.org/wikipedia/commons"
#: Widths the Wikimedia thumbnail servers render; a direct request for any other width is refused.
THUMBNAIL_STEPS = (20, 40, 60, 120, 250, 330, 500, 960, 1280, 1920, 3840)
THUMBNAIL_STEPS_EVIDENCE = "https://www.mediawiki.org/w/index.php?title=Common_thumbnail_sizes&oldid=8531221"
#: Titles per `prop` request; the API's limit for a client without the bot right.
BATCH = 50
MAXLAG_RETRIES = 5
TEXT_SOURCE = "wikisource"
#: Contributor names for the text attribution, by wiki language code.
CONTRIBUTORS = {
    "ko": "Korean Wikisource contributors",
    "ja": "Japanese Wikisource contributors",
    "zh": "Chinese Wikisource contributors",
    "en": "English Wikisource contributors",
}

_NOINCLUDE_HEAD = re.compile(r"^\s*<noinclude>(.*?)</noinclude>", re.DOTALL)
_NOINCLUDE_TAIL = re.compile(r"<noinclude>((?:(?!<noinclude>).)*?)</noinclude>\s*$", re.DOTALL)
_QUALITY = re.compile(r"""<pagequality\s+level\s*=\s*["']?(\d)["']?(?:\s+user\s*=\s*["']([^"']*)["'])?""")
_TEMPLATE = re.compile(r"\{\{(?:[^{}]|\{\{[^{}]*\}\})*\}\}")
_LINK = re.compile(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]")
_EXTERNAL = re.compile(r"\[(https?://\S+)\s+([^\]]+)\]")
_LICENCE_HEADER = re.compile(r"^==\s*\{\{\s*int:license-header\s*\}\}\s*==\s*$", re.MULTILINE | re.IGNORECASE)
_YEAR = re.compile(r"^\s*(\d{3,4})\s*$")


class WikiError(RuntimeError):
    """An API answer that carries an error or lacks the data asked for."""


# --------------------------------------------------------------------------- HTTP
class Http:
    """Serial, paced requests to the Wikimedia hosts, through `net.download`."""

    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        pause: float = PAUSE,
        retries: int = 5,
        clock: Callable[[], float] | None = None,
        sleeper: Callable[[float], None] | None = None,
    ):
        self.client = client
        self.pause = pause
        self.retries = retries
        self.clock = clock
        self.sleeper = sleeper if sleeper is not None else time.sleep
        #: Every API URL asked, in order, for the dataset's provenance.
        self.asked: list[str] = []

    def download(self, url: str, dest: Path, *, expected: str) -> dict[str, Any]:
        meta: dict[str, Any] = {}
        net.download(url, dest, expected=expected, pause=self.pause, retries=self.retries, client=self.client,
                     clock=self.clock, sleeper=self.sleeper, refresh=True, meta=meta, user_agent=USER_AGENT)
        return meta

    def api(self, host: str, params: dict[str, Any]) -> dict[str, Any]:
        """One API answer, retried while the servers report replication lag."""
        query = urlencode({**params, "format": "json", "formatversion": 2, "maxlag": 5})
        url = f"https://{host}/w/api.php?{query}"
        self.asked.append(url)
        for attempt in range(1, MAXLAG_RETRIES + 1):
            with tempfile.TemporaryDirectory(prefix="wikisource-scans-") as scratch:
                dest = Path(scratch) / "answer.json"
                self.download(url, dest, expected="json")
                payload = json.loads(dest.read_text(encoding="utf-8"))
            error = payload.get("error") if isinstance(payload, dict) else None
            if error is None:
                return payload
            if error.get("code") == "maxlag" and attempt < MAXLAG_RETRIES:
                self.sleeper(5.0)
                continue
            raise WikiError(f"{host}: API error {error.get('code')}")
        raise AssertionError("unreachable")

    def query(self, host: str, params: dict[str, Any]) -> dict[str, Any]:
        """`action=query` followed through every `continue`, with `pages` merged by title."""
        merged: dict[str, Any] = {}
        pages: dict[str, dict[str, Any]] = {}
        cursor: dict[str, Any] = {}
        while True:
            payload = self.api(host, {"action": "query", **params, **cursor})
            for key, value in (payload.get("query") or {}).items():
                if key == "pages":
                    for page in value:
                        into = pages.setdefault(page["title"], {})
                        for name, item in page.items():
                            if isinstance(item, list):
                                into.setdefault(name, []).extend(item)
                            else:
                                into[name] = item
                elif isinstance(value, list):
                    merged.setdefault(key, []).extend(value)
                else:
                    merged[key] = value
            cursor = payload.get("continue") or {}
            if not cursor:
                break
        if pages:
            merged["pages"] = list(pages.values())
        return merged


# ----------------------------------------------------------------------- wikitext
def split_page(wikitext: str) -> tuple[str, str, str]:
    """A `Page:` wikitext as (header, body, footer).

    ProofreadPage stores a page as `<noinclude>header</noinclude>body<noinclude>footer</noinclude>`;
    the header holds the `<pagequality>` tag. A text without the sections is all body.
    """
    header = footer = ""
    rest = wikitext
    head = _NOINCLUDE_HEAD.match(rest)
    if head:
        header, rest = head.group(1), rest[head.end():]
    tail = _NOINCLUDE_TAIL.search(rest)
    if tail:
        footer, rest = tail.group(1), rest[: tail.start()]
    return header, rest, footer


def quality_of(header: str) -> tuple[int | None, str | None]:
    """The `<pagequality>` level and user stated in a page header."""
    found = _QUALITY.search(header)
    if not found:
        return None, None
    return int(found.group(1)), found.group(2)


def field_of(wikitext: str, name: str) -> str | None:
    """The value of a `|name=value` template parameter on its own line, or None when empty."""
    found = re.search(rf"^\s*\|\s*{re.escape(name)}\s*=(.*)$", wikitext, re.MULTILINE)
    if not found:
        return None
    value = found.group(1).strip()
    return value or None


def plain(value: str | None) -> str | None:
    """Wiki links reduced to their label."""
    if value is None:
        return None
    return _LINK.sub(lambda m: m.group(1), value).strip() or None


def licence_tags(wikitext: str) -> list[str]:
    """The templates of a Commons file page's licence section, verbatim."""
    header = _LICENCE_HEADER.search(wikitext)
    if not header:
        return []
    section = wikitext[header.end():]
    following = re.search(r"^==[^=]", section, re.MULTILINE)
    if following:
        section = section[: following.start()]
    return _TEMPLATE.findall(section)


def source_link(wikitext: str) -> tuple[str | None, str | None]:
    """The first external link of the Commons `source=` field, as (URL, label)."""
    value = field_of(wikitext, "source")
    found = _EXTERNAL.search(value or "")
    return (found.group(1), found.group(2).strip()) if found else (None, None)


def year_of(value: str | None) -> int | None:
    found = _YEAR.match(value or "")
    return int(found.group(1)) if found else None


def clean_url(url: str) -> str:
    """A URL without the tracking query Commons appends to `imageinfo` answers."""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def wiki_url(host: str, title: str) -> str:
    return f"https://{host}/wiki/" + quote(title.replace(" ", "_"), safe=":/()")


# ------------------------------------------------------------------------ images
def thumbnail_width(native: int) -> int:
    """The largest thumbnail step no wider than `native`, or the smallest step."""
    fitting = [step for step in THUMBNAIL_STEPS if step <= native]
    return fitting[-1] if fitting else THUMBNAIL_STEPS[0]


def thumbnail_url(file_name: str, page: int, width: int) -> str:
    """Commons' JPEG rendering of one page of a paged file (PDF or DjVu)."""
    name = file_name.removeprefix("File:").replace(" ", "_")
    digest = hashlib.md5(name.encode("utf-8")).hexdigest()
    encoded = quote(name)
    return f"{UPLOAD}/thumb/{digest[0]}/{digest[:2]}/{encoded}/page{page}-{width}px-{encoded}.jpg"


def fetch_image(http: Http, url: str, *, root: Path | None, known: dict[str, images.ImageRecord]):
    """The cache row of `url`, downloaded and registered when the cache does not hold it."""
    cache = images.images_root(root)
    record = known.get(url)
    if record is not None and images.path_for(url, root=cache) is not None:
        return record
    scratch = cache / ".tmp"
    scratch.mkdir(parents=True, exist_ok=True)
    part = scratch / f"{uuid4().hex}.jpg"
    try:
        meta = http.download(url, part, expected="image")
        record = images.register(part, url, root=cache, etag=meta.get("etag"),
                                 last_modified=meta.get("last_modified"))
    finally:
        part.unlink(missing_ok=True)
    known[url] = record
    return record


# ------------------------------------------------------------------------ records
@dataclass
class ScanPage:
    number: int
    title: str
    label: str | None = None
    pageid: int | None = None
    revision: int | None = None
    timestamp: str | None = None
    url: str | None = None
    quality: int | None = None
    quality_text: str | None = None
    quality_user: str | None = None
    body: str = ""
    transcluded_in: list[str] = field(default_factory=list)

    @property
    def exists(self) -> bool:
        return bool(self.pageid)

    @property
    def status(self) -> str:
        if not self.exists:
            return "missing"
        return "transcribed" if self.body.strip() else "empty"


@dataclass
class Site:
    host: str
    page_ns: str
    index_ns: str
    licence_url: str
    licence_text: str
    rightsinfo_url: str


def read_site(http: Http, wiki: str) -> Site:
    """The wiki's Page and Index namespaces and its licence.

    ProofreadPage names its namespace ids per wiki (250 and 252 on ja and ko, 104 and 106 on zh), so
    the ids come from `proofreadinfo` and the local names from `siteinfo`.
    """
    host = f"{wiki}.wikisource.org"
    params = {"meta": "siteinfo|proofreadinfo", "siprop": "rightsinfo|namespaces", "piprop": "namespaces"}
    answer = http.query(host, params)
    spaces = answer.get("namespaces") or {}
    proofread = answer.get("proofreadnamespaces") or {}
    rightsinfo = answer.get("rightsinfo") or {}
    try:
        page_ns = spaces[str(proofread["page"]["id"])]["name"]
        index_ns = spaces[str(proofread["index"]["id"])]["name"]
    except KeyError:
        raise WikiError(f"{host}: no ProofreadPage namespaces") from None
    return Site(host=host, page_ns=page_ns, index_ns=index_ns, licence_url=rightsinfo.get("url") or "",
                licence_text=rightsinfo.get("text") or "",
                rightsinfo_url=f"https://{host}/w/api.php?" + urlencode(
                    {"action": "query", "meta": "siteinfo", "siprop": "rightsinfo"}))


def read_pages(http: Http, site: Site, index_title: str) -> list[ScanPage]:
    """Every page the index lists, in file order, with the text and level of those that exist."""
    listing = http.query(site.host, {"list": "proofreadpagesinindex", "prppiititle": index_title,
                                     "prppiiprop": "ids|title|formattedpagenumber"})
    entries = listing.get("proofreadpagesinindex")
    if not isinstance(entries, list):
        raise WikiError(f"{site.host}: {index_title} lists no pages")
    pages = [ScanPage(number=int(entry["pageoffset"]), title=entry["title"],
                      label=entry.get("formattedPageNumber") or entry.get("formattedpagenumber"),
                      pageid=entry.get("pageid") or None)
             for entry in entries]
    pages.sort(key=lambda page: page.number)
    existing = [page for page in pages if page.exists]
    by_id = {page.pageid: page for page in existing}
    for start in range(0, len(existing), BATCH):
        chunk = existing[start: start + BATCH]
        answer = http.query(site.host, {"prop": "revisions|proofread|info|transcludedin",
                                        "rvprop": "content|ids|timestamp", "rvslots": "main",
                                        "inprop": "url", "tinamespace": 0, "tilimit": "max",
                                        # Page ids keep the URL short; 50 Korean titles exceed its limit.
                                        "pageids": "|".join(str(page.pageid) for page in chunk)})
        for item in answer.get("pages") or []:
            page = by_id.get(item.get("pageid"))
            if page is None or item.get("missing"):
                continue
            revision = (item.get("revisions") or [{}])[0]
            content = ((revision.get("slots") or {}).get("main") or {}).get("content") or ""
            header, body, _footer = split_page(content)
            level, user = quality_of(header)
            proofread = item.get("proofread") or {}
            page.revision = revision.get("revid")
            page.timestamp = revision.get("timestamp")
            page.url = item.get("fullurl")
            page.quality = proofread.get("quality", level)
            page.quality_text = proofread.get("quality_text")
            page.quality_user = user
            page.body = body.strip()
            page.transcluded_in = sorted({t["title"] for t in item.get("transcludedin") or []})
    return pages


def read_reading_pages(http: Http, site: Site, titles: Iterable[str]) -> list[dict[str, Any]]:
    """The main-namespace pages that transclude the scan, with the year their header states."""
    titles = sorted(set(titles))
    works = []
    for start in range(0, len(titles), BATCH):
        answer = http.query(site.host, {"prop": "revisions|info", "rvprop": "content|ids", "rvslots": "main",
                                        "inprop": "url", "titles": "|".join(titles[start: start + BATCH])})
        for item in answer.get("pages") or []:
            revision = (item.get("revisions") or [{}])[0]
            content = ((revision.get("slots") or {}).get("main") or {}).get("content") or ""
            stated = field_of(content, "연도") or field_of(content, "year")
            works.append({"title": item["title"], "url": item.get("fullurl") or wiki_url(site.host, item["title"]),
                          "revision": revision.get("revid"), "year_stated": stated})
    return sorted(works, key=lambda work: work["title"])


def read_commons_file(http: Http, file_name: str) -> dict[str, Any]:
    title = "File:" + file_name.removeprefix("File:")
    answer = http.query(COMMONS, {"titles": title, "prop": "imageinfo|revisions",
                                  "iiprop": "url|size|sha1|mime|extmetadata",
                                  "rvprop": "content|ids", "rvslots": "main"})
    pages = answer.get("pages") or []
    if not pages or pages[0].get("missing") or not pages[0].get("imageinfo"):
        raise WikiError(f"{COMMONS}: {title} has no file")
    page = pages[0]
    info = page["imageinfo"][0]
    revision = (page.get("revisions") or [{}])[0]
    wikitext = ((revision.get("slots") or {}).get("main") or {}).get("content") or ""
    extmetadata = {key: value.get("value") for key, value in (info.get("extmetadata") or {}).items()
                   if key in ("LicenseShortName", "License", "UsageTerms", "DateTimeOriginal", "ObjectName")}
    return {"title": page["title"], "revision": revision.get("revid"), "wikitext": wikitext,
            "url": clean_url(info["url"]), "description_url": info["descriptionurl"],
            "width": info["width"], "height": info["height"], "pagecount": info.get("pagecount"),
            "size": info.get("size"), "sha1": info.get("sha1"), "mime": info.get("mime"),
            "extmetadata": extmetadata}


def read_index(http: Http, site: Site, index_title: str) -> dict[str, Any]:
    answer = http.query(site.host, {"titles": index_title, "prop": "revisions|info", "rvprop": "content|ids",
                                    "rvslots": "main", "inprop": "url"})
    pages = answer.get("pages") or []
    if not pages or pages[0].get("missing"):
        raise WikiError(f"{site.host}: {index_title} does not exist")
    revision = (pages[0].get("revisions") or [{}])[0]
    wikitext = ((revision.get("slots") or {}).get("main") or {}).get("content") or ""
    return {"title": pages[0]["title"], "url": pages[0].get("fullurl") or wiki_url(site.host, index_title),
            "revision": revision.get("revid"), "wikitext": wikitext}


# ------------------------------------------------------------------------ collect
@dataclass
class Collected:
    document: Document
    pages: list[Page]
    texts: list[PageText]
    summary: dict[str, Any]


def collect_index(
    http: Http,
    site: Site,
    wiki: str,
    name: str,
    *,
    image_root: Path | None = None,
    native_width: int | None = None,
    native_width_evidence: str | None = None,
    scripts: Iterable[str] = (),
    language: str | None = None,
    contributors: str | None = None,
    production: str = "unknown",
    known: dict[str, images.ImageRecord] | None = None,
    checked: date | None = None,
) -> Collected:
    """One index as a document, its pages and its page texts."""
    file_name = name.split(":", 1)[1] if ":" in name and not name.lower().startswith("file:") else name
    file_name = file_name.removeprefix("File:")
    index_title = f"{site.index_ns}:{file_name}"
    checked = checked or datetime.now(UTC).date()
    known = known if known is not None else {}

    index = read_index(http, site, index_title)
    commons = read_commons_file(http, file_name)
    scan_pages = read_pages(http, site, index_title)
    pagecount = commons.get("pagecount") or (max(p.number for p in scan_pages) if scan_pages else 0)
    listed = {page.number: page for page in scan_pages}
    for number in range(1, pagecount + 1):  # a file page the index does not list is still a scan page
        listed.setdefault(number, ScanPage(number=number, title=f"{site.page_ns}:{file_name}/{number}"))
    scan_pages = [listed[number] for number in sorted(listed)]
    works = read_reading_pages(http, site, (t for p in scan_pages for t in p.transcluded_in))
    for work in works:
        work["pages"] = [p.number for p in scan_pages if work["title"] in p.transcluded_in]

    limit = native_width or commons["width"]
    width = thumbnail_width(limit)
    key = hashlib.sha256(f"{site.host}:{index_title}".encode()).hexdigest()[:24]
    document_id = f"ws:{wiki}:scan:{key}"

    pages: list[Page] = []
    texts: list[PageText] = []
    for scan in scan_pages:
        url = thumbnail_url(file_name, scan.number, width)
        record = fetch_image(http, url, root=image_root, known=known)
        pages.append(Page(
            id=f"{document_id}:{scan.number}", document_id=document_id, seq=scan.number, image=url,
            width=record.width, height=record.height, sha256=record.sha256,
            transcription=({"source": TEXT_SOURCE, "entry": scan.title, "revision": str(scan.revision or "")}
                           if scan.exists else {}),
            meta={"pdf_page": scan.number, "label": scan.label, "wikisource_page": scan.title,
                  "wikisource_url": scan.url or wiki_url(site.host, scan.title),
                  "wikisource_pageid": scan.pageid, "timestamp": scan.timestamp, "text_status": scan.status,
                  "quality": scan.quality, "quality_text": scan.quality_text, "quality_user": scan.quality_user,
                  "transcluded_in": scan.transcluded_in, "commons_file": commons["description_url"],
                  "thumbnail_width": width, "geometry": "none"},
        ))
        if scan.status == "transcribed":
            texts.append(PageText(page_id=f"{document_id}:{scan.number}", source=TEXT_SOURCE,
                                  revision=str(scan.revision or ""), text_raw=scan.body))

    source_url, holder = source_link(commons["wikitext"])
    catalogue_id = re.search(r"viewKey=([A-Z]+-\d+)", source_url or "")
    date_field = field_of(commons["wikitext"], "date")
    dating: list[Dating] = []
    published = year_of(date_field)
    if published is not None:
        dating.append(Dating(literal=date_field, start=published, end=published, kind="publication",
                             evidence=commons["description_url"]))
    elif date_field:
        dating.append(Dating(literal=date_field, kind="unknown", evidence=commons["description_url"]))
    if len(works) == 1 and year_of(works[0]["year_stated"]) is not None:
        # One reading page covers the whole transcription, so its stated year dates the text.
        year = year_of(works[0]["year_stated"])
        dating.append(Dating(literal=works[0]["year_stated"], start=year, end=year, kind="composition",
                             evidence=works[0]["url"]))

    image_licence = rights.resolve(licence=commons["extmetadata"].get("LicenseShortName")).licence
    text_licence = rights.resolve(url=site.licence_url).licence
    contributors = contributors or CONTRIBUTORS.get(wiki, f"{wiki} Wikisource contributors")
    language = language or field_of(index["wikitext"], "언어") or field_of(index["wikitext"], "Language") or wiki
    refs = {
        "commons-file": commons["description_url"],
        "commons-original": commons["url"],
        "commons-api": f"https://{COMMONS}/w/api.php",
        "wikisource-index": index["url"],
        "wikisource-api": f"https://{site.host}/w/api.php",
        "wikisource-licence": site.licence_url,
        "wikisource-rightsinfo": site.rightsinfo_url,
        "thumbnail-sizes": THUMBNAIL_STEPS_EVIDENCE,
    }
    if source_url:
        refs["catalogue"] = source_url
    if catalogue_id:
        refs["nlk" if "nl.go.kr" in (source_url or "") else "catalogue-id"] = catalogue_id.group(1)
    for work in works:
        refs[f"wikisource-text:{work['title']}"] = work["url"]

    levels = Counter("missing" if p.quality is None else str(p.quality) for p in scan_pages)
    document = Document(
        id=document_id,
        title=file_name.rsplit(".", 1)[0],
        source_refs=refs,
        holder=holder,
        production=production,
        dating=dating,
        image_rights=Rights(licence=image_licence, holder=holder,
                            attribution=f"{holder or 'unstated holder'}, via Wikimedia Commons, "
                                        f"{commons['description_url']}",
                            evidence=commons["description_url"], checked=checked),
        text_rights=Rights(licence=text_licence, holder=contributors,
                           attribution=f"{contributors}, {index['url']}, {site.licence_text}",
                           evidence=site.rightsinfo_url, checked=checked),
        meta={
            "language": language,
            "scripts": list(scripts),
            "commons": {"title": commons["title"], "revision": commons["revision"],
                        "licence_tags": licence_tags(commons["wikitext"]),
                        "date": date_field, "description": plain(field_of(commons["wikitext"], "description")),
                        "author": field_of(commons["wikitext"], "author"),
                        "page_width": commons["width"], "page_height": commons["height"],
                        "pagecount": commons["pagecount"], "bytes": commons["size"], "sha1": commons["sha1"],
                        "mime": commons["mime"], "extmetadata": commons["extmetadata"]},
            "index": {"title": index["title"], "revision": index["revision"],
                      "work_title": plain(field_of(index["wikitext"], "제목") or field_of(index["wikitext"], "Title")),
                      "publisher": plain(field_of(index["wikitext"], "출판사") or field_of(index["wikitext"], "Publisher")),
                      "year": field_of(index["wikitext"], "연도") or field_of(index["wikitext"], "Year"),
                      "progress": field_of(index["wikitext"], "진행 상황") or field_of(index["wikitext"], "Progress")},
            "reading_pages": works,
            "thumbnail": {"width": width, "native_width": limit,
                          "native_width_source": (native_width_evidence or "operator") if native_width
                          else "commons page size",
                          "steps": list(THUMBNAIL_STEPS), "steps_evidence": THUMBNAIL_STEPS_EVIDENCE},
            "quality_levels": dict(sorted(levels.items())),
            "collected_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "user_agent": USER_AGENT,
        },
    )
    summary = {"document": document_id, "title": document.title, "index": index["url"],
               "pages": len(pages), "listed_pages": sum(p.exists for p in scan_pages),
               "pages_with_text": len(texts), "quality_levels": dict(sorted(levels.items())),
               "thumbnail_width": width}
    return Collected(document=document, pages=pages, texts=texts, summary=summary)


def collect(
    out: Path,
    wiki: str,
    names: Iterable[str],
    *,
    http: Http | None = None,
    image_root: Path | None = None,
    command: str = "collect Wikisource scans",
    **options: Any,
) -> dict[str, Any]:
    """Collect every index into one dataset directory and return a summary per document."""
    http = http or Http()
    out = Path(out)
    site = read_site(http, wiki)
    root = images.images_root(image_root)
    known = {row.url: row for row in images.index(root) if row.superseded_by is None}
    results = [collect_index(http, site, wiki, name, image_root=root, known=known, **options) for name in names]
    documents = [result.document for result in results]
    pages = [page for result in results for page in result.pages]
    texts = [text for result in results for text in result.texts]
    out.mkdir(parents=True, exist_ok=True)
    for table, records, model in (("documents", documents, Document), ("pages", pages, Page),
                                  ("page_texts", texts, PageText)):
        staging = out / f".{table}.parquet"
        tables.write(staging, records, model)
        os.replace(staging, out / f"{table}.parquet")
    counts = {"documents": len(documents), "pages": len(pages), "page_texts": len(texts)}
    sources = sorted({url for document in documents for url in document.source_refs.values()
                      if url.startswith("http")})
    manifest = {"schema_version": tables.SCHEMA_VERSION, "tables": counts, "command": command,
                "geometry": "none", "character_crops": 0, "image_cache": str(root),
                "written_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "documents": [result.summary for result in results], "sources": sources,
                "requests": http.asked}
    staging = out / ".MANIFEST.json"
    staging.write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.replace(staging, out / "MANIFEST.json")
    return {"tables": counts, "documents": [result.summary for result in results]}

