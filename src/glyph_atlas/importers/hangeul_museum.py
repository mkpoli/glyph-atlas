"""Records of the 국립한글박물관 아카이브, with their images and 판독.

A record is read from the archive's own JSON view and becomes one document; each file of its
`imgList` becomes a page, in the order the record lists them, and its image goes into the image
cache. The 판독 (`orgItptr`) covers the whole record and carries no page breaks, so it is kept
whole as the page text of the first page, and the document says so in `meta["text_placement"]`.

Only records the archive marks `CD00167` are collected. The archive's item template renders that
code as 공공누리 제1유형(출처표시), KOGL Type 1: use, commercial use and derivatives are allowed with
the source named. A record under any other code is skipped and listed as such.
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

import httpx

from .. import images, net, tables
from ..schema import Dating, Document, Licence, Page, PageText, Rights

SOURCE = "hangeul-museum"
HOLDER = "국립한글박물관"
BASE = "https://archives.hangeul.go.kr/ko/M000000591"
RECORD_API = BASE + "/api/archv/ot/view?rcrdId={id}"
IMAGE_API = BASE + "/api/archv/image/{sn}"
ITEM_PAGE = BASE + "/archv/ot/view?rcrdId={id}"
POLICY = "https://archives.hangeul.go.kr/ko/M000000614/html/view"

KOGL_1_CODE = "CD00167"
KOGL_1_WORDING = "공공누리 제1유형(출처표시)"
ATTRIBUTION = f"{HOLDER}, {KOGL_1_WORDING}"

PRODUCTION = {"필사본": "handwritten", "목판본": "printed/woodblock", "활자본": "printed/type"}
#: `chctCdNm`, the record's statement of the scripts it is written in, as ISO 15924 codes.
SCRIPTS = {"순한글": ["Hang"], "국한문 혼용": ["Hang", "Hani"], "순한문": ["Hani"]}
HAN = re.compile(r"[㐀-䶿一-鿿豈-﫿\U00020000-\U0003134f]")
PRIVATE_USE = re.compile(r"[-]")
TRANSCRIBER = re.compile(r"\s*판독자\s*[:：]\s*(?P<name>[^\n]+?)\s*$")
CENTURIES = re.compile(r"(\d{1,2})세기(?:\s*~\s*(\d{1,2})세기)?")
YEAR = re.compile(r"(\d{4})년")


class RecordError(RuntimeError):
    """A record the archive did not deliver in the expected shape."""


def record_id(value: str | int) -> str:
    text = str(value).strip()
    if not text.isdecimal():
        raise ValueError(f"{value!r}: a record id is a number")
    return text


def collect(
    ids: Iterable[str | int],
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
    """Collect the records `ids` into the dataset directory `out` and return a summary.

    Record JSON is kept in `out/upstream/<id>.json` and reused on a rerun; images are fetched through
    `images.fetch`, which skips an image already in the cache. A record whose JSON or any image cannot
    be fetched is left out whole and listed under `unavailable`.
    """
    out = Path(out)
    upstream = out / "upstream"
    ids = [record_id(value) for value in ids]
    checked = checked or datetime.now(UTC).date()
    own_client = client is None
    if own_client:
        client = httpx.Client(timeout=60.0, follow_redirects=True)
    fetch_options = {"client": client, "clock": clock, "sleeper": sleeper}
    documents: list[Document] = []
    pages: list[Page] = []
    texts: list[PageText] = []
    collected, skipped, unavailable = [], [], []
    try:
        for rid in ids:
            try:
                path = net.download(RECORD_API.format(id=rid), upstream / f"{rid}.json", expected="json",
                                    retries=retries, **fetch_options)
                payload = path.read_bytes()
                record = _record(payload, rid)
            except (net.DownloadError, RecordError) as error:
                unavailable.append({"id": rid, "error": str(error)})
                continue
            code = record.get("koglCdId")
            if code != KOGL_1_CODE:
                skipped.append({"id": rid, "title": record.get("rcrdNm"), "koglCdId": code})
                continue
            try:
                held = [
                    images.fetch(IMAGE_API.format(sn=item["atchFileSn"]), root=cache, **fetch_options)
                    for item in record["imgList"]
                ]
            except (net.DownloadError, images.ImageError) as error:
                unavailable.append({"id": rid, "title": record.get("rcrdNm"), "error": str(error)})
                continue
            document, record_pages, text = build(record, payload, held, checked=checked)
            documents.append(document)
            pages.extend(record_pages)
            texts.extend(text)
            collected.append({"id": rid, "title": document.title, "koglCdId": code,
                              "pages": len(record_pages), "has_text": bool(text)})
    finally:
        if own_client:
            client.close()

    out.mkdir(parents=True, exist_ok=True)
    counts = {"documents": len(documents), "pages": len(pages), "page_texts": len(texts)}
    for name, records, model in (("documents", documents, Document), ("pages", pages, Page),
                                 ("page_texts", texts, PageText)):
        staging = out / f".{name}.parquet"
        tables.write(staging, records, model)
        os.replace(staging, out / f"{name}.parquet")
    summary = {"source": SOURCE, "holder": HOLDER, "licence": Licence.KOGL_1.value,
               "attribution": ATTRIBUTION, "licence_evidence": POLICY,
               "collected": collected, "skipped": skipped, "unavailable": unavailable}
    manifest = {"schema_version": tables.SCHEMA_VERSION, "tables": counts,
                "command": command or "collect 국립한글박물관 아카이브 records", "collection": summary}
    staging = out / ".MANIFEST.json"
    staging.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(staging, out / "MANIFEST.json")
    return summary


def _record(payload: bytes, rid: str) -> dict[str, Any]:
    try:
        record = json.loads(payload)
    except ValueError as error:
        raise RecordError(f"record {rid}: not JSON") from error
    if not isinstance(record, dict) or str(record.get("rcrdId")) != rid or not record.get("rcrdNm"):
        raise RecordError(f"record {rid}: the archive answered with no such record")
    if not isinstance(record.get("imgList"), list):
        raise RecordError(f"record {rid}: no image list")
    return record


def build(
    record: dict[str, Any], payload: bytes, held: list[images.ImageRecord], *, checked: date
) -> tuple[Document, list[Page], list[PageText]]:
    """The document, pages and page text of one KOGL Type 1 record whose images are cached."""
    rid = str(record["rcrdId"])
    relic = record.get("relicVO") or {}
    document_id = f"{SOURCE}:{rid}"
    revision = hashlib.sha256(payload).hexdigest()
    rights = Rights(licence=Licence.KOGL_1, holder=HOLDER, attribution=ATTRIBUTION, evidence=POLICY,
                    checked=checked)

    raw_text = (record.get("orgItptr") or "").strip()
    transcriber = None
    found = TRANSCRIBER.search(raw_text)
    if found:
        transcriber = found.group("name")
        raw_text = raw_text[: found.start()].rstrip()

    scripts = list(SCRIPTS.get(relic.get("chctCdNm") or "", []))
    if HAN.search(raw_text) and "Hani" not in scripts:
        scripts.append("Hani")
    meta: dict[str, Any] = {
        "language": "ko",
        "scripts": scripts,
        "scripts_evidence": f"chctCdNm {relic.get('chctCdNm')!r}" if relic.get("chctCdNm") else None,
        "record_number": record.get("rcrdNum"),
        "title_hanja": relic.get("nameCn") or None,
        "author": relic.get("author") or None,
        "edition": relic.get("editionCdNm") or None,
        "size": relic.get("size") or None,
        "description": relic.get("relicDesc") or None,
        "commentary": record.get("orgTxt") or None,
        "tags": [tag for tag in (record.get("tags") or "").split(",") if tag],
        "period": record.get("histClCdNm") or None,
        "date_note": record.get("histInfo") or None,
        "location": record.get("orLoc") or None,
        "kogl_code": record.get("koglCdId"),
        "licence_wording": KOGL_1_WORDING,
        "record_sha256": revision,
    }
    if raw_text:
        meta["text_placement"] = (
            "The 판독 covers the whole record and marks no page breaks; it is kept whole as the text of page 1."
        )
        if transcriber:
            meta["transcriber"] = transcriber
        pua = len(PRIVATE_USE.findall(raw_text))
        if pua:
            meta["private_use_code_points"] = pua
            meta["private_use_note"] = (
                "The 판독 contains Private Use Area code points, kept as delivered. They probably encode "
                "old Hangul syllables in a PUA convention; which one is uncertain, so nothing is converted."
            )
    else:
        meta["text_placement"] = "The archive gives no 판독 for this record; the pages carry no text."
    meta = {key: value for key, value in meta.items() if value not in (None, [], "")}

    text_rights = None
    if raw_text:
        credit = f"판독: {transcriber}; {ATTRIBUTION}" if transcriber else ATTRIBUTION
        text_rights = rights.model_copy(update={"attribution": credit})
    document = Document(
        id=document_id,
        title=record["rcrdNm"],
        source_refs={SOURCE: rid, "catalogue": ITEM_PAGE.format(id=rid), "record": RECORD_API.format(id=rid)},
        holder=HOLDER,
        shelfmark=relic.get("relicMngNum") or None,
        production=PRODUCTION.get(relic.get("editionCdNm") or "", "unknown"),
        dating=_dating(record),
        image_rights=rights,
        text_rights=text_rights,
        meta=meta,
    )

    pages = []
    for seq, (item, image) in enumerate(zip(record["imgList"], held, strict=True), 1):
        pages.append(Page(
            id=f"{document_id}:{seq}", document_id=document_id, seq=seq,
            image=image.url, width=image.width, height=image.height, sha256=image.sha256,
            transcription={"source": SOURCE, "entry id": rid, "revision": revision} if raw_text and seq == 1 else {},
            meta={"atchFileSn": item["atchFileSn"], "file_name": item.get("orgnlFileNm")},
        ))
    texts = []
    if raw_text and pages:
        texts.append(PageText(page_id=pages[0].id, source=SOURCE, revision=revision, text_raw=raw_text))
    return document, pages, texts


def _dating(record: dict[str, Any]) -> list[Dating]:
    """The archive's period class and its date note, each as a dating with the interval it states."""
    found = []
    for field in ("histClCdNm", "histInfo"):
        literal = (record.get(field) or "").strip()
        if not literal:
            continue
        start = end = None
        years = [int(year) for year in YEAR.findall(literal)]
        centuries = CENTURIES.search(literal)
        if years:
            start, end = min(years), max(years)
        elif centuries:
            first = int(centuries.group(1))
            last = int(centuries.group(2) or first)
            start, end = (first - 1) * 100 + 1, last * 100
            if literal.startswith("한글 창제 이후"):
                start = None
        kind = "publication" if "刊" in literal else "unknown"
        found.append(Dating(literal=literal, start=start, end=end, kind=kind, evidence=field))
    return found
