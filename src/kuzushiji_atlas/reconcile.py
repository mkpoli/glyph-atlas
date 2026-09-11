"""Rights reconciliation across sources and the attribution file.

An importer resolves the rights of a document from the fields of one upstream. `resolve_directory`
collects what the other sources say about the same document — the licence string the upstream
dataset recorded, the rights fields of the IIIF manifest behind the pages, the holder table and, for
a holder that states the licence of each item on the item's own page, that per-item statement read
from the manifest — keeps every statement in `meta.rights_evidence` as a `{source, licence, url,
fetched}` row, and sets `image_rights` from the first row of the precedence order that names a
licence of `data/vocab/licences.yaml`:

    per-item, manifest, upstream, holder

A statement that matches no entry stays in the list with licence `unknown` and is passed over while
another row names a licence, in the same way that `rights.resolve` prefers a statement it knows to a
raw string it does not; a document whose rows all read `unknown` takes the first row of the order,
and a document no source states anything about keeps the rights its importer wrote. Rows that name
different licences are kept and counted, so a document never loses a statement it carries.

Manifests are fetched through `net.download` into `cache/manifests/<sha256 of url>.json`, the
directory みんなで翻刻データ uses, so a manifest is downloaded once for the corpus. `limit` caps how
many manifests one run fetches: a cached manifest is read without spending the budget, so bounded
runs walk the corpus and accumulate evidence. `recheck` refetches the terms page of every holder
present, at most `recheck_limit` of them and at most `limit` of them when `recheck_limit` is not
given, and reports the statements a page no longer prints.

`report` counts documents, pages, lines and units by licence and by eligibility for the release
licence and lists the documents whose rows disagree; `attribution` writes one entry per source and
per holder present, with the credit line the holder asks for, the licence, the licence URL and the
obligations of the vocabulary, and says which material the dataset republishes unchanged and which
it modifies.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from . import net, registry, rights, tables
from .schema import Document, Licence, Rights, Source

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "cache"
CACHE_ENV = "KUZUSHIJI_ATLAS_CACHE"
MANIFESTS = "manifests"
SOURCES = registry.SOURCES
MANIFEST_NAME = tables.MANIFEST_NAME

#: The `meta` key that holds the evidence rows of a document.
EVIDENCE = "rights_evidence"
#: The `source_refs` key that holds the URL of the document's IIIF manifest.
MANIFEST_REF = "iiif-manifest"
#: The licence a release is published under; material must be eligible for it.
TARGET = Licence.CC_BY_SA_4
COMMAND = "atlas rights resolve"
#: Labels for material whose rights the record does not state.
NO_RIGHTS = "(none)"
NO_DOCUMENT = "(no document)"
NO_STATEMENT = "No licence statement on the record; ask the holder before reuse."

#: The evidence sources, most authoritative first.
PER_ITEM = "per-item"
MANIFEST = "manifest"
UPSTREAM = "upstream"
HOLDER = "holder"
PRECEDENCE = (PER_ITEM, MANIFEST, UPSTREAM, HOLDER)

#: `meta` fields an importer may record the upstream licence string and its URL in.
LICENCE_FIELDS = ("image_license", "image_licence", "licence", "license", "rights_statement", "rights")
URL_FIELDS = (
    "image_license_url",
    "image_licence_url",
    "licence_url",
    "license_url",
    "rights_url",
    "terms_url",
    "terms",
)

#: The dataset tables the report counts.
TABLES = ("documents", "pages", "lines", "units")

#: The counts `resolve_directory` returns, all present so that a caller sees one shape.
COUNTS = (
    "documents",
    "resolved",
    "missing",
    "changed",
    "agreements",
    "disagreements",
    "unstated",
    "evidence",
    "holders",
)
#: The counts `recheck` returns.
RECHECK_COUNTS = ("rechecked", "terms_changed", "terms_failed", "recheck_skipped")

#: Attempts and first backoff of a terms-page request, as `net.download` retries.
TERMS_ATTEMPTS = 5
TERMS_BACKOFF = 1.0

_HREF = re.compile(r"""href\s*=\s*["\']([^"\']+)["\']""", re.IGNORECASE)

# Monotonic clock and sleeper of the terms-page requests, so that a test can assert the host pause.
CLOCK = time.monotonic
SLEEP = time.sleep
#: The time of the last request per host, so that requests to one host stay `host_pause` apart.
_LAST_REQUEST: dict[str, float] = {}


def _text_of(value: Any) -> str | None:
    """A field of a table row as a non-empty string, or None."""
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _first_text(fields: dict[str, Any], names: Sequence[str]) -> str | None:
    for name in names:
        found = _text_of(fields.get(name))
        if found is not None:
            return found
    return None


def _cell(value: Any) -> str:
    """A value as a Markdown table cell."""
    return " ".join(str(value).split()).replace("|", "\\|")


def _line(value: Any) -> str:
    """A value as one line of Markdown: the whitespace collapsed."""
    return " ".join(str(value).split())


def _number(value: int) -> str:
    return str(value)


def cache_root(cache: Path | None = None) -> Path:
    """The cache directory: `$KUZUSHIJI_ATLAS_CACHE`, or `cache/` of the repository."""
    if cache is not None:
        return Path(cache)
    override = os.environ.get(CACHE_ENV)
    return Path(override) if override else CACHE


def manifests_dir(cache: Path | None = None) -> Path:
    """`cache/manifests`, the directory the IIIF manifests are cached in."""
    return cache_root(cache) / MANIFESTS


def manifest_path(url: str, cache: Path | None = None) -> Path:
    """Where the manifest of `url` is cached: `<cache>/manifests/<sha256 of url>.json`."""
    return manifests_dir(cache) / f"{hashlib.sha256(url.encode('utf-8')).hexdigest()}.json"


def read_manifest(path: Path) -> dict | None:
    """A cached manifest, or None when the file does not hold a JSON object.

    ADEAC serves a manifest with a byte order mark, so the text is read as `utf-8-sig`.
    """
    try:
        manifest = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    return manifest if isinstance(manifest, dict) else None


@dataclass(frozen=True)
class Evidence:
    """One statement about a document: the source that made it, the licence and where it was read."""

    source: str
    rights: Rights
    url: str | None = None

    def row(self, fetched: str) -> dict[str, Any]:
        """The evidence as it is stored in `meta.rights_evidence`."""
        return {
            "source": self.source,
            "licence": self.rights.licence.value,
            "url": self.url,
            "fetched": fetched,
        }


class Manifests:
    """The IIIF manifests of one run, read from the cache and fetched through `net.download`.

    `limit` caps how many manifests the run fetches; a manifest already in the cache is read without
    spending the budget. `refresh` fetches a cached manifest again. A fetch that fails, and a cached
    file that does not parse, counts as an error and leaves the document without manifest evidence.
    """

    def __init__(
        self,
        *,
        cache: Path | None = None,
        client: httpx.Client | None = None,
        limit: int | None = None,
        pause: float | None = None,
        refresh: bool = False,
    ) -> None:
        self.cache = cache
        self.client = client
        self.limit = limit
        self.pause = pause
        self.refresh = refresh
        self.read = 0
        self.fetched = 0
        self.skipped = 0
        self.errors = 0
        self._loaded: dict[str, dict | None] = {}

    def get(self, url: str | None) -> dict | None:
        """The parsed manifest of `url`, fetched when it is not cached, or None."""
        if not url:
            return None
        if url in self._loaded:
            return self._loaded[url]
        path = manifest_path(url, self.cache)
        found: dict | None = None
        if path.is_file() and not self.refresh:
            self.read += 1
            found = read_manifest(path)
            if found is None:
                self.errors += 1
        elif self.limit is not None and self.fetched >= self.limit:
            self.skipped += 1
        else:
            try:
                net.download(url, path, expected="json", client=self.client, pause=self.pause)
            except net.DownloadError:
                self.errors += 1
            else:
                self.fetched += 1
                found = read_manifest(path)
                if found is None:
                    self.errors += 1
        self._loaded[url] = found
        return found

    def counts(self) -> dict[str, int]:
        return {
            "manifests_read": self.read,
            "manifests_fetched": self.fetched,
            "manifests_skipped": self.skipped,
            "manifest_errors": self.errors,
        }


def vocabulary_index() -> dict[str, dict]:
    """One vocabulary entry per licence value, the first entry that states it."""
    index: dict[str, dict] = {}
    for entry in rights.vocabulary():
        index.setdefault(entry["licence"], entry)
    return index


def entry_for_url(url: str | None) -> dict | None:
    """The vocabulary entry a URL is a spelling of, or None."""
    if not _text_of(url):
        return None
    wanted = rights.key(url)
    for entry in rights.vocabulary():
        if any(rights.key(match) == wanted for match in entry["match"]):
            return entry
    return None


def entry_for_rights(resolved: Rights | None) -> dict | None:
    """The vocabulary entry of a rights record: the statement it read, else its licence."""
    if resolved is None:
        return None
    if resolved.evidence:
        found = entry_for_url(resolved.evidence)
        if found is not None:
            return found
    return vocabulary_index().get(resolved.licence.value)


def per_item_terms(holder: str | None) -> str | None:
    """The terms page of a holder whose licence is stated per item, or None.

    A holder row states `per-item` itself, or its terms page is the one entry of the vocabulary
    whose `eligible` is `per-item`; both name 国書データベース.
    """
    row = rights.holder_entry(holder)
    if row is None:
        return None
    for field in rights.HOLDER_LICENCE_FIELDS:
        if str(row.get(field) or "").strip() == rights.PER_ITEM:
            return _text_of(row.get("terms")) or _text_of(row.get("url"))
    terms = _text_of(row.get("terms")) or _text_of(row.get("url"))
    entry = entry_for_url(terms)
    if entry is not None and entry["eligible"] == rights.PER_ITEM:
        return terms
    return None


def upstream_fields(document: Document) -> tuple[str | None, str | None]:
    """The upstream licence string and licence URL of a document.

    The fields the importer recorded in `meta` come first; a document that carries none falls back to
    the statement already resolved onto `image_rights`, which is what the importer read upstream.
    """
    licence = _first_text(document.meta, LICENCE_FIELDS)
    url = _first_text(document.meta, URL_FIELDS)
    if licence is None and url is None:
        recorded = document.image_rights
        if recorded is not None and (recorded.licence is not Licence.UNKNOWN or recorded.evidence):
            return recorded.licence.value, recorded.evidence
    return licence, url


def evidence_of(document: Document, manifests: Manifests) -> list[Evidence]:
    """Every statement about one document, in the precedence order.

    A source that states nothing contributes no row: a manifest without a rights field, a holder the
    holder table does not list. A statement whose licence the vocabulary does not know is a row with
    licence `unknown`, since the page it points at is what a reviewer has to read. The holder row of
    a per-item holder is the page that states the licence of each item, so once the item's own
    statement is read the row is that statement at a lower precedence and is not kept beside it.
    """
    found: list[Evidence] = []
    per_item = per_item_terms(document.holder)
    manifest_url = _text_of(document.source_refs.get(MANIFEST_REF))
    manifest = manifests.get(manifest_url)
    stated = rights.manifest_rights(manifest) if manifest is not None else None
    item_statement = stated is not None and (stated.licence is not Licence.UNKNOWN or stated.evidence)
    if item_statement:
        found.append(Evidence(source=PER_ITEM if per_item else MANIFEST, rights=stated, url=manifest_url))
    licence, url = upstream_fields(document)
    if licence or url:
        resolved = rights.resolve(licence=licence, url=url, holder=document.holder)
        found.append(Evidence(source=UPSTREAM, rights=resolved, url=url or resolved.evidence))
    holder_row = rights.holder_entry(document.holder)
    if holder_row is not None and not (per_item and item_statement):
        resolved = rights.resolve(holder=document.holder)
        url = resolved.evidence or _text_of(holder_row.get("terms"))
        found.append(Evidence(source=HOLDER, rights=resolved, url=url))
    return found


def chosen_row(rows: Iterable[Evidence]) -> Evidence | None:
    """The statement the precedence order picks: the first that names a licence, else the first."""
    rows = list(rows)
    if not rows:
        return None
    for row in rows:
        if row.rights.licence is not Licence.UNKNOWN:
            return row
    return rows[0]


def rights_of(row: Evidence, document: Document) -> Rights:
    """The `image_rights` a chosen statement sets, keeping the holder of the document."""
    resolved = row.rights
    holder = resolved.holder or document.holder
    if holder == resolved.holder:
        return resolved
    return resolved.model_copy(update={"holder": holder})


def holder_terms(documents: Iterable[Document]) -> dict[str, list[str]]:
    """The terms page of every holder present, with the holders that name it."""
    found: dict[str, list[str]] = {}
    for name in sorted({document.holder for document in documents if document.holder}):
        row = rights.holder_entry(name)
        if row is None:
            continue
        url = _text_of(row.get("terms")) or _text_of(row.get("url"))
        if url:
            found.setdefault(url, []).append(name)
    return found


def _wait_for_turn(url: str, pause: float | None) -> None:
    """Hold the per-host interval between two terms-page requests."""
    host = (urlsplit(url).hostname or "").lower()
    interval = net.host_pause(url) if pause is None else float(pause)
    now = CLOCK()
    last = _LAST_REQUEST.get(host)
    if last is not None:
        remaining = interval - (now - last)
        if remaining > 0:
            SLEEP(remaining)
            now = CLOCK()
    _LAST_REQUEST[host] = now


def fetch_terms(
    url: str,
    *,
    client: httpx.Client | None = None,
    pause: float | None = None,
    attempts: int = TERMS_ATTEMPTS,
) -> str:
    """Fetch a terms page as text, politely and with the retries the other downloads use.

    `net.download` refuses an HTML body for every content kind, so a terms page is read with the
    project User-Agent directly. The per-host interval of `net.host_pause` holds between attempts, a
    transport error or a 429 and 5xx is retried with an exponential backoff up to `attempts`, and
    `net.DownloadError` is raised when the page cannot be read.
    """
    own = client is None
    if own:
        client = httpx.Client(timeout=30.0, follow_redirects=True)
    failure = "no attempt was made"
    try:
        for attempt in range(1, max(1, attempts) + 1):
            _wait_for_turn(url, pause)
            try:
                response = client.get(url, headers={"User-Agent": net.USER_AGENT, "Accept": "text/html,*/*"})
            except httpx.HTTPError as error:
                failure = f"{error.__class__.__name__}: {error}"
            else:
                if response.status_code in net.RETRY_STATUS:
                    failure = f"HTTP {response.status_code} {response.reason_phrase}"
                elif response.status_code >= 400:
                    raise net.DownloadError(f"{url}: HTTP {response.status_code} {response.reason_phrase}")
                else:
                    return response.text
            if attempt < attempts:
                SLEEP(min(TERMS_BACKOFF * 2 ** (attempt - 1), net.MAX_BACKOFF))
    finally:
        if own:
            client.close()
    raise net.DownloadError(f"{url}: gave up after {max(1, attempts)} attempts ({failure})")


def terms_diff(text: str, entry: dict | None, base: str | None = None) -> dict[str, Any]:
    """How a fetched terms page stands against the statement the vocabulary recorded.

    `still` holds the match strings of the entry the page prints or links, `missing` the rest of
    them, and `found` the licences of every vocabulary entry the page names. `changed` is true when
    the page prints none of the recorded spellings, or when it names licences and none of them is
    the recorded one, which is the case a recheck has to report.
    """
    if entry is None:
        return {"still": [], "missing": [], "found": [], "names": False, "changed": False}
    still = [match for match in entry["match"] if _mentions(text, match, base)]
    found = sorted(
        {
            candidate["licence"]
            for candidate in rights.vocabulary()
            if any(_mentions(text, match, base) for match in candidate["match"])
        }
    )
    names = entry["licence"] in found
    return {
        "still": still,
        "missing": [match for match in entry["match"] if match not in still],
        "found": found,
        "names": names,
        "changed": not still or (bool(found) and not names),
    }


def _mentions(text: str, match: str, base: str | None = None) -> bool:
    """Whether a page prints or links a spelling of a statement: a URL, an id or a label.

    A URL counts when the page prints it, when one of its links resolves to it — a relative link is
    resolved against `base`, the address the page was fetched from — or when it prints the host and
    the path.
    """
    if "://" in match:
        if match in text:
            return True
        wanted = rights.key(match)
        if wanted and wanted in _links(text, base):
            return True
        parts = urlsplit(match)
        path = parts.path.strip("/")
        return bool(path) and parts.hostname is not None and parts.hostname in text and path in text
    return bool(match.strip()) and match.casefold() in text.casefold()


def _links(text: str, base: str | None) -> set[str]:
    """The URLs a page links to, as `rights.key` compares them."""
    found = set()
    for href in _HREF.findall(text):
        absolute = urljoin(base, href) if base else href
        key = rights.key(absolute)
        if key:
            found.add(key)
    return found


def recheck(
    terms: dict[str, list[str]],
    *,
    client: httpx.Client | None = None,
    pause: float | None = None,
    limit: int | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Fetch the terms page of every holder present and diff it against the vocabulary.

    The returned rows carry the URL, the holders that name it, the recorded entry and the diff; the
    counts hold `rechecked`, `terms_changed`, `terms_failed` and `recheck_skipped`.
    """
    results: list[dict[str, Any]] = []
    counts: Counter[str] = Counter(dict.fromkeys(RECHECK_COUNTS, 0))
    for index, url in enumerate(sorted(terms)):
        if limit is not None and index >= limit:
            counts["recheck_skipped"] += 1
            continue
        entry = entry_for_url(url)
        result: dict[str, Any] = {"url": url, "holders": terms[url], "entry": entry, "error": None}
        try:
            text = fetch_terms(url, client=client, pause=pause)
        except net.DownloadError as error:
            counts["terms_failed"] += 1
            result["error"] = str(error)
            result.update({"still": [], "missing": [], "found": [], "changed": False})
        else:
            counts["rechecked"] += 1
            result.update(terms_diff(text, entry, url))
            if result["changed"]:
                counts["terms_changed"] += 1
        results.append(result)
    return results, dict(counts)


def _recheck_markdown(results: list[dict[str, Any]], counts: dict[str, int], fetched: str) -> list[str]:
    """The recheck of one run as Markdown lines."""
    lines = [
        "## Terms pages",
        "",
        (
            f"{fetched}: {counts.get('rechecked', 0)} terms pages read, "
            f"{counts.get('terms_changed', 0)} that no longer print the recorded statement, "
            f"{counts.get('terms_failed', 0)} that could not be read."
        ),
        "",
    ]
    for result in results:
        holders = "、".join(result["holders"])
        lines.append(f"### {result['url']}")
        lines.append("")
        lines.append(f"- Holders: {holders or '(none)'}")
        if result["error"]:
            lines.append(f"- Not read: {result['error']}")
            lines.append("")
            continue
        entry = result["entry"]
        if entry is None:
            lines.append("- The vocabulary holds no entry for this page; nothing to compare.")
        else:
            lines.append(f"- Recorded: {entry['licence']} — {entry['label']} (checked {entry['checked']})")
            lines.append(f"- Still printed: {', '.join(result['still']) or '(nothing)'}")
            lines.append(f"- No longer printed: {', '.join(result['missing']) or '(nothing)'}")
            lines.append(f"- Statements the page now names: {', '.join(result['found']) or '(none)'}")
            lines.append(f"- The page names the recorded licence: {'yes' if result['names'] else 'no'}")
            lines.append(f"- Changed: {'yes' if result['changed'] else 'no'}")
        lines.append("")
    return lines


def _sync_manifest(directory: Path, command: str, documents: int) -> None:
    """Record the rewritten documents table in an existing `MANIFEST.json` of the directory."""
    path = directory / MANIFEST_NAME
    if not path.is_file():
        return
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return
    if not isinstance(manifest, dict):
        return
    file = directory / "documents.parquet"
    manifest["command"] = command
    manifest["written_at"] = datetime.now(tz=UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    if isinstance(manifest.get("tables"), dict):
        manifest["tables"]["documents"] = documents
    if isinstance(manifest.get("files"), dict) and file.is_file():
        manifest["files"]["documents.parquet"] = _sha256(file)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_directory(
    directory: Path,
    *,
    recheck: bool = False,
    limit: int | None = None,
    recheck_limit: int | None = None,
    cache: Path | None = None,
    client: httpx.Client | None = None,
    pause: float | None = None,
    refresh: bool = False,
    out: Path | None = None,
    command: str = COMMAND,
) -> dict[str, int]:
    """Reconcile the rights of every document in `directory` and write the table back.

    Each document gathers its evidence, the precedence order sets `image_rights`, all rows are kept
    in `meta.rights_evidence`, and the documents table is written with `tables.write_table`; a
    `MANIFEST.json` in the directory is updated with the new file hash. A document no source states
    anything about keeps the rights its importer wrote and is counted in `missing`.

    `limit` caps the manifests fetched from the network in this run, `pause` the interval between
    requests to one host (3 s by default, `net.host_pause`). `recheck` refetches the terms page of
    every holder present, at most `recheck_limit` of them and at most `limit` of them when
    `recheck_limit` is not given, and `out` writes the run and the recheck as Markdown. The returned
    mapping holds the counts of the run.
    """
    directory = Path(directory)
    documents = tables.Dataset(directory).read("documents")
    fetched = datetime.now(tz=UTC).date().isoformat()
    manifests = Manifests(cache=cache, client=client, limit=limit, pause=pause, refresh=refresh)
    counts: Counter[str] = Counter(dict.fromkeys(COUNTS, 0))
    for document in documents:
        counts["documents"] += 1
        previous = document.image_rights
        rows = evidence_of(document, manifests)
        counts["evidence"] += len(rows)
        if not rows:
            counts["missing"] += 1
            document.meta.pop(EVIDENCE, None)
            continue
        counts["resolved"] += 1
        pick = chosen_row(rows)
        resolved = rights_of(pick, document)
        if previous is None or previous.licence is not resolved.licence:
            counts["changed"] += 1
        if len({row.rights.licence for row in rows}) > 1:
            counts["disagreements"] += 1
        else:
            counts["agreements"] += 1
        if resolved.licence is Licence.UNKNOWN:
            counts["unstated"] += 1
        document.image_rights = resolved
        document.meta[EVIDENCE] = [row.row(fetched) for row in rows]
    counts["holders"] = len({document.holder for document in documents if document.holder})
    counts.update(manifests.counts())
    counts.update(dict.fromkeys(RECHECK_COUNTS, 0))
    results: list[dict[str, Any]] = []
    if recheck:
        results, rechecked_counts = recheck_holders(
            documents,
            client=client,
            pause=pause,
            limit=recheck_limit if recheck_limit is not None else limit,
        )
        counts.update(rechecked_counts)
    tables.write_table(directory / "documents.parquet", documents, Document, command=command)
    _sync_manifest(directory, command, len(documents))
    if out is not None:
        write_summary(Path(out), directory, counts, results, fetched, command)
    return {name: int(value) for name, value in counts.items()}


def recheck_holders(
    documents: Iterable[Document],
    *,
    client: httpx.Client | None = None,
    pause: float | None = None,
    limit: int | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """`recheck` over the holders of a document list."""
    return recheck(holder_terms(documents), client=client, pause=pause, limit=limit)


def write_summary(
    path: Path,
    directory: Path,
    counts: dict[str, int],
    results: list[dict[str, Any]],
    fetched: str,
    command: str,
) -> Path:
    """Write the run and its recheck as Markdown and return the path."""
    lines = [
        "# Rights reconciliation",
        "",
        f"`{command} {directory}` on {fetched}.",
        "",
        f"- Documents: {counts.get('documents', 0)}",
        f"- With evidence: {counts.get('resolved', 0)}",
        f"- No statement from any source: {counts.get('missing', 0)}",
        f"- Licence changed by reconciliation: {counts.get('changed', 0)}",
        f"- Statements that disagree: {counts.get('disagreements', 0)}",
        f"- Statements that match no vocabulary entry: {counts.get('unstated', 0)}",
        f"- Evidence rows stored: {counts.get('evidence', 0)}",
        f"- Manifests read from the cache: {counts.get('manifests_read', 0)}",
        f"- Manifests fetched: {counts.get('manifests_fetched', 0)}",
        f"- Manifests not fetched, over the limit: {counts.get('manifests_skipped', 0)}",
        f"- Manifests that could not be read: {counts.get('manifest_errors', 0)}",
        "",
    ]
    if results or counts.get("rechecked") or counts.get("terms_changed") or counts.get("terms_failed"):
        lines += _recheck_markdown(results, counts, fetched)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8")
    return path


def _label(resolved: Rights | None) -> str:
    return resolved.licence.value if resolved is not None else NO_RIGHTS


def _table_counts(dataset: tables.Dataset, documents: list[Document]) -> dict[str, Counter]:
    """Documents, pages, lines and units counted by the licence of the document they belong to."""
    label = {document.id: _label(document.image_rights) for document in documents}
    counts: dict[str, Counter] = {name: Counter() for name in TABLES}
    for document in documents:
        counts["documents"][label[document.id]] += 1
    page_document: dict[str, str] = {}
    if dataset.tables["pages"] is not None:
        for page in dataset.read("pages"):
            page_document[page.id] = page.document_id
            counts["pages"][label.get(page.document_id, NO_DOCUMENT)] += 1
    if dataset.tables["lines"] is not None:
        for batch in dataset.scan("lines"):
            for line in batch:
                document_id = page_document.get(line.page_id)
                counts["lines"][label.get(document_id, NO_DOCUMENT)] += 1
    if dataset.tables["units"] is not None:
        for batch in dataset.scan("units"):
            for unit in batch:
                document_id = unit.document_id or page_document.get(unit.page_id)
                counts["units"][label.get(document_id, NO_DOCUMENT)] += 1
    return counts


def report(directory: Path, *, limit: int | None = None, target: Licence | str = TARGET) -> str:
    """The rights of a dataset directory as Markdown.

    Documents, pages, lines and units are counted by the licence of the document they belong to and
    by eligibility for `target`; the holders whose material the release cannot carry are named, and
    every evidence row that disagrees with the licence chosen for its document is listed. `limit`
    caps the rows listed, not the counts.
    """
    directory = Path(directory)
    dataset = tables.Dataset(directory)
    documents = dataset.read("documents")
    counts = _table_counts(dataset, documents)
    target = Licence(target)
    index = vocabulary_index()
    eligible = {
        licence: rights.eligible(Rights(licence=Licence(licence), attribution=licence), target)
        for licence in index
    }
    totals = {name: sum(counter.values()) for name, counter in counts.items()}
    order = sorted(
        set().union(*(counter.keys() for counter in counts.values())),
        key=lambda licence: (-counts["lines"][licence], -counts["documents"][licence], licence),
    )
    lines = [
        "# Rights report",
        "",
        (
            f"`{directory}`: {_number(totals['documents'])} documents, {_number(totals['pages'])} pages, "
            f"{_number(totals['lines'])} lines, {_number(totals['units'])} units."
        ),
        "",
        (
            f"Release licence: {target.value}. A statement is eligible when `data/vocab/licences.yaml` "
            f"marks it eligible for that licence."
        ),
        "",
        "## By licence",
        "",
        "| Licence | Documents | Pages | Lines | Units | Eligible |",
        "| --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for licence in order:
        lines.append(
            f"| {_cell(licence)} | {_number(counts['documents'][licence])} | {_number(counts['pages'][licence])} "
            f"| {_number(counts['lines'][licence])} | {_number(counts['units'][licence])} "
            f"| {'yes' if eligible.get(licence, False) else 'no'} |"
        )
    lines.append(
        f"| **Total** | {_number(totals['documents'])} | {_number(totals['pages'])} "
        f"| {_number(totals['lines'])} | {_number(totals['units'])} | |"
    )
    lines += [
        "",
        "## By eligibility",
        "",
        f"| Eligible for {target.value} | Licences | Documents | Pages | Lines | Units |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for wanted in (True, False):
        chosen = [licence for licence in order if eligible.get(licence, False) is wanted]
        lines.append(
            f"| {'yes' if wanted else 'no'} | {len(chosen)} "
            f"| {_number(sum(counts['documents'][licence] for licence in chosen))} "
            f"| {_number(sum(counts['pages'][licence] for licence in chosen))} "
            f"| {_number(sum(counts['lines'][licence] for licence in chosen))} "
            f"| {_number(sum(counts['units'][licence] for licence in chosen))} |"
        )
    lines += _follow_up(documents, eligible, target)
    lines += _disagreements(documents, limit)
    return "\n".join(lines).rstrip("\n") + "\n"


def _follow_up(documents: list[Document], eligible: dict[str, bool], target: Licence) -> list[str]:
    """The holders whose material the release licence cannot carry."""
    holders: Counter[tuple[str, str]] = Counter()
    for document in documents:
        licence = _label(document.image_rights)
        if eligible.get(licence, False):
            continue
        holders[(document.holder or "(no holder)", licence)] += 1
    if not holders:
        return []
    lines = [
        "",
        "## Holders to follow up",
        "",
        (
            f"{len({holder for holder, _ in holders})} holders carry material that is not eligible for "
            f"{target.value}. RS-NOC-CR binds the images by contract, and NC, ND, restricted and unstated "
            f"material needs the holder's answer before any reuse."
        ),
        "",
        "| Holder | Licence | Documents |",
        "| --- | --- | ---: |",
    ]
    for (holder, licence), count in sorted(holders.items(), key=lambda item: (-item[1], item[0])):
        lines.append(f"| {_cell(holder)} | {_cell(licence)} | {_number(count)} |")
    return lines


def _disagreements(documents: list[Document], limit: int | None) -> list[str]:
    """Every evidence row that disagrees with the licence chosen for its document."""
    rows: list[tuple[str, str, str, str, str, str, str]] = []
    documents_with = 0
    for document in documents:
        chosen = _label(document.image_rights)
        evidence = document.meta.get(EVIDENCE)
        if not isinstance(evidence, list):
            continue
        differing = [row for row in evidence if isinstance(row, dict) and row.get("licence") != chosen]
        if not differing:
            continue
        documents_with += 1
        for row in differing:
            rows.append(
                (
                    document.id,
                    document.title,
                    document.holder or "(no holder)",
                    str(row.get("source") or ""),
                    str(row.get("licence") or ""),
                    str(row.get("url") or ""),
                    chosen,
                )
            )
    if not rows:
        return [
            "",
            "## Disagreements",
            "",
            "No document carries a statement that disagrees with the licence chosen for it.",
        ]
    shown = rows if limit is None else rows[:limit]
    lines = [
        "",
        "## Disagreements",
        "",
        (
            f"{documents_with} documents carry {len(rows)} statements that disagree with the licence "
            f"chosen for them; {len(shown)} rows are listed. The rows are in `meta.rights_evidence`; the "
            f"row the precedence order chose set `image_rights`."
        ),
        "",
        "| Document | Title | Holder | Source | Statement | Evidence | Chosen |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for ident, title, holder, source, licence, url, chosen in shown:
        lines.append(
            f"| {_cell(ident)} | {_cell(title)} | {_cell(holder)} | {_cell(source)} | {_cell(licence)} "
            f"| {_cell(url)} | {_cell(chosen)} |"
        )
    if len(shown) < len(rows):
        lines += [
            "",
            f"{len(rows) - len(shown)} further rows are not listed; run the report again with `limit`.",
        ]
    return lines


def _imported_source(directory: Path) -> str | None:
    """The source a directory was imported from, read from the command in its `MANIFEST.json`."""
    path = directory / MANIFEST_NAME
    if not path.is_file():
        return None
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None
    command = manifest.get("command") if isinstance(manifest, dict) else None
    if not isinstance(command, str):
        return None
    words = [word for word in command.split() if not word.startswith("-")]
    if len(words) >= 3 and words[0] == "atlas" and words[1] == "import":
        return words[2]
    return None


def sources_present(
    documents: Iterable[Document], known: dict[str, Source], imported: str | None = None
) -> list[str]:
    """The registry sources a dataset directory holds: the importing source and its named upstreams."""
    found: set[str] = set()
    if imported in known:
        found.add(imported)
    for document in documents:
        found |= {key for key in document.source_refs if key in known}
        prefix = document.id.split(":", 1)[0]
        if prefix in known:
            found.add(prefix)
    return sorted(found)


def _source_counts(documents: list[Document], source_id: str) -> int:
    return sum(
        1
        for document in documents
        if source_id in document.source_refs or document.id.split(":", 1)[0] == source_id
    )


def _credit(resolved: Rights | None, holder: str) -> str:
    """The credit line a holder asks for, falling back to the holder name."""
    if resolved is None:
        return holder
    attribution = resolved.attribution.strip()
    if not attribution or "unresolved for review" in attribution:
        return holder
    return attribution


def attribution(directory: Path) -> str:
    """The sources and holders a dataset directory holds, as the release's `ATTRIBUTION.md`.

    One entry per source and per holder present, each with the credit line, the licence, the URL that
    states it and the obligations of `data/vocab/licences.yaml`, and with the material the dataset
    republishes unchanged told apart from the material it modifies.
    """
    directory = Path(directory)
    documents = tables.Dataset(directory).read("documents")
    known = {source.id: source for source in registry.load(SOURCES)}
    imported = _imported_source(directory)
    lines = [
        "# Attribution",
        "",
        (
            f"`{directory}` holds {len(documents)} documents. The annotations and the compilation of this "
            f"dataset are {TARGET.value}; every record keeps the rights of its image and of its text. "
            f"Generated by `atlas rights attribution {directory}`."
        ),
        "",
        (
            "The images stay at the holder's own address: a record stores the page URL and a rectangle, "
            "and a release crop cuts that rectangle out of the image, which is a modification. The text of "
            "a transcription is republished with the markup removed and the records rebuilt."
        ),
        "",
        "## Sources",
        "",
    ]
    for source_id in sources_present(documents, known, imported):
        source = known[source_id]
        entry = entry_for_rights(Rights(licence=source.licence, attribution=source.attribution, evidence=None))
        licence_url = entry["evidence"] if entry and entry["evidence"] else source.url
        lines += [
            f"### {_line(source.name)} ({source.id})",
            "",
            f"- Credit: {_line(source.attribution)}",
            f"- Publisher: {_line(source.publisher)}",
            f"- Licence: {source.licence.value} — {licence_url}",
            f"- URL: {source.url}",
            f"- Obligations: {entry['obligations'] if entry else '(no vocabulary entry)'}",
            "- Material unchanged: the text and the metadata as the source published them.",
            (
                "- Material modified: the records rebuilt with this dataset's ids, the text markup removed, "
                "and coordinates added where the source states them."
            ),
            f"- Documents: {_source_counts(documents, source_id)}",
            "",
        ]
    if not sources_present(documents, known, imported):
        lines += ["No source of `data/sources/` is named by these records.", ""]
    lines += ["## Holders", ""]
    holders: dict[str, list[Document]] = {}
    for document in documents:
        holders.setdefault(document.holder or "(no holder)", []).append(document)
    for holder in sorted(holders):
        group = holders[holder]
        licences: Counter[str] = Counter(_label(document.image_rights) for document in group)
        credits: Counter[str] = Counter(_credit(document.image_rights, holder) for document in group)
        urls: dict[str, Counter[str]] = {}
        for document in group:
            resolved = document.image_rights
            url = resolved.evidence if resolved is not None else None
            entry = entry_for_rights(resolved)
            if url is None and entry is not None:
                url = entry["evidence"]
            urls.setdefault(_label(resolved), Counter())[url or ""] += 1
        lines += [f"### {_line(holder)}", "", f"- Credit: {_line(credits.most_common(1)[0][0])}"]
        for licence, count in sorted(licences.items(), key=lambda item: (-item[1], item[0])):
            entry = vocabulary_index().get(licence)
            url = urls[licence].most_common(1)[0][0]
            obligations = entry["obligations"] if entry else NO_STATEMENT
            lines.append(f"- Licence: {licence} — {url or '(no URL)'} ({count} documents)")
            lines.append(f"  - Obligations: {obligations}")
        lines += [
            (
                "- Material unchanged: the page image as the holder serves it, which this dataset "
                "addresses by URL and rectangle."
            ),
            "- Material modified: the crop cut from that image, and the line boxes drawn on it.",
            "",
        ]
    return "\n".join(lines).rstrip("\n") + "\n"
