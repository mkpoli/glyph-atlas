"""Bibliographic records of 国書データベース (国文学研究資料館) for documents held here.

A document is matched to its 国書データベース record by the record's id, the `bid`: the `nijl-bid`
source reference CODH books carry, or the bid in an IIIF manifest or image address served from
`kokusho.nijl.ac.jp` or `kotenseki.nijl.ac.jp`. The record (`/api/biblioDetail/{bid}`, the JSON the
database's own pages read) is kept under `meta["kokusho"]` whole in the fields the dataset uses, with
the record URL, the DOI and the date it was read. It fills only what a document leaves empty: the
holder, the shelfmark, and the production when it is `unknown` (刊 printed, 写 handwritten). A
document's own image rights are never replaced; the record's per-item licence is kept beside them, so
a difference can be read and acted on.

国書データベース's terms allow free use of its bibliographic data and ask for the title, the holder,
a note of any change, and the database's name (https://kokusho.nijl.ac.jp/page/terms.html).
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable
from datetime import date
from pathlib import Path
from typing import Any

from . import net
from .schema import Document, Licence

SITE = "https://kokusho.nijl.ac.jp"
SOURCE = "国書データベース（国文学研究資料館）"
DETAIL = SITE + "/api/biblioDetail/{bid}"
RECORD = SITE + "/biblio/{bid}"

_BID_URL = re.compile(r"(?:kokusho|kotenseki)\.nijl\.ac\.jp/(?:biblio|api/iiif)/(\d{9})\b")

#: The record's licence link, read into the dataset's licence vocabulary. A link that names none of
#: these, the database's own usage page among them, is a statement to ask about: `restricted`.
_LICENCES = (
    ("publicdomain/mark/1.0", Licence.PDM),
    ("licenses/by-nc-nd/4.0", Licence.CC_BY_NC_ND_4),
    ("licenses/by-nc-sa/4.0", Licence.CC_BY_NC_SA_4),
    ("licenses/by-nc/4.0", Licence.CC_BY_NC_4),
    ("licenses/by-nd/4.0", Licence.CC_BY_ND_4),
    ("licenses/by-sa/4.0", Licence.CC_BY_SA_4),
    ("licenses/by/4.0", Licence.CC_BY_4),
    ("rightsstatements.org/page/NoC-CR", Licence.RS_NOC_CR),
    ("rightsstatements.org/vocab/NoC-CR", Licence.RS_NOC_CR),
)

#: 刊写: how the item was made. A record may also leave it empty or give both.
_PRODUCTION = {"刊": "printed", "写": "handwritten"}


def bid_of(document: Document) -> str | None:
    """The 国書データベース id a document names, or None."""
    if bid := document.source_refs.get("nijl-bid"):
        return bid if re.fullmatch(r"\d{9}", bid) else None
    for value in (*document.source_refs.values(), json.dumps(document.meta, ensure_ascii=False)):
        if match := _BID_URL.search(str(value)):
            return match.group(1)
    return None


def licence_of(link: str | None) -> Licence:
    """The licence a record's `licenselink` states."""
    for fragment, licence in _LICENCES:
        if link and fragment in link:
            return licence
    return Licence.RESTRICTED


def fetch(bid: str, cache: Path, *, fetcher: Callable[..., Path] = net.download) -> dict[str, Any]:
    """The record for `bid`, read once and then from `cache`."""
    dest = cache / f"{bid}.json"
    fetcher(DETAIL.format(bid=bid), dest, expected="json")
    return json.loads(dest.read_text(encoding="utf-8"))


def summary(record: dict[str, Any], retrieved: date) -> dict[str, Any]:
    """The fields of a record the dataset keeps, with where and when they were read."""
    bid = str(record["bid"])
    text = lambda key: (record.get(key) or "").strip() or None
    items = lambda key: [item.strip() for item in record.get(key) or [] if str(item).strip()]
    return {
        "bid": bid,
        "record_url": RECORD.format(bid=bid),
        "doi": text("doi"),
        "title": text("top_shomeih"),
        "holder": text("smeishoh"),
        "collection": text("cmeishoh"),
        "shelfmark": text("w_seikyu"),
        "kansha": text("kansha"),
        "publication": items("bpublish"),
        "notes": items("chuki"),
        "works": [{"wid": str(work.get("wid")), "name": work.get("name")} for work in record.get("work") or []],
        "licence": licence_of(record.get("licenselink")).value,
        "licence_url": text("licenselink"),
        "manifest": text("manifest"),
        "source": SOURCE,
        "retrieved": retrieved.isoformat(),
    }


def enrich(document: Document, record: dict[str, Any], retrieved: date) -> Document:
    """`document` with the record kept and its empty fields filled from it."""
    kept = summary(record, retrieved)
    update: dict[str, Any] = {
        "source_refs": {**document.source_refs, "nijl-bid": kept["bid"]},
        "meta": {**document.meta, "kokusho": kept},
    }
    if not document.holder and kept["holder"]:
        update["holder"] = kept["holder"]
    if not document.shelfmark and kept["shelfmark"]:
        update["shelfmark"] = kept["shelfmark"]
    if document.production == "unknown" and (made := _PRODUCTION.get(kept["kansha"] or "")):
        update["production"] = made
    return document.model_copy(update=update)


def enrich_all(documents: Iterable[Document], cache: Path, retrieved: date,
               *, fetcher: Callable[..., Path] = net.download) -> tuple[list[Document], dict[str, int]]:
    """Every document, those with a 国書データベース id enriched, and what was filled."""
    counts = {"documents": 0, "matched": 0, "failed": 0, "holder": 0, "shelfmark": 0, "production": 0, "doi": 0}
    out = []
    for document in documents:
        counts["documents"] += 1
        bid = bid_of(document)
        if bid is None:
            out.append(document)
            continue
        try:
            record = fetch(bid, cache, fetcher=fetcher)
        except (net.DownloadError, json.JSONDecodeError, KeyError):
            counts["failed"] += 1
            out.append(document)
            continue
        if str(record.get("bid")) != bid:
            counts["failed"] += 1
            out.append(document)
            continue
        enriched = enrich(document, record, retrieved)
        counts["matched"] += 1
        counts["holder"] += enriched.holder != document.holder
        counts["shelfmark"] += enriched.shelfmark != document.shelfmark
        counts["production"] += enriched.production != document.production
        counts["doi"] += bool(enriched.meta["kokusho"]["doi"])
        out.append(enriched)
    return out, counts
