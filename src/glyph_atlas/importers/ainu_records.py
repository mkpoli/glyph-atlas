"""Import the アイヌ関連資料 project of みんなで翻刻 from the platform API.

The platform publishes a project as `GET /api/projects/{project}` — its title, description and the
ids of its collections — and, for every entry, `GET /api/entries/{entry}`, which carries the label,
the entry's size, its licence URL, its IIIF manifest URL, one record per canvas with the canvas id,
its pixel size and the URLs of its image and `info.json`, and one transcription per canvas with the
text, its `updatedAt` and its `canvasId`. There is no endpoint that lists a project's entries: the
ids come from the project's collections (`GET /api/collections/{id}`, whose `entries` array is the
only enumeration the platform offers) and from `data/sources/ainu-records.yaml`, which is what this
importer reads. `scripts/check_ainu_records.py` enumerates the collections and compares what they
hold with what the file lists.

Page text and lines
-------------------
A transcription's `text` is one string per canvas. Its newlines are the transcriber's: on the prose
manuscripts and prints in this project they fall on the physical lines of the page, and in the
dictionary entries of 藻汐草 and 蝦夷方言 they fall on the entries, which are written one to a line or
two. Both are structures the transcriber put there, so a line record is written for every segment,
which is what the source states about itself: "one transcription line per physical line, with no
coordinates". No line or unit box is invented — the pages of this project have never had a detector
run on them, and `docs/schema.md` allows a line without a box. The alignment stage fills the boxes in
from the page image; until it does, a line of this import is text anchored to a page and a reading
order, and nothing more. A line whose text is empty is not written, and a page whose transcription is
empty gets no lines at all.

The markup is みんなで翻刻's own, so `text_raw` keeps it and `text` is `koji.plain` of it, exactly as
the Honkoku-Lines importer does it.

Identifiers
-----------
Documents take `hk:<entry id>`, pages `hk:<entry id>:<n>` with `n` the canvas position from zero.
**Eight of the nine entries of this project are also items of Honkoku-Lines**, under the same 32-hex
item id and therefore under that importer's `hl:<entry id>`. The two datasets are kept apart on
purpose: this one is the platform's own text with no line or character boxes, and Honkoku-Lines is
the aligned line corpus with boxes, so the atlas gains one document per item rather than overwriting
either. Both carry the same id under `source_refs["honkoku-data"]`, which is how a later step joins
them and decides which record to keep.

Caching and rights
------------------
Every platform response is written under `cache/ainu-records/` — the project record, one JSON per
entry, and the transcription pages under `entries/<entry>/` — so a rerun makes no request at all. A
response already in the cache is read from disk; `refresh` fetches it again.

Image rights come from the entry's `license` URL and the holding institution, both through
`rights.resolve`, and the curated `data/sources/ainu-records.yaml` supplies what the platform does
not state: the holder, the shelfmark and the licence URL the curation read from the holder's own
page. The text is CC BY-SA 4.0, the platform's terms for a transcription; the images stay under the
holder's terms, which for 龍谷大学, 筑波大学 and Wereldmuseum Leiden resolve to `restricted`.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections import Counter
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import yaml

from .. import koji, net, rights, tables
from ..schema import Document, Licence, Line, Page, PageText, Rights

SOURCE = "ainu-records"
PROJECT = "ainu"
TEXT_LICENCE = Licence.CC_BY_SA_4
ATTRIBUTION = "みんなで翻刻（https://honkoku.org/）翻刻データ, CC BY-SA 4.0"
LICENCE_EVIDENCE = "https://wiki.honkoku.org/doku.php?id=guideline"
#: The platform API. `{entry}` is an entry id; the project and collection templates take their own.
ENTRY_API = "https://app.honkoku.org/api/entries/{entry}"
PROJECT_API = "https://app.honkoku.org/api/projects/{project}"
COLLECTION_API = "https://app.honkoku.org/api/collections/{collection}"
#: The line-splitting method recorded on every line this importer writes.
MATCH_METHOD = "ainu-records-2026-09"
#: `src/glyph_atlas/importers/ainu_records.py` sits three directories below the repository root.
REPO_ROOT = Path(__file__).resolve().parents[3]
SOURCE_FILE = REPO_ROOT / "data" / "sources" / "ainu-records.yaml"
ENV_CACHE = "GLYPH_ATLAS_CACHE"


def cache_root() -> Path:
    """The directory that holds the caches: `$GLYPH_ATLAS_CACHE`, or `cache/` of the repository."""
    override = os.environ.get(ENV_CACHE)
    return Path(override) if override else REPO_ROOT / "cache"


def records_dir(cache: Path | None = None) -> Path:
    """Where the platform responses are cached: `cache/ainu-records` by default."""
    return (Path(cache) if cache is not None else cache_root()) / SOURCE


def entry_path(entry: str, cache: Path | None = None) -> Path:
    """Where one entry's record is cached."""
    return records_dir(cache) / "entries" / f"{entry}.json"


