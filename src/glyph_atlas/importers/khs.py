"""Designated heritage items of the 국가유산청 (Korea Heritage Service), with their photographs.

An item is named by the three keys of the 국가유산청 Open API, `ccbaKdcd` (the designation kind, 12
for 보물), `ccbaAsno` (the item number) and `ccbaCtcd` (the province), written `12-0008750000400-24`.
The item's record comes from `SearchKindOpenapiDt.do` and its photographs from `SearchImageOpenapi.do`;
both answer in XML and are kept in `out/upstream/`. Each photograph becomes a page, in the order the
image list gives. A photograph shows the object as it was photographed: an opened spread, a scroll
partly unrolled, several leaves laid side by side. It is not a scan of one page.

The image list states the 공공누리 type of each photograph in `imageNuri`, and the Open API page reads
`A` as 제1유형 (KOGL Type 1, use, commercial use and derivatives allowed with the source named). Only
those photographs are collected; an item with none is skipped and listed as such. The heritage
portal's copyright policy frees only works that carry a 공공누리 mark, which is what `imageNuri`
records.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx

from .. import images, net, tables
from ..schema import Dating, Document, Licence, Page, PageText, Rights

SOURCE = "khs"
HOLDER = "국가유산청"
API = "https://www.khs.go.kr/cha"
RECORD_API = API + "/SearchKindOpenapiDt.do?ccbaKdcd={kind}&ccbaAsno={number}&ccbaCtcd={province}"
IMAGE_LIST_API = API + "/SearchImageOpenapi.do?ccbaKdcd={kind}&ccbaAsno={number}&ccbaCtcd={province}"
ITEM_PAGE = "https://www.heritage.go.kr/heri/cul/culSelectDetail.do?ccbaCpno={cpno}"
API_TERMS = "https://www.khs.go.kr/html/HtmlPage.do?pg=/publicinfo/pbinfo3_0202.jsp&mn=NS_04_04_02"
POLICY = "https://www.khs.go.kr/html/HtmlPage.do?pg=/guide/copyright.jsp&mn=NS_08_03"

KOGL_1_CODE = "A"
KOGL_1_WORDING = "공공누리 제1유형(출처표시)"
ATTRIBUTION = f"{HOLDER}, {KOGL_1_WORDING}"

#: Captions that credit a photograph to a publisher outside the public sector, whose own terms the
#: portal's `imageNuri` cannot speak for. Such a photograph is left out whatever its `imageNuri`.
CREDITED_ELSEWHERE = frozenset({"경남신문", "한국민족대백과"})
#: The Open API masks the name of a private custodian with ＊ (e.g. 범＊＊＊).
MASKED = "＊"

ITEM_ID = re.compile(r"^(?P<kind>\d{2})-(?P<number>\d{13})-(?P<province>\d{2})$")


class RecordError(RuntimeError):
    """A record the Open API did not deliver in the expected shape."""


def item_id(value: str) -> str:
    text = str(value).strip()
    if not ITEM_ID.match(text):
        raise ValueError(f"{value!r}: an item id is ccbaKdcd-ccbaAsno-ccbaCtcd, e.g. 12-0008750000400-24")
    return text


def _keys(iid: str) -> dict[str, str]:
    return ITEM_ID.match(iid).groupdict()


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
) -> dict[str, Any]:
    """Collect the items `ids` into the dataset directory `out` and return a summary.

    The record and image list XML are kept in `out/upstream/` and reused on a rerun; images are fetched
    through `images.fetch`, which skips an image already in the cache. An item whose record, image list
    or any KOGL Type 1 photograph cannot be fetched is left out whole and listed under `unavailable`.
    """
    out = Path(out)
    upstream = out / "upstream"
    ids = [item_id(value) for value in ids]
    checked = checked or datetime.now(UTC).date()
    own_client = client is None
    if own_client:
        client = httpx.Client(timeout=60.0, follow_redirects=True)
    fetch_options = {"client": client, "clock": clock, "sleeper": sleeper}
    documents: list[Document] = []
    pages: list[Page] = []
    collected, skipped, unavailable = [], [], []
    try:
        for iid in ids:
            keys = _keys(iid)
            try:
                record_path = net.download(RECORD_API.format(**keys), upstream / f"{iid}.xml", expected="text",
                                           retries=retries, **fetch_options)
                list_path = net.download(IMAGE_LIST_API.format(**keys), upstream / f"{iid}.images.xml",
                                         expected="text", retries=retries, **fetch_options)
                record = _record(record_path.read_bytes(), iid)
                photos = _image_list(list_path.read_bytes(), iid)
            except net.DownloadError as error:
                unavailable.append({"id": iid, "error": str(error)})
                continue
            except RecordError as error:
                # An error page can arrive as 200 with an XML head; a kept copy would fail every rerun.
                for path in (upstream / f"{iid}.xml", upstream / f"{iid}.images.xml"):
                    path.unlink(missing_ok=True)
                unavailable.append({"id": iid, "error": str(error)})
                continue
            free = [photo for photo in photos
                    if photo.get("imageNuri") == KOGL_1_CODE and photo.get("ccimDesc") not in CREDITED_ELSEWHERE]
            if not free:
                skipped.append({"id": iid, "title": record["ccbaMnm1"],
                                "imageNuri": sorted({photo.get("imageNuri", "") for photo in photos}),
                                "credited_elsewhere": sum(photo.get("ccimDesc") in CREDITED_ELSEWHERE
                                                          for photo in photos)})
                continue
            try:
                held = [images.fetch(_https(photo["imageUrl"]), root=cache, **fetch_options) for photo in free]
            except (net.DownloadError, images.ImageError) as error:
                unavailable.append({"id": iid, "title": record["ccbaMnm1"], "error": str(error)})
                continue
            revision = hashlib.sha256(record_path.read_bytes() + list_path.read_bytes()).hexdigest()
            document, item_pages = build(iid, record, free, held, revision=revision, checked=checked)
            documents.append(document)
            pages.extend(item_pages)
            collected.append({"id": iid, "title": document.title, "pages": len(item_pages),
                              "left_out": len(photos) - len(free),
                              "credited_elsewhere": sum(photo.get("ccimDesc") in CREDITED_ELSEWHERE
                                                        for photo in photos)})
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
    summary = {"source": SOURCE, "holder": HOLDER, "licence": Licence.KOGL_1.value,
               "attribution": ATTRIBUTION, "licence_evidence": API_TERMS,
               "collected": collected, "skipped": skipped, "unavailable": unavailable}
    manifest = {"schema_version": tables.SCHEMA_VERSION, "tables": counts,
                "command": command or "collect 국가유산청 heritage items", "collection": summary}
    staging = out / ".MANIFEST.json"
    staging.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(staging, out / "MANIFEST.json")
    return summary


def _unmasked(value: str | None) -> str | None:
    """A name as the Open API gives it, or None when it is empty or masked."""
    return value if value and MASKED not in value else None


def _https(url: str) -> str:
    """The Open API writes image URLs with http://, which the server only redirects to https://."""
    return "https://" + url.removeprefix("http://") if url.startswith("http://") else url


