"""Wikisource discovery and import, incrementally.

Wikisource is the one large source the atlas can read without a bulk download: its
search API answers ``insource:"<character>"`` directly, so a character query becomes a
handful of HTTP requests instead of an archive. That matters for rare characters,
where the question is "does anyone have this at all" and the answer is often a
single page.

What this module does *not* do: treat a Wikisource hit as a manuscript crop. A
Wikisource page is a **transcription**, often of a printed edition, sometimes
proofread against a scan in the ``Page:`` namespace. An occurrence found here is a
text occurrence with a page and a revision and a licence — never a located glyph.
When the page belongs to a proofread index we also record the scan's file page, which
is what a later step would need to locate an image.

Rights: Wikisource transcription text is CC BY-SA 4.0 (or public domain for
PD-original works). The underlying scan has its own terms. Both are recorded, and
neither is guessed: the site licence comes from the API, the scan's from the file
page.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .. import net
from .occurrence import find_occurrences

DEFAULT_HOST = "ja.wikisource.org"

#: Namespaces whose content is transcribed source text.
TEXT_NAMESPACES = frozenset({0, 250})  # main, Page:

#: Namespaces that are transcription *conventions* or navigation, never occurrences.
#: ``Index:`` (252) is the important one: it holds notes like 「𪜈」は「トモ」 — a
#: statement about how the project transcribes, not a place a manuscript writes the
#: character. Counting those would inflate a ligature with its own documentation.
EXCLUDED_NAMESPACES = frozenset({4, 10, 12, 14, 100, 102, 252})


def namespace_of(title: str) -> int:
    """The namespace number a title belongs to, by its prefix."""
    if ":" not in title:
        return 0
    prefix = title.split(":", 1)[0].strip().lower()
    return {
        "page": 250,
        "index": 252,
        "file": 6,
        "image": 6,
        "category": 14,
        "template": 10,
        "help": 12,
        "wikisource": 4,
        "author": 102,
        "portal": 100,
        "mediawiki": 8,
    }.get(prefix, 0)


def is_text_namespace(title_or_ns: str | int) -> bool:
    """Whether a page carries transcribed source text."""
    ns = namespace_of(title_or_ns) if isinstance(title_or_ns, str) else title_or_ns
    return ns in TEXT_NAMESPACES and ns not in EXCLUDED_NAMESPACES


#: Wikisource's own terms for the transcription text.
SITE_LICENCE = "CC BY-SA 4.0"
SITE_LICENCE_URL = "https://wikisource.org/wiki/Wikisource:Copyright"
#: The API will not answer a regex search over an unbounded set; keep batches small.
API_BATCH = 20
DEFAULT_CACHE = Path("cache") / "wikisource"


@dataclass
class WikisourcePage:
    """One Wikisource page, with the provenance a search result needs."""

    title: str
    pageid: int | None
    revision: int | None
    timestamp: str | None
    url: str | None
    namespace: int | None
    wikitext: str = ""
    #: For a ``Page:`` namespace page, the scan it was proofread against.
    index_title: str | None = None
    file_page: str | None = None
    host: str = DEFAULT_HOST
    licence: str = SITE_LICENCE
    licence_url: str = SITE_LICENCE_URL
    licence_evidence: str = "site-wide Wikisource terms"
    meta: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class Wikisource:
    """A thin, cached client. One instance = one host."""

    def __init__(
        self, host: str = DEFAULT_HOST, cache: str | Path | None = None, *, pause: float | None = None
    ):
        self.host = host
        self.cache = Path(cache) if cache is not None else DEFAULT_CACHE / host
        self.cache.mkdir(parents=True, exist_ok=True)
        self.pause = pause

    # ------------------------------------------------------------------ HTTP
    def _api(self, params: dict[str, Any], *, bucket: str) -> dict[str, Any]:
        params = {**params, "format": "json", "formatversion": 2}
        import urllib.parse

        query = urllib.parse.urlencode(params, doseq=True)
        dest = self.cache / bucket / (net_hash(query) + ".json")
        if not dest.exists():
            net.download(f"https://{self.host}/w/api.php?{query}", dest, expected="json", pause=self.pause)
        payload = json.loads(dest.read_text(encoding="utf-8"))
        if "error" in payload or "query" not in payload:
            # maxlag and other API errors can use HTTP 200. Never cache them as an empty book.
            dest.unlink(missing_ok=True)
            raise RuntimeError("Wikisource API did not return query data")
        return payload

    # -------------------------------------------------------------- discovery
    def search(self, char: str, *, limit: int = 20) -> list[str]:
        """Titles whose wikitext contains `char`.

        ``insource:`` is a server-side content search, so this is one request and no
        corpus download. It is exact-string, not fuzzy: a page that writes the
        character is returned, a page that writes an expansion is not.
        """
        titles: list[str] = []
        offset = 0
        while len(titles) < limit:
            batch = min(API_BATCH, limit - len(titles))
            payload = self._api(
                {
                    "action": "query",
                    "list": "search",
                    "srsearch": f'insource:"{char}"',
                    "srlimit": batch,
                    "sroffset": offset,
                    "srnamespace": "*",
                },
                bucket="search",
            )
            hits = (payload.get("query") or {}).get("search") or []
            if not hits:
                break
            titles.extend(h["title"] for h in hits)
            if "continue" not in payload:
                break
            offset = payload["continue"].get("sroffset", offset + batch)
        return titles[:limit]

    def search_reported_total(self, char: str) -> int | None:
        payload = self._api(
            {"action": "query", "list": "search", "srsearch": f'insource:"{char}"', "srlimit": 1},
            bucket="search",
        )
        return ((payload.get("query") or {}).get("searchinfo") or {}).get("totalhits")

    # ------------------------------------------------------------------ fetch
    def pages(self, titles: Sequence[str]) -> list[WikisourcePage]:
        """Fetch wikitext, revision and URL for each title."""
        out: list[WikisourcePage] = []
        for start in range(0, len(titles), API_BATCH):
            chunk = list(titles[start : start + API_BATCH])
            payload = self._api(
                {
                    "action": "query",
                    "prop": "revisions|info",
                    "rvprop": "content|ids|timestamp",
                    "rvslots": "main",
                    "inprop": "url",
                    "titles": "|".join(chunk),
                    "maxlag": 5,
                },
                bucket="pages",
            )
            if not isinstance((payload.get("query") or {}).get("pages"), list):
                raise TypeError("Wikisource page request did not return page data")
            for page in payload["query"]["pages"]:
                if page.get("missing"):
                    continue
                revision = (page.get("revisions") or [{}])[0]
                content = ((revision.get("slots") or {}).get("main") or {}).get("content", "")
                title = page["title"]
                out.append(
                    WikisourcePage(
                        title=title,
                        pageid=page.get("pageid"),
                        revision=revision.get("revid"),
                        timestamp=revision.get("timestamp"),
                        url=page.get("fullurl"),
                        namespace=page.get("ns"),
                        wikitext=content or "",
                        index_title=_index_of(title),
                        file_page=_file_of(content or ""),
                        host=self.host,
                    )
                )
        return out

    # -------------------------------------------------------------- extraction
    def occurrences(self, titles: Sequence[str], char: str) -> list[dict[str, Any]]:
        """Every occurrence of `char` in the transcribed pages among these titles."""
        return occurrences_in_pages(self.pages(titles), char)

    def scan_page(self, index_title: str, page_number: int) -> dict[str, Any]:
        """The proofread scan behind a ``Page:`` — the image a crop would come from.

        Reads the ``Index:`` page's ``File:`` reference and the ``Page:`` wikitext, so
        a later step can place a character on the scan. No image is downloaded here.
        """
        page_title = f"Page:{index_title.split(':', 1)[-1]}/{page_number}"
        index = self.pages([index_title])
        body = self.pages([page_title])
        file_name = _first_image(index[0].wikitext) if index else None
        return {
            "index_title": index_title,
            "page_title": page_title,
            "file": file_name,
            "file_url": f"https://{self.host}/wiki/File:{file_name}" if file_name else None,
            "page_url": f"https://{self.host}/wiki/{page_title.replace(' ', '_')}",
            "wikitext": body[0].wikitext if body else "",
        }


def net_hash(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def _index_of(title: str) -> str | None:
    if title.startswith("Page:"):
        rest = title[len("Page:") :]
        return "Index:" + rest.rsplit("/", 1)[0] if "/" in rest else None
    return None


def _file_of(wikitext: str) -> str | None:
    return _first_image(wikitext)


def _first_image(wikitext: str) -> str | None:
    import re

    m = re.search(r"\[\[\s*(?:File|Image|ファイル)\s*:\s*([^\]\|]+)", wikitext, re.IGNORECASE)
    return m.group(1).strip() if m else None


# --------------------------------------------------------------------- import
def document_of(host: str = DEFAULT_HOST):
    """The one document every Wikisource page belongs to.

    Rights are recorded, never inferred. The transcription is CC BY-SA 4.0 under the
    site's own terms. The underlying scan is **not** assumed to be public domain just
    because Wikisource hosts it: it is left ``unknown``, which the API refuses to
    proxy, so an unresolved scan can never be re-served as if it were free.
    """
    from ..schema import Document

    return Document(
        id="ws:ja",
        title="Wikisource (ja) — 翻刻テキスト",
        holder="Wikimedia Foundation",
        shelfmark=host,
        source_refs={"wikisource": f"https://{host}", "licence": SITE_LICENCE_URL},
        image_rights={
            "licence": "unknown",
            "holder": None,
            "attribution": "per-file; not resolved",
            "evidence": "Wikisource hosting does not imply PD",
            "checked": None,
        },
        text_rights={
            "licence": "CC-BY-SA-4.0",
            "holder": "Wikisource contributors",
            "attribution": "Wikisource contributors, CC BY-SA 4.0",
            "evidence": "site-wide Wikisource terms",
            "checked": None,
        },
    )


def page_of(page: WikisourcePage):
    """A ``Page`` row for one Wikisource page, with its revision and URL."""
    from ..schema import Page

    return Page(
        id=f"ws:{page.pageid}",
        document_id="ws:ja",
        seq=0,
        canvas=page.url,
        image="",
        width=0,
        height=0,
        transcription={"source": "wikisource", "entry id": page.title, "revision": str(page.revision or "")},
        meta={
            "title": page.title,
            "namespace": page.namespace,
            "index_title": page.index_title,
            "file_page": page.file_page,
            "timestamp": page.timestamp,
            "host": page.host,
            "geometry": "none",
        },
    )


def page_text_of(page: WikisourcePage):
    """A ``PageText`` row: the wikitext, with its revision for provenance."""
    from ..schema import PageText

    return PageText(
        page_id=f"ws:{page.pageid}",
        source="wikisource",
        revision=str(page.revision or ""),
        text_raw=page.wikitext,
    )


def select_text_pages(pages: Iterable[WikisourcePage]) -> tuple[list[WikisourcePage], list[WikisourcePage]]:
    """Split fetched pages into transcribed text and convention/navigation."""
    keep, drop = [], []
    for page in pages:
        ns = page.namespace if page.namespace is not None else namespace_of(page.title)
        (keep if ns in TEXT_NAMESPACES and ns not in EXCLUDED_NAMESPACES else drop).append(page)
    return keep, drop


def import_corpus(
    out: str | Path,
    *,
    char: str = "\U0002a708",
    limit: int = 20,
    host: str = DEFAULT_HOST,
    cache: str | Path | None = None,
    client: Wikisource | None = None,
    command: str = "atlas corpus wikisource",
) -> dict[str, int]:
    """Discover pages, keep the transcribed ones, and write a corpus directory.

    Writes the standard ``documents``/``pages``/``page_texts`` layout so the ordinary
    index and API serve it — a corpus, not a side file the UI cannot see.
    """
    import json as _json
    import time as _time

    from .. import tables

    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    client = client or Wikisource(host, cache)
    pages = client.pages(client.search(char, limit=limit))
    keep, drop = select_text_pages(pages)

    documents = [document_of(host)]
    kept_pages = []
    for index, page in enumerate(keep, 1):
        row = page_of(page)
        row.seq = index
        kept_pages.append(row)
    texts = [page_text_of(page) for page in keep]

    tables.write(out / "documents.parquet", documents, type(documents[0]), command=command)
    tables.write(out / "pages.parquet", kept_pages, type(kept_pages[0]))
    tables.write(out / "page_texts.parquet", texts, type(texts[0]))
    counts = {
        "documents": len(documents),
        "pages": len(kept_pages),
        "page_texts": len(texts),
        "excluded_pages": len(drop),
        "fetched_pages": len(pages),
    }
    (out / "MANIFEST.json").write_text(
        _json.dumps(
            {
                "schema_version": tables.SCHEMA_VERSION,
                "tables": counts,
                "files": {},
                "writer": "kuzushiji-atlas corpus",
                "command": command,
                "written_at": _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime()),
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    return counts


def occurrences_in_pages(pages: Iterable[WikisourcePage], char: str) -> list[dict[str, Any]]:
    """Occurrences of `char` in the *transcribed* pages among `pages`.

    The namespace policy is applied here and only here, so a convention note in the
    ``Index:`` namespace can never be counted as a place a manuscript writes the
    character — however the pages were obtained.
    """
    keep, _ = select_text_pages(pages)
    found: list[dict[str, Any]] = []
    for page in keep:
        if char not in page.wikitext:
            continue
        for line_no, line in enumerate(page.wikitext.splitlines(), 1):
            for start, end, char_class, context in find_occurrences(line, char):
                found.append(
                    {
                        "char": char,
                        "codepoint": f"U+{ord(char):04X}",
                        "char_class": char_class,
                        "tier": "page_text",
                        "host": page.host,
                        "title": page.title,
                        "url": page.url,
                        "pageid": page.pageid,
                        "revision": page.revision,
                        "timestamp": page.timestamp,
                        "namespace": page.namespace,
                        "index_title": page.index_title,
                        "file_page": page.file_page,
                        "line_number": line_no,
                        "span_start": start,
                        "span_end": end,
                        "line": line,
                        "context": context,
                        "licence": page.licence,
                        "licence_url": page.licence_url,
                        "licence_evidence": page.licence_evidence,
                        "geometry": "none",
                        "note": "Wikisource transcription occurrence; no image rectangle. Not a crop.",
                    }
                )
    return found


def search(
    char: str, *, limit: int = 20, host: str = DEFAULT_HOST, cache: str | Path | None = None
) -> list[dict[str, Any]]:
    """Titles containing `char`, as records ready for the index."""
    client = Wikisource(host, cache)
    titles = client.search(char, limit=limit)
    return [{"title": t, "host": host, "url": f"https://{host}/wiki/{t.replace(' ', '_')}"} for t in titles]


def fetch_pages(
    titles: Iterable[str],
    *,
    char: str | None = None,
    host: str = DEFAULT_HOST,
    cache: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Fetch pages and, when `char` is given, their occurrences of it."""
    client = Wikisource(host, cache)
    titles = list(titles)
    if char:
        return client.occurrences(titles, char)
    return [p.as_dict() for p in client.pages(titles)]