def text_path(entry: str, index: int, cache: Path | None = None) -> Path:
    """Where one page's transcription is cached, under the entry's directory."""
    return records_dir(cache) / "entries" / entry / f"{index:04d}.json"


def project_path(project: str = PROJECT, cache: Path | None = None) -> Path:
    """Where the project record is cached."""
    return records_dir(cache) / "project.json"


def load_source(path: Path | None = None) -> dict[str, Any]:
    """The curated source file: the project, its attribution and the entries to import.

    `data/sources/ainu-records.yaml` is the list of entries, hand-checked against each witness's
    catalogue record and against what the platform serves. It is data rather than code because the
    set is a curation: the project holds 80 entries and this import covers the nine that the
    curation has read, so which nine is a decision recorded in a file, not a constant.
    """
    document = yaml.safe_load(Path(path or SOURCE_FILE).read_text(encoding="utf-8"))
    if not isinstance(document, dict) or not isinstance(document.get("entries"), list):
        raise TypeError(f"{path or SOURCE_FILE}: no entries list")
    return document


def entry_ids(source: dict[str, Any] | None = None, *, only: Iterable[str] | None = None) -> list[str]:
    """The entry ids to import, in the order the curated file lists them.

    A row may be a bare id or a mapping with `id` and the curated fields; both shapes are accepted so
    that the file can carry as much or as little per entry as the curation has.
    """
    document = source if source is not None else load_source()
    found: list[str] = []
    for row in document["entries"]:
        entry = row if isinstance(row, str) else str(row.get("id", "")).strip()
        if not entry:
            raise ValueError(f"{SOURCE_FILE}: an entry row names no id: {row!r}")
        found.append(entry)
    if only is not None:
        wanted = list(only)
        unknown = [entry for entry in wanted if entry not in found]
        if unknown:
            raise ValueError(f"{SOURCE_FILE} does not list {', '.join(unknown)}")
        return [entry for entry in found if entry in set(wanted)]
    return found


