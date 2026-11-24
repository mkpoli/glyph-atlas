"""Wikimedia Commons scans (PDF or DjVu) that have no Wikisource index.

One Commons file becomes one document. Every page of the file becomes a dataset page whose image is
Commons' rendering of that page, fetched through the same per-page thumbnail route and image cache as
`wikisource_scans`. The pages are untranscribed: the dataset gets no page texts and no lines, and the
character review route cuts them later.

The file page states the bibliography; each field is read from the template parameters that exist
(`byline`, `title`, `volume`, `版本`, `出版者`, `出版时间`, `责任者`, `索取号` on the National Library of China
templates, `Author`, `Title`, `Publisher`, `Publication date` on `{{Book}}`) and an absent one stays
empty. The scan's licence comes from the file page's `LicenseShortName`, as for `wikisource_scans`; a
file page whose licence section is transcluded by its template keeps no `licence_tags`, so the
categories Commons files it under say why it is public domain. "Public domain" is taken only with an
expired-term rationale (`PD-old…`); under another one, such as `PD-China` on a Republican-era title,
the licence stays unknown.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from .. import images, rights
from ..schema import Dating, Document, Licence, Page, Rights
from .wikisource_scans import (
    COMMONS,
    EXTMETADATA,
    THUMBNAIL_STEPS,
    THUMBNAIL_STEPS_EVIDENCE,
    USER_AGENT,
    Collected,
    Http,
    WikiError,
    fetch_image,
    field_of,
    licence_tags,
    plain,
    read_commons_file,
    thumbnail_url,
    thumbnail_width,
    write_dataset,
    year_of,
)

_BRACKET_YEAR = re.compile(r"\[(\d{4})\]")
_REPUBLIC_YEAR = re.compile(r"民[國国]\s*(\d{1,2})\s*年")
_REPUBLIC = re.compile(r"民[國国]")
_TAG = re.compile(r"<[^>]*>")
_PREFIX = re.compile(r"^(?:file|image)\s*:\s*", re.IGNORECASE)
_PD_CATEGORY = re.compile(r"^(?:CC-)?PD\b(?!.*\bmissing\b)")
_EXPIRED = re.compile(r"^(?:PD-scan \()?PD-old(?:-\d+)?(?:-expired)?(?:-\d+)?\)?$")
#: The years of the Republican era on the mainland, for a date that names the era and no year.
REPUBLIC = (1912, 1949)


def file_title(name: str) -> str:
    """A Commons file title with its `File:` prefix and spaces for underscores."""
    return "File:" + _PREFIX.sub("", name.strip()).replace("_", " ").strip()


def rationales(categories: str | None) -> list[str]:
    """The public-domain categories Commons files a page under, e.g. `PD-old-100-expired`."""
    return [name for name in (part.strip() for part in (categories or "").split("|")) if _PD_CATEGORY.match(name)]


def category_files(http: Http, category: str, match: str) -> list[str]:
    """The titles of the files in a Commons category whose title matches the regular expression."""
    title = "Category:" + category.removeprefix("Category:")
    answer = http.query(COMMONS, {"list": "categorymembers", "cmtitle": title, "cmtype": "file",
                                  "cmlimit": "max"})
    pattern = re.compile(match)
    return sorted(member["title"] for member in answer.get("categorymembers") or []
                  if pattern.search(member["title"]))


def first_field(wikitext: str, *names: str) -> str | None:
    """The first of the template parameters that is present and not empty."""
    for name in names:
        value = field_of(wikitext, name)
        if value:
            return value
    return None


def credit_of(extmetadata_credit: str | None) -> str | None:
    """The plain text of the `Credit` Commons states for the file."""
    if not extmetadata_credit:
        return None
    return re.sub(r"\s+", " ", _TAG.sub("", extmetadata_credit)).strip() or None


def published_year(value: str | None) -> int | None:
    """The year of a stated date: `1847` in `清道光27年[1847]`, `1936` in `民國25年`, or a bare year."""
    found = _BRACKET_YEAR.search(value or "")
    if found:
        return int(found.group(1))
    republic = _REPUBLIC_YEAR.search(value or "")
    return 1911 + int(republic.group(1)) if republic else year_of(value)


def collect_file(
    http: Http,
    name: str,
    *,
    image_root: Path | None = None,
    native_width: int | None = None,
    native_width_evidence: str | None = None,
    scripts: Iterable[str] = (),
    language: str | None = None,
    production: str = "unknown",
    origin: str = "unknown",
    known: dict[str, images.ImageRecord] | None = None,
    checked: date | None = None,
) -> Collected:
    """One Commons file as a document and its untranscribed pages."""
    checked = checked or datetime.now(UTC).date()
    known = known if known is not None else {}

    commons = read_commons_file(http, file_title(name).removeprefix("File:"),
                                extmetadata=(*EXTMETADATA, "Credit", "Categories"))
    # The title as Commons spells it names the document and the thumbnail path, whatever was asked for.
    title = file_title(commons["title"])
    file_name = title.removeprefix("File:")
    pagecount = commons.get("pagecount")
    if not pagecount:
        raise WikiError(f"{COMMONS}: {title} is not a paged file")
    wikitext = commons["wikitext"]
    limit = native_width or commons["width"]
    width = thumbnail_width(limit)
    key = hashlib.sha256(f"{COMMONS}:{title}".encode()).hexdigest()[:24]
    document_id = f"commons:scan:{key}"

    pages: list[Page] = []
    for number in range(1, pagecount + 1):
        url = thumbnail_url(file_name, number, width)
        record = fetch_image(http, url, root=image_root, known=known)
        pages.append(Page(
            # `seq` counts from zero, as every page's does; the id and `pdf_page` keep the file's own number.
            id=f"{document_id}:{number}", document_id=document_id, seq=number - 1, image=url,
            width=record.width, height=record.height, sha256=record.sha256,
            meta={"pdf_page": number, "commons_file": commons["description_url"], "thumbnail_width": width,
                  "geometry": "none"},
        ))

    work = plain(first_field(wikitext, "title", "Title"))
    volume = plain(first_field(wikitext, "volume", "Volume"))
    byline = plain(first_field(wikitext, "byline", "责任者", "Author"))
    publisher = plain(first_field(wikitext, "出版者", "Publisher"))
    edition = plain(first_field(wikitext, "版本", "Edition"))
    shelfmark = plain(first_field(wikitext, "索取号"))
    date_field = first_field(wikitext, "出版时间", "Publication date")
    dating: list[Dating] = []
    year = published_year(date_field)
    if year is not None:
        dating.append(Dating(literal=date_field, start=year, end=year, kind="publication",
                             evidence=commons["description_url"]))
    elif date_field and _REPUBLIC.search(date_field):
        dating.append(Dating(literal=date_field, start=REPUBLIC[0], end=REPUBLIC[1], kind="publication",
                             evidence=commons["description_url"]))
    elif date_field:
        dating.append(Dating(literal=date_field, kind="unknown", evidence=commons["description_url"]))

    extmetadata = commons["extmetadata"] or {}
    holder = credit_of(extmetadata.get("Credit"))
    why = rationales(extmetadata.pop("Categories", None))
    licence = rights.resolve(licence=extmetadata.get("LicenseShortName")).licence
    if licence is Licence.PUBLIC_DOMAIN and not any(_EXPIRED.match(name) for name in why):
        licence = Licence.UNKNOWN
    refs = {
        "commons-file": commons["description_url"],
        "commons-original": commons["url"],
        "commons-api": f"https://{COMMONS}/w/api.php",
        "thumbnail-sizes": THUMBNAIL_STEPS_EVIDENCE,
    }
    nlc = {name: value for name in ("catid", "db", "dbid", "bookid", "volumeid", "volumenth", "volumetotal")
           if (value := field_of(wikitext, name))}
    document = Document(
        id=document_id,
        title=" ".join(part for part in (work, volume) if part) or file_name.rsplit(".", 1)[0],
        source_refs=refs,
        holder=holder,
        shelfmark=shelfmark,
        production=production,
        origin=origin,
        dating=dating,
        image_rights=Rights(licence=licence, holder=holder,
                            attribution=", ".join(filter(None, (holder, "Wikimedia Commons", commons["description_url"]))),
                            evidence=commons["description_url"], checked=checked),
        meta={
            "language": language,
            "scripts": list(scripts),
            "commons": {"title": commons["title"], "revision": commons["revision"],
                        "licence_tags": licence_tags(wikitext), "licence_rationale": why, "date": date_field,
                        "author": byline, "work": work, "volume": volume, "publisher": publisher,
                        "edition": edition, "nlc": nlc,
                        "page_width": commons["width"], "page_height": commons["height"],
                        "pagecount": pagecount, "bytes": commons["size"], "sha1": commons["sha1"],
                        "mime": commons["mime"], "extmetadata": extmetadata},
            "thumbnail": {"width": width, "native_width": limit,
                          "native_width_source": (native_width_evidence or "operator") if native_width
                          else "commons page size",
                          "steps": list(THUMBNAIL_STEPS), "steps_evidence": THUMBNAIL_STEPS_EVIDENCE},
            "collected_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "user_agent": USER_AGENT,
        },
    )
    summary = {"document": document_id, "title": document.title, "commons_file": commons["description_url"],
               "pages": len(pages), "image_bytes": commons["size"], "thumbnail_width": width,
               "page_size": [pages[0].width, pages[0].height] if pages else None}
    return Collected(document=document, pages=pages, texts=[], summary=summary)


def collect(
    out: Path,
    titles: Iterable[str],
    *,
    http: Http | None = None,
    image_root: Path | None = None,
    command: str = "collect Commons scans",
    **options: Any,
) -> dict[str, Any]:
    """Collect every Commons file into one dataset directory and return a summary per document."""
    http = http or Http()
    root = images.images_root(image_root)
    known = {row.url: row for row in images.index(root) if row.superseded_by is None}
    # One spelling of a title per file: a file named twice would be written twice under one id.
    names = dict.fromkeys(file_title(title) for title in titles)
    results = [collect_file(http, name, image_root=root, known=known, **options) for name in names]
    return write_dataset(Path(out), results, http=http, root=root, command=command)