def _parse(payload: bytes, iid: str, what: str) -> ET.Element:
    try:
        return ET.fromstring(payload)
    except ET.ParseError as error:
        raise RecordError(f"item {iid}: the {what} is not XML") from error


def _record(payload: bytes, iid: str) -> dict[str, str]:
    root = _parse(payload, iid, "record")
    item = root.find("item")
    if item is None:
        raise RecordError(f"item {iid}: the Open API answered with no such item")
    # The keys and ccbaCpno sit on <result>, every other field on <item>.
    record = {child.tag: (child.text or "").strip() for child in root if child.tag != "item"}
    record.update({child.tag: (child.text or "").strip() for child in item})
    keys = _keys(iid)
    found = (record.get("ccbaKdcd"), record.get("ccbaAsno"), record.get("ccbaCtcd"))
    if found != (keys["kind"], keys["number"], keys["province"]):
        raise RecordError(f"item {iid}: the Open API answered with a different item {'-'.join(map(str, found))}")
    if not record.get("ccbaMnm1"):
        raise RecordError(f"item {iid}: the record has no name")
    return record


def _image_list(payload: bytes, iid: str) -> list[dict[str, str]]:
    """The photographs of an image list. The list writes them all into one `<item>` as a run of
    `sn`, `imageNuri`, `imageUrl` and `ccimDesc` fields, so each `sn` starts the next photograph."""
    root = _parse(payload, iid, "image list")
    photos: list[dict[str, str]] = []
    for item in root.iter("item"):
        for child in item:
            if child.tag == "sn" or not photos:
                photos.append({})
            photos[-1][child.tag] = (child.text or "").strip()
    return [photo for photo in photos if photo.get("imageUrl")]


def build(
    iid: str, record: dict[str, str], photos: list[dict[str, str]], held: list[images.ImageRecord], *,
    revision: str, checked: date,
) -> tuple[Document, list[Page]]:
    """The document and pages of one item whose KOGL Type 1 photographs are cached."""
    document_id = f"{SOURCE}:{iid}"
    rights = Rights(licence=Licence.KOGL_1, holder=HOLDER, attribution=ATTRIBUTION, evidence=API_TERMS,
                    checked=checked)
    category = [record.get(key) for key in ("gcodeName", "bcodeName", "mcodeName", "scodeName")]
    meta: dict[str, Any] = {
        "language": "ko",
        "title_hanja": record.get("ccbaMnm2") or None,
        "designation": record.get("ccmaName") or None,
        "designated": record.get("ccbaAsdt") or None,
        "category": [name for i, name in enumerate(category) if name and name not in category[:i]],
        "quantity": record.get("ccbaQuan") or None,
        "owner": _unmasked(record.get("ccbaPoss")),
        "masked": [field for field in ("ccbaAdmin", "ccbaPoss") if MASKED in (record.get(field) or "")],
        "location": record.get("ccbaLcad") or None,
        "description": record.get("content") or None,
        "kogl_code": KOGL_1_CODE,
        "licence_wording": KOGL_1_WORDING,
        "copyright_policy": POLICY,
        "photographs": "Each page is a photograph of the object, which may show several leaves or an opened roll.",
        "record_sha256": revision,
    }
    meta = {key: value for key, value in meta.items() if value not in (None, [], "")}
    period = record.get("ccceName") or ""
    document = Document(
        id=document_id,
        title=record["ccbaMnm1"],
        source_refs={SOURCE: iid, "catalogue": ITEM_PAGE.format(cpno=record.get("ccbaCpno", "")),
                     "record": RECORD_API.format(**_keys(iid))},
        holder=_unmasked(record.get("ccbaAdmin")),
        dating=[Dating(literal=period, evidence="ccceName")] if period else [],
        image_rights=rights,
        meta=meta,
    )
    pages = []
    for seq, (photo, image) in enumerate(zip(photos, held, strict=True), 1):
        pages.append(Page(
            id=f"{document_id}:{seq}", document_id=document_id, seq=seq,
            image=image.url, width=image.width, height=image.height, sha256=image.sha256,
            meta={key: value for key, value in
                  {"sn": photo.get("sn"), "caption": photo.get("ccimDesc"), "imageNuri": photo["imageNuri"]}.items()
                  if value},
        ))
    return document, pages