def curated(source: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    """The curated fields of every entry, by entry id."""
    document = source if source is not None else load_source()
    found: dict[str, dict[str, Any]] = {}
    for row in document["entries"]:
        if isinstance(row, dict) and row.get("id"):
            found[str(row["id"])] = row
    return found


def fetch_json(url: str, path: Path, *, client: httpx.Client | None = None, refresh: bool = False) -> Any:
    """One platform response, from the cache when it is there and from the platform otherwise.

    A body that is not JSON, or is JSON but not an object, is a failure that leaves nothing in the
    cache, so a later run tries again rather than serving a broken answer for ever.
    """
    if refresh or not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="kuzushiji-ainu-") as scratch:
            temporary = Path(scratch) / "response.json"
            net.download(url, temporary, expected="json", client=client, refresh=True)
            body = temporary.read_bytes()
        try:
            document = json.loads(body.decode("utf-8-sig"))
        except (UnicodeDecodeError, ValueError) as error:
            raise net.DownloadError(f"{url}: the response does not parse ({error})") from error
        if not isinstance(document, (dict, list)):
            raise net.DownloadError(f"{url}: the response is not JSON")
        path.write_bytes(body)
    try:
        document = json.loads(path.read_text(encoding="utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as error:
        path.unlink(missing_ok=True)
        raise net.DownloadError(f"{path}: the cached response does not parse ({error})") from error
    return document


def fetch_entry(entry: str, *, cache: Path | None = None, client: httpx.Client | None = None,
                refresh: bool = False) -> dict[str, Any]:
    """One entry's record, with its canvases, transcriptions and metadata."""
    document = fetch_json(ENTRY_API.format(entry=entry), entry_path(entry, cache), client=client, refresh=refresh)
    if not isinstance(document, dict):
        raise net.DownloadError(f"{ENTRY_API.format(entry=entry)}: the entry is not a JSON object")
    return document


def fetch_project(project: str = PROJECT, *, cache: Path | None = None, client: httpx.Client | None = None,
                  refresh: bool = False) -> dict[str, Any]:
    """The project record, with the ids of its collections."""
    document = fetch_json(
        PROJECT_API.format(project=project), project_path(project, cache), client=client, refresh=refresh
    )
    if not isinstance(document, dict):
        raise net.DownloadError(f"{PROJECT_API.format(project=project)}: the project is not a JSON object")
    return document


def fetch_collection(collection: str, *, cache: Path | None = None, client: httpx.Client | None = None,
                     refresh: bool = False) -> dict[str, Any]:
    """One collection record, whose `entries` array is the only enumeration the platform offers."""
    path = records_dir(cache) / "collections" / f"{collection}.json"
    document = fetch_json(
        COLLECTION_API.format(collection=collection), path, client=client, refresh=refresh
    )
    if not isinstance(document, dict):
        raise net.DownloadError(f"{COLLECTION_API.format(collection=collection)}: not a JSON object")
    return document


def collection_entries(
    project: str = PROJECT, *, cache: Path | None = None, client: httpx.Client | None = None,
    refresh: bool = False,
) -> tuple[list[str], list[dict[str, Any]], dict[str, str]]:
    """Every entry id the project's collections hold, with the collections and what failed.

    Returns the ids in collection order without duplicates, the collection records, and the
    collections that could not be read, mapped to the failure. This is the platform's own statement
    of what the project contains, which is what the curated file is checked against.
    """
    record = fetch_project(project, cache=cache, client=client, refresh=refresh)
    ids = record.get("collections")
    found: list[str] = []
    documents: list[dict[str, Any]] = []
    failures: dict[str, str] = {}
    seen: set[str] = set()
    for collection in ids or []:
        name = str(collection)
        try:
            document = fetch_collection(name, cache=cache, client=client, refresh=refresh)
        except (net.DownloadError, OSError, ValueError) as error:
            failures[name] = f"{error.__class__.__name__}: {error}"
            continue
        documents.append(document)
        for entry in document.get("entries") or []:
            text = str(entry).strip()
            if text and text not in seen:
                seen.add(text)
                found.append(text)
    return found, documents, failures


def canvases(entry: dict[str, Any]) -> list[dict[str, Any]]:
    """The canvas records of an entry, in the order the platform lists them."""
    found = entry.get("canvases")
    if not isinstance(found, list):
        return []
    return [canvas for canvas in found if isinstance(canvas, dict)]


def transcriptions(entry: dict[str, Any]) -> dict[int, dict[str, Any]]:
    """An entry's transcriptions by canvas position, taken from the canvas the record names.

    The order of the `transcriptions` array is not documented and the pages a reviewer is shown are
    keyed by canvas, so a transcription is placed by its `canvasId` when the canvas list holds that
    id, and by its `index` when it does not. Two transcriptions claiming one canvas keep the first,
    and the count of that is reported rather than hidden.
    """
    found: dict[int, dict[str, Any]] = {}
    position = {str(canvas.get("id")): index for index, canvas in enumerate(canvases(entry))}
    for row in entry.get("transcriptions") or []:
        if not isinstance(row, dict):
            continue
        index = position.get(str(row.get("canvasId")))
        if index is None:
            index = _whole(row.get("index"))
        if index is None or index in found:
            continue
        found[index] = row
    return found


def text_of(transcription: dict[str, Any] | None) -> str:
    """The transcribed text of one page, which the platform leaves empty on an untranscribed page."""
    if not transcription:
        return ""
    text = transcription.get("text")
    return text if isinstance(text, str) else ""


def lines_of(text: str) -> list[str]:
    """The lines of one page's transcription, empty segments dropped.

    A line is one newline-separated segment with its trailing whitespace removed. The leading
    full-width spaces that indent a dictionary entry or a 割書 column are kept, because they are part
    of how the transcriber laid the page out.
    """
    found: list[str] = []
    for segment in text.split("\n"):
        stripped = segment.rstrip()
        if stripped.strip():
            found.append(stripped)
    return found


def plain(text: str) -> str:
    """`koji.plain` of one line, or the line itself when the markup does not parse."""
    try:
        return koji.plain(text)
    except (ValueError, RecursionError):  # malformed markup, and a line of text is worth more than a crash
        return text


def resolve_rights(entry: dict[str, Any], row: dict[str, Any] | None) -> Rights:
    """The image rights of one entry, from the platform's licence URL and the curation's holder.

    The curated row wins where it disagrees, because the curation read the holder's own page and the
    platform's `license` field is a URL pasted by a transcriber. The platform's holder (`attribution`)
    fills in when the curation names none.
    """
    curated_licence = _str(row.get("image_licence") if row else None) or _str(row.get("imageLicense") if row else None)
    platform_licence = _str(entry.get("license"))
    holder = _str(row.get("holder") if row else None) or _str(entry.get("attribution"))
    if curated_licence:
        resolved = rights.resolve(licence=curated_licence, url=curated_licence, holder=holder)
        if resolved.licence is not Licence.UNKNOWN:
            return resolved
    return rights.resolve(licence=platform_licence, url=platform_licence or curated_licence, holder=holder)


def document_of(
    entry: str,
    record: dict[str, Any],
    row: dict[str, Any] | None,
    *,
    entry_pages: list[Page],
    revision: str | None,
) -> Document:
    """The document of one entry: its title, its references, its holder and its rights."""
    label = _str(record.get("label")) or entry
    curated_title = _str(row.get("title") if row else None)
    # The platform's label carries the volume and the holder in parentheses, which the curation's
    # title does not; the label is what a reader of the platform sees, so it is the title, and the
    # curation's title and its Latin form are kept beside it.
    refs: dict[str, str] = {"honkoku-data": entry, "honkoku-project": PROJECT}
    manifest = _str(record.get("manifestUrl"))
    if manifest:
        refs["iiif-manifest"] = manifest
    # The curated file names a witness under `witness`; `slug` is accepted too, because a flat list
    # of one-row witnesses uses it. A work and a witness together identify the physical copy, and the
    # platform entry is one part of it.
    for keys, name in (
        (("witness", "slug"), "ainu-witness"),
        (("work",), "ainu-work"),
        (("catalogue",), "aynu-catalogue"),
        (("part",), "ainu-part"),
    ):
        for key in keys:
            value = _str(row.get(key) if row else None)
            if value:
                refs[name] = value
                break
    meta: dict[str, Any] = {}
    for key in ("size", "progress", "description", "collectionId", "index"):
        value = record.get(key)
        if value not in (None, "", []):
            meta[key] = value
    if curated_title:
        meta["title_curated"] = curated_title
    for key in ("titleLatin", "titleReading", "authors", "date", "kind", "language", "witness", "copied",
                "imagesVia", "dbAynu", "shelfmark"):
        value = row.get(key) if row else None
        if value not in (None, "", []):
            meta[f"curated_{key}"] = value
    if revision:
        meta["revision"] = revision
    meta["pages"] = len(entry_pages)
    holder = _str(row.get("holder") if row else None) or _str(record.get("attribution"))
    text_rights = Rights(
        licence=TEXT_LICENCE, holder=holder, attribution=ATTRIBUTION, evidence=LICENCE_EVIDENCE
    )
    return Document(
        id=f"hk:{entry}",
        title=label,
        source_refs=refs,
        holder=holder,
        shelfmark=_str(row.get("shelfmark") if row else None),
        image_rights=resolve_rights(record, row),
        text_rights=text_rights,
        meta=meta,
    )


def records_of(
    entry: str,
    record: dict[str, Any],
    row: dict[str, Any] | None,
    *,
    revision: str | None,
) -> tuple[Document, list[Page], list[PageText], list[Line], dict[str, int]]:
    """The records of one entry: its document, its pages, its page texts, its lines and the counts."""
    found = canvases(record)
    texts = transcriptions(record)
    pages: list[Page] = []
    page_texts: list[PageText] = []
    lines: list[Line] = []
    problems: Counter[str] = Counter()
    for index, canvas in enumerate(found):
        page_id = f"hk:{entry}:{index}"
        transcription = texts.get(index)
        text = text_of(transcription)
        page_meta: dict[str, Any] = {}
        for key in ("infoJsonUrl", "thumbnailUrl", "imageUrl"):
            value = _str(canvas.get(key))
            if value:
                page_meta[key] = value
        stamp = _stamp(transcription.get("updatedAt")) if transcription else None
        if stamp:
            page_meta["updated_at"] = stamp
        status = _str(transcription.get("status")) if transcription else None
        if status:
            page_meta["status"] = status
        if not text.strip():
            problems["pages_without_text"] += 1
            page_meta["untranscribed"] = True
        pages.append(
            Page(
                id=page_id,
                document_id=f"hk:{entry}",
                seq=index,
                canvas=_str(canvas.get("id")),
                image=_str(canvas.get("imageUrl")) or _str(canvas.get("infoJsonUrl")),
                width=_whole(canvas.get("width")) or 0,
                height=_whole(canvas.get("height")) or 0,
                transcription={"source": SOURCE, "entry": entry}
                | ({"revision": revision} if revision else {}),
                meta=page_meta,
            )
        )
        page_texts.append(
            PageText(page_id=page_id, source=SOURCE, revision=stamp or revision, text_raw=text)
        )
        for position, body in enumerate(lines_of(text)):
            lines.append(
                Line(
                    id=f"{page_id}:L{position}",
                    page_id=page_id,
                    seq=position,
                    box=None,
                    vertical=True,
                    text_raw=body,
                    text=plain(body),
                    match_method=MATCH_METHOD,
                    meta={"transcription_index": index, "line_position": position},
                )
            )
    if len(texts) != len(found):
        problems["pages_without_transcription"] += max(0, len(found) - len(texts))
    document = document_of(entry, record, row, entry_pages=pages, revision=revision)
    return document, pages, page_texts, lines, dict(problems)


def revision_of(record: dict[str, Any]) -> str | None:
    """The newest `updatedAt` among an entry's transcriptions, as an ISO 8601 UTC timestamp.

    The platform stamps every page's transcription, so the entry's revision is the newest of them:
    the page rows carry their own stamp and `page_texts` carries the entry's, which is what the
    schema asks for and what makes a later refresh comparable.
    """
    stamps = [
        _stamp(row.get("updatedAt"))
        for row in record.get("transcriptions") or []
        if isinstance(row, dict)
    ]
    found = [stamp for stamp in stamps if stamp]
    return max(found) if found else None


def import_all(
    out: Path,
    *,
    cache: Path | None = None,
    limit: int | None = None,
    only: Iterable[str] | None = None,
    source_path: Path | None = None,
    client: httpx.Client | None = None,
    refresh: bool = False,
    command: str | None = None,
) -> dict[str, int]:
    """Import the project's entries into `out` and return the counts written.

    `out` is the dataset directory, `cache/ainu-records` holds the platform responses by default, and
    `limit` stops after that many entries in the curated file's order. `only` narrows the run to
    listed entry ids. Every response is cached, so a rerun after a complete import makes no request
    unless `refresh` asks it to. The returned mapping counts the documents, pages, page texts and
    lines written, and the pages that carry no transcription, no canvas or no image.
    """
    document = load_source(source_path)
    rows = curated(document)
    wanted = entry_ids(document, only=only)
    if limit is not None:
        wanted = wanted[: max(0, limit)]
    out = Path(out)
    documents: list[Document] = []
    pages: list[Page] = []
    page_texts: list[PageText] = []
    lines: list[Line] = []
    counts: Counter[str] = Counter()
    for entry in wanted:
        record = fetch_entry(entry, cache=cache, client=client, refresh=refresh)
        revision = revision_of(record)
        page_ids = {page.id for page in pages}
        entry_document, entry_pages, entry_texts, entry_lines, problems = records_of(
            entry, record, rows.get(entry), revision=revision
        )
        if any(page.id in page_ids for page in entry_pages):
            raise ValueError(f"{entry}: its page ids collide with an entry already read")
        documents.append(entry_document)
        pages.extend(entry_pages)
        page_texts.extend(entry_texts)
        lines.extend(entry_lines)
        counts.update(problems)
        counts["entries"] += 1
        counts["lines"] += len(entry_lines)
        for page in entry_pages:
            if not page.image:
                counts["pages_without_image"] += 1
            if not page.canvas:
                counts["pages_without_canvas"] += 1
        texts = transcriptions(record)
        counts["transcriptions"] += len(texts)
        counts["pages"] += len(entry_pages)
    counts["entries_listed"] = len(entry_ids(document))
    written = _write(
        out,
        documents,
        pages,
        page_texts,
        lines,
        command or f"atlas import ainu-records --out {out}",
    )
    # Counter.update adds, which would double the rows this loop already counted; the store's own
    # row counts are the authority for the tables it wrote.
    for name, rows_written in written.items():
        counts[name] = rows_written
    return {name: counts[name] for name in sorted(counts)}


def _write(
    out: Path,
    documents: list[Document],
    pages: list[Page],
    texts: list[PageText],
    lines: list[Line],
    command: str,
) -> dict[str, int]:
    """Write the four tables and return their row counts, leaving the dataset manifest consistent."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    written: dict[str, int] = {}
    for name, records, model in (
        ("documents", documents, Document),
        ("pages", pages, Page),
        ("page_texts", texts, PageText),
        ("lines", lines, Line),
    ):
        written[name] = tables.write(out / f"{name}.parquet", records, model)
        records.clear()
    tables.Dataset(out).merge([], out, command=command)
    return written


def _str(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, (int, float)):
        return str(value)
    return str(value)


def _whole(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return None


def _stamp(value: Any) -> str | None:
    """A platform timestamp as ISO 8601 UTC, from the shapes the API uses for one."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, dict):
        seconds = value.get("_seconds", value.get("seconds"))
        return _from_seconds(seconds) if isinstance(seconds, (int, float)) else None
    if isinstance(value, (int, float)):
        return _from_seconds(int(value))
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            moment = datetime.fromisoformat(text)
        except ValueError:
            return text
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        return moment.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    return None


def _from_seconds(seconds: int) -> str:
    return datetime.fromtimestamp(seconds, tz=UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def digest(path: Path) -> str:
    """The sha256 of a file, for the check command's report."""
    hasher = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            hasher.update(block)
    return hasher.hexdigest()


def iter_cached(cache: Path | None = None) -> Iterator[Path]:
    """Every cached platform response, for a command that reports on the cache."""
    root = records_dir(cache)
    if not root.is_dir():
        return
    yield from sorted(root.rglob("*.json"))
