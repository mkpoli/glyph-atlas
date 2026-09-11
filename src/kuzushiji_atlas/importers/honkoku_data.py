"""Import みんなで翻刻データ v3 and the IIIF manifests behind its pages.

The clone holds `v3/<project>/info.tsv` (columns id, label, manifestUrl, projectId, size, progress,
attribution, thumbnail) and `v3/<project>/<entry>/<NNN>.txt`, one file per page of the entry. A text
file holds the transcription as it stands on the platform, markup and 【右丁】/【左丁】 markers
included; `v3/<project>/<entry>/info.tsv` and `translations/` beside it are not page text. Page `NNN`
is canvas `NNN` of the entry's manifest: the canvases of the first sequence for IIIF Presentation 2,
the canvas items for Presentation 3.

A manifest is fetched once into `cache/manifests/<sha256 of url>.json` through `net.download`, each
manifest is attempted `MANIFEST_RETRIES` times, and a host that fails `HOST_FAILURES` manifests in a
row is left alone for the rest of the run: 資料編纂所 answers HTTP 500 for the manifest of every
entry of a project, and five attempts at each of 941 manifests is hours of retrying a server that
says no. An entry whose manifest cannot be fetched keeps its pages, with an empty `image` and the
failure in `meta.manifest_error`; a later run retries it, since a manifest that failed is not in the
cache. Text files beyond the manifest's canvases become pages with a null `canvas` and a warning.

The images belong to the holder that `attribution` names, read from the manifest where it states its
own rights; the transcription is CC BY-SA 4.0. `refresh` asks the platform API for the current text
of one entry and writes it beside the clone's text as `page_texts` rows with `source =
"honkoku-api"` and the revision the platform states.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import tempfile
import warnings
from collections import Counter
from collections.abc import Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from .. import net, rights, tables
from ..images import INFO_SUFFIX, REQUEST_SUFFIX, service_of
from ..schema import Document, Licence, Page, PageText, Rights

SOURCE = "honkoku-data"
API_SOURCE = "honkoku-api"
TEXT_LICENCE = Licence.CC_BY_SA_4
ATTRIBUTION = "みんなで翻刻（https://honkoku.org/）翻刻データ, CC BY-SA 4.0"
LICENCE_EVIDENCE = "https://github.com/yuta1984/honkoku-data/blob/master/README.md"
#: The platform API, with `{entry}` standing for the entry id.
API = "https://app.honkoku.org/api/entries/{entry}"
INFO_COLUMNS = ("id", "label", "manifestUrl", "projectId", "size", "progress", "attribution", "thumbnail")
PAGE_FILE = re.compile(r"^(\d+)\.txt$")
#: `src/kuzushiji_atlas/importers/honkoku_data.py` sits three directories below the repository root.
REPO_ROOT = Path(__file__).resolve().parents[3]
ENV_CACHE = "KUZUSHIJI_ATLAS_CACHE"
#: Manifest hosts each answer at their own pace, so a run keeps one worker per host and several
#: hosts in flight; the requests to one host stay `net.host_pause` apart inside its worker.
WORKERS = 12
#: Attempts per manifest. Three cover the intermittent 429 that Gallica answers; a host that keeps
#: failing is dropped by `_fetch_host` instead of being asked five times for each of its manifests.
MANIFEST_RETRIES = 3
#: Manifests one host may fail in a row before the run leaves it alone. 資料編纂所 answered 500 for
#: 941 manifests of a project, and retrying each of them five times would have taken hours.
HOST_FAILURES = 5
#: Branches of a canvas that hold no page image.
NOT_AN_IMAGE = frozenset({"thumbnail", "rendering", "otherContent", "seeAlso", "logo", "service"})


def cache_root() -> Path:
    """The directory that holds the caches: `$KUZUSHIJI_ATLAS_CACHE`, or `cache/` of the repository."""
    override = os.environ.get(ENV_CACHE)
    return Path(override) if override else REPO_ROOT / "cache"


def default_clone() -> Path:
    """`cache/honkoku-data`, the clone `atlas import honkoku-data` reads without `--clone`."""
    return cache_root() / "honkoku-data"


def manifests_dir(cache: Path | None = None) -> Path:
    """The manifest cache `cache/manifests`, or the same directory under `cache`."""
    return (Path(cache) if cache is not None else cache_root()) / "manifests"


def manifest_path(url: str, cache: Path | None = None) -> Path:
    """Where the manifest of `url` is stored: `cache/manifests/<sha256 of url>.json`."""
    return manifests_dir(cache) / f"{hashlib.sha256(url.encode('utf-8')).hexdigest()}.json"


def clone_revision(clone: Path) -> str | None:
    """The commit a clone is at, read from its `.git`, or None when it is not a git checkout."""
    git = Path(clone) / ".git"
    if git.is_file():
        pointer = _text(git)
        if pointer is None or not pointer.startswith("gitdir:"):
            return pointer or None
        git = (Path(clone) / pointer.split(":", 1)[1].strip()).resolve()
    head = _text(git / "HEAD")
    if not head:
        return None
    if not head.startswith("ref:"):
        return head
    reference = head.split(":", 1)[1].strip()
    direct = _text(git / reference)
    if direct:
        return direct
    packed = _text(git / "packed-refs") or ""
    for line in packed.splitlines():
        parts = line.split(" ", 1)
        if len(parts) == 2 and parts[1].strip() == reference:
            return parts[0].strip()
    return None


def _text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None


def read_info(path: Path) -> list[dict[str, str]]:
    """The rows of a project's `info.tsv`, in file order."""
    with Path(path).open(encoding="utf-8", newline="") as handle:
        return [row for row in csv.DictReader(handle, delimiter="\t")]


def entries(clone: Path, projects: list[str] | None = None) -> tuple[list[tuple[str, dict[str, str]]], int, int]:
    """The entries of a clone as `(project, info row)`, ordered by project and entry id.

    A row whose entry directory is absent names no text and is counted as skipped instead; a
    directory that no row names is counted as unlisted. Both are anomalies of the upstream clone.
    """
    root = Path(clone) / "v3"
    wanted = set(projects) if projects else None
    found: list[tuple[str, dict[str, str]]] = []
    skipped = unlisted = 0
    for project_dir in sorted(path for path in root.iterdir() if path.is_dir()):
        project = project_dir.name
        if wanted is not None and project not in wanted:
            continue
        info = project_dir / "info.tsv"
        if not info.is_file():
            warnings.warn(f"{project}: no info.tsv in the clone; skipped", RuntimeWarning, stacklevel=2)
            continue
        listed: set[str] = set()
        for row in sorted(read_info(info), key=lambda row: row.get("id") or ""):
            entry = (row.get("id") or "").strip()
            if not entry or not (project_dir / entry).is_dir():
                skipped += 1
                continue
            listed.add(entry)
            found.append((project, row))
        unlisted += sum(1 for path in project_dir.iterdir() if path.is_dir() and path.name not in listed)
    if wanted is not None:
        for project in sorted(wanted):
            if not (root / project).is_dir():
                warnings.warn(f"{project}: not a project of {root}; skipped", RuntimeWarning, stacklevel=2)
    return found, skipped, unlisted


def page_files(entry_dir: Path) -> list[tuple[int, Path]]:
    """The `NNN.txt` pages of one entry, by page number; `translations/` and `info.tsv` are not pages."""
    found = []
    for path in Path(entry_dir).iterdir():
        if not path.is_file():
            continue
        match = PAGE_FILE.match(path.name)
        if match:
            found.append((int(match.group(1)), path))
    found.sort()
    return found


def canvases(manifest: dict) -> list[dict]:
    """The canvases of a manifest: those of its first sequence, or of its `items`."""
    if not isinstance(manifest, dict):
        return []
    sequences = manifest.get("sequences")
    if isinstance(sequences, list):
        for sequence in sequences:
            found = sequence.get("canvases") if isinstance(sequence, dict) else None
            if isinstance(found, list):
                return [canvas for canvas in found if isinstance(canvas, dict)]
    items = manifest.get("items")
    if isinstance(items, list):
        found = [item for item in items if isinstance(item, dict)]
        marked = [item for item in found if _is_canvas(item)]
        return marked or found
    return []


def _is_canvas(node: dict) -> bool:
    return _kind(node) == "canvas"


def _kind(node: dict) -> str:
    value = node.get("type") or node.get("@type") or ""
    return str(value).rsplit(":", 1)[-1].lower()


def is_image(node: dict) -> bool:
    """Whether a node of a canvas is an image resource rather than an annotation around one."""
    if _kind(node) == "image":
        return True
    media = node.get("format")
    return isinstance(media, str) and media.lower().startswith("image/")


def image_nodes(canvas: dict) -> Iterator[dict]:
    """The image resources of a canvas, depth first, without its thumbnails or renderings."""
    for key in ("images", "items"):
        if key in canvas:
            yield from _image_nodes(canvas[key])


def _image_nodes(value: Any) -> Iterator[dict]:
    if isinstance(value, dict):
        if is_image(value):
            yield value
            return
        for key, item in value.items():
            if key not in NOT_AN_IMAGE:
                yield from _image_nodes(item)
    elif isinstance(value, list):
        for item in value:
            yield from _image_nodes(item)


def canvas_image(canvas: dict) -> str:
    """The image service of a canvas's first image, or the image URL when it names no service.

    A service the manifest states is kept as it stands apart from a trailing `info.json` or image
    request: 龍谷大学 carries the image path in a query string, which `images.service_of` drops. An
    image that names no service is cut back to the service it belongs to.
    """
    nodes = list(image_nodes(canvas))
    for node in nodes:
        service = identifier(node.get("service"))
        if service:
            return _service_base(service)
    for node in nodes:
        url = identifier(node.get("id") if node.get("id") else node.get("@id"))
        if url:
            return service_of(url) or url
    return ""


def _service_base(url: str) -> str:
    """A service URL without a trailing `info.json` or image request, keeping any query string."""
    cleaned = url.split("#", 1)[0]
    for pattern in (INFO_SUFFIX, REQUEST_SUFFIX):
        found = pattern.search(cleaned)
        if found:
            return cleaned[: found.start()].rstrip("/") or cleaned
    return cleaned.rstrip("/") or url


def canvas_size(canvas: dict) -> tuple[int, int]:
    """The pixels of a canvas, from the canvas or failing that from its first image."""
    width, height = _pixels(canvas.get("width")), _pixels(canvas.get("height"))
    if width and height:
        return width, height
    for node in image_nodes(canvas):
        width = width or _pixels(node.get("width"))
        height = height or _pixels(node.get("height"))
        if width and height:
            break
    return width, height


def canvas_page(canvas: dict) -> tuple[str | None, str, int, int]:
    """One page of a manifest canvas: its id, its image, its width and its height."""
    canvas_id = identifier(canvas.get("id") if canvas.get("id") else canvas.get("@id"))
    width, height = canvas_size(canvas)
    return canvas_id, canvas_image(canvas), width, height


def identifier(value: Any) -> str | None:
    """A IIIF identifier: a string, the first of a list, or the `id`/`@id` of a mapping."""
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, list):
        for item in value:
            found = identifier(item)
            if found:
                return found
        return None
    if isinstance(value, dict):
        for key in ("id", "@id"):
            found = identifier(value.get(key))
            if found:
                return found
    return None


def _pixels(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, float):
        return max(0, int(value))
    return 0


def fetch_manifest(
    url: str, *, cache: Path | None = None, client: httpx.Client | None = None, retries: int = MANIFEST_RETRIES
) -> dict:
    """Fetch one manifest into the manifest cache and parse it.

    A URL already in the cache is read from disk without a request. The body decides what the
    manifest is: ADEAC serves it with a byte order mark and 龍谷大学 labels a JSON body `text/html`,
    so the content type is not checked and a body that does not parse as a JSON object fails as a
    `net.DownloadError` and is left out of the cache.
    """
    path = manifest_path(url, cache)
    net.download(url, path, client=client, retries=retries)
    try:
        document = json.loads(path.read_text(encoding="utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as error:
        path.unlink(missing_ok=True)
        raise net.DownloadError(f"{url}: the manifest does not parse ({error})") from error
    if not isinstance(document, dict):
        path.unlink(missing_ok=True)
        raise net.DownloadError(f"{url}: the manifest is not a JSON object")
    return document


def manifests(
    urls: Iterable[str], *, cache: Path | None = None, client: httpx.Client | None = None, workers: int = WORKERS
) -> tuple[dict[str, tuple[dict | None, str | None]], list[str]]:
    """Fetch every manifest once and return `url -> (manifest, error)` and the hosts left alone.

    Hosts are fetched in parallel, one worker per host, so that no host sees two requests closer
    together than its pause; the returned mapping is keyed by URL and holds the failure instead of
    raising it. A host that fails `HOST_FAILURES` manifests in a row is abandoned after that, and
    the rest of its manifests carry the failure it answered with.
    """
    wanted = sorted({url for url in urls if url})
    queues: dict[str, list[str]] = {}
    for url in wanted:
        queues.setdefault(urlsplit(url).netloc.lower(), []).append(url)
    if not queues:
        return {}, []
    if client is not None:
        return _across_hosts(queues, cache, client, workers)
    with httpx.Client(timeout=60.0, follow_redirects=True) as shared:
        return _across_hosts(queues, cache, shared, workers)


def _across_hosts(
    queues: dict[str, list[str]], cache: Path | None, client: httpx.Client, workers: int
) -> tuple[dict[str, tuple[dict | None, str | None]], list[str]]:
    """Fetch the hosts with the longest queues first, so that a large host does not start last."""
    found: dict[str, tuple[dict | None, str | None]] = {}
    abandoned: list[str] = []
    order = sorted(queues.items(), key=lambda item: len(item[1]), reverse=True)
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(order)))) as pool:
        futures = {host: pool.submit(_fetch_host, queue, cache, client) for host, queue in order}
        for host, future in futures.items():
            fetched, gave_up = future.result()
            found.update(fetched)
            if gave_up:
                abandoned.append(host)
    return found, sorted(abandoned)


def _fetch_host(
    urls: list[str], cache: Path | None, client: httpx.Client
) -> tuple[dict[str, tuple[dict | None, str | None]], bool]:
    """Fetch the manifests of one host in turn, so that its pause holds.

    The host is abandoned once `HOST_FAILURES` of its manifests fail in a row; every manifest left
    in its queue records the failure the host answered with, so that the entries it belongs to say
    why they have no image.
    """
    found: dict[str, tuple[dict | None, str | None]] = {}
    failure = "no attempt was made"
    failed = 0
    for url in urls:
        if failed >= HOST_FAILURES:
            found[url] = (None, f"{HOST_FAILURES} manifests of this host failed in a row; left alone ({failure})")
            continue
        try:
            found[url] = (fetch_manifest(url, cache=cache, client=client), None)
            failed = 0
        except (net.DownloadError, OSError, ValueError) as error:
            failure = f"{error.__class__.__name__}: {error}"
            found[url] = (None, failure)
            failed += 1
    return found, failed >= HOST_FAILURES


def import_all(
    out: Path,
    *,
    clone: Path | None = None,
    projects: list[str] | None = None,
    limit: int | None = None,
    cache: Path | None = None,
    client: httpx.Client | None = None,
    workers: int = WORKERS,
    command: str | None = None,
) -> dict[str, int]:
    """Import the entries of a honkoku-data clone into `out` and return the counts written.

    `clone` is the checkout, `cache/honkoku-data` by default; `projects` limits the run to those
    project ids and `limit` to that many entries, in project and entry id order. `cache` names the
    directory that holds `manifests/`, `$KUZUSHIJI_ATLAS_CACHE` by default. The tables hold one
    document per entry, one page per `NNN.txt`, and one `page_texts` row per page with the clone's
    commit as the revision; `command` is recorded in the dataset manifest. `projects` counts the
    project directories read, `projects_with_entries` those that hold an entry, since a project may
    ship a `info.tsv` with no rows.
    """
    clone = Path(clone) if clone is not None else default_clone()
    root = clone / "v3"
    if not root.is_dir():
        raise FileNotFoundError(f"{clone}: no v3 directory; expected a clone of honkoku-data")
    revision = clone_revision(clone)
    wanted = set(projects) if projects else None
    selected = [path for path in root.iterdir() if path.is_dir() and (wanted is None or path.name in wanted)]
    rows, skipped, unlisted = entries(clone, projects)
    if limit is not None:
        rows = rows[: max(0, limit)]

    urls = [(row.get("manifestUrl") or "").strip() for _, row in rows]
    fetched, abandoned = manifests(urls, cache=cache, client=client, workers=workers)
    documents: list[Document] = []
    pages: list[Page] = []
    texts: list[PageText] = []
    counts: Counter[str] = Counter()
    seen: set[str] = set()
    for (project, row), url in zip(rows, urls, strict=True):
        document, entry_pages, entry_texts, problems = records(
            clone, project, row, fetched.get(url, (None, None)), revision
        )
        seen.add(project)
        counts["manifest_errors"] += problems["manifest_error"]
        counts["pages_without_canvas"] += problems["no_canvas"]
        counts["pages_without_image"] += problems["no_image"]
        documents.append(document)
        pages.extend(entry_pages)
        texts.extend(entry_texts)

    written = _write(out, documents, pages, texts, command or f"atlas import honkoku-data --clone {clone}")
    counts["projects"] = len(selected)
    counts["projects_with_entries"] = len(seen)
    counts.update(written)
    counts["entries_skipped"] = skipped
    counts["entries_unlisted"] = unlisted
    counts["hosts_abandoned"] = len(abandoned)
    return {name: counts[name] for name in sorted(counts)}


def records(
    clone: Path,
    project: str,
    row: dict[str, str],
    manifest: tuple[dict | None, str | None],
    revision: str | None,
) -> tuple[Document, list[Page], list[PageText], dict[str, int]]:
    """The records of one entry: its document, its pages, its page texts and what went wrong.

    The counts name the entries whose manifest could not be read, the pages that are beyond the
    canvases of their manifest, and the pages that end up with no image either way.
    """
    entry = (row.get("id") or "").strip()
    project_id = (row.get("projectId") or "").strip() or project
    url = (row.get("manifestUrl") or "").strip()
    document_data, failure = manifest
    attribution = row.get("attribution")
    holder_rights = rights.resolve(holder=attribution)
    holder = holder_rights.holder
    image_rights = rights.manifest_rights(document_data) if document_data is not None else None
    if image_rights is None:
        image_rights = holder_rights
    elif holder and not image_rights.holder:
        image_rights = image_rights.model_copy(update={"holder": holder})

    refs = {"honkoku-data": entry, "honkoku-project": project_id}
    if url:
        refs["iiif-manifest"] = url
    document = Document(
        id=f"hk:{entry}",
        title=(row.get("label") or "").strip() or entry,
        source_refs=refs,
        holder=holder,
        image_rights=image_rights,
        text_rights=Rights(
            licence=TEXT_LICENCE, holder=holder, attribution=ATTRIBUTION, evidence=LICENCE_EVIDENCE
        ),
        meta={key: row[key] for key in ("size", "progress") if row.get(key)},
    )

    found = canvases(document_data) if document_data is not None else []
    if document_data is None and failure is None:
        failure = f"no manifestUrl in info.tsv of {project}"
    carried = {"source": SOURCE, "entry": entry}
    if revision:
        carried["revision"] = revision
    problems = {"manifest_error": 0, "no_canvas": 0, "no_image": 0}
    if failure is not None:
        problems["manifest_error"] = 1
    pages: list[Page] = []
    texts: list[PageText] = []
    for position, path in page_files(clone / "v3" / project / entry):
        page_id = f"hk:{entry}:{position}"
        meta: dict[str, Any] = {}
        canvas_id: str | None = None
        image = ""
        width = height = 0
        if position <= len(found):
            canvas_id, image, width, height = canvas_page(found[position - 1])
        elif failure is not None:
            meta["manifest_error"] = failure
        else:
            meta["canvas_missing"] = True
            problems["no_canvas"] += 1
            warnings.warn(
                f"{page_id}: {url} has {len(found)} canvases; the page keeps no image",
                RuntimeWarning,
                stacklevel=2,
            )
        if not image:
            problems["no_image"] += 1
        pages.append(
            Page(
                id=page_id,
                document_id=document.id,
                seq=position,
                canvas=canvas_id,
                image=image,
                width=width,
                height=height,
                transcription=dict(carried),
                meta=meta,
            )
        )
        texts.append(
            PageText(page_id=page_id, source=SOURCE, revision=revision, text_raw=path.read_text(encoding="utf-8"))
        )
    return document, pages, texts, problems


def refresh(
    out: Path,
    entry: str,
    *,
    base: str = API,
    client: httpx.Client | None = None,
    command: str | None = None,
) -> int:
    """Write the platform's current text of one entry as `page_texts` rows and return their number.

    The rows carry `source = "honkoku-api"` and the transcription's `updatedAt` as the revision, and
    sit beside the rows the clone supplied; a page whose transcription is not in `out` is left out.
    `base` is the API template, with `{entry}` standing for the entry id.
    """
    out = Path(out)
    pages_path = out / "pages.parquet"
    if not pages_path.is_file():
        raise FileNotFoundError(f"{out}: no pages table; import the entry before refreshing it")
    entry = entry.removeprefix("hk:")
    known = {page.id for page in tables.read(pages_path, Page)}
    document = _api_entry(base, entry, client=client)

    rows: list[PageText] = []
    missing = 0
    for transcription in document.get("transcriptions") or []:
        if not isinstance(transcription, dict):
            continue
        index = _whole(transcription.get("index"))
        text = transcription.get("text")
        if index is None or not isinstance(text, str):
            continue
        page_id = f"hk:{entry}:{index + 1}"
        if page_id not in known:
            missing += 1
            continue
        rows.append(
            PageText(page_id=page_id, source=API_SOURCE, revision=_updated_at(transcription.get("updatedAt")), text_raw=text)
        )
    if missing:
        warnings.warn(f"hk:{entry}: {missing} transcriptions name no page of {out}", RuntimeWarning, stacklevel=2)

    texts_path = out / "page_texts.parquet"
    kept = [] if not texts_path.is_file() else [
        row
        for row in tables.read(texts_path, PageText)
        if not (row.source == API_SOURCE and row.page_id.startswith(f"hk:{entry}:"))
    ]
    if rows or texts_path.is_file():
        tables.write(texts_path, [*kept, *rows], PageText)
        tables.Dataset(out).merge([], out, command=command or f"atlas honkoku refresh {entry}")
    return len(rows)


def _api_entry(base: str, entry: str, *, client: httpx.Client | None = None) -> dict:
    """The platform's record of an entry, fetched fresh."""
    url = base.format(entry=entry)
    with tempfile.TemporaryDirectory(prefix="kuzushiji-honkoku-") as scratch:
        path = Path(scratch) / "entry.json"
        net.download(url, path, expected="json", client=client, refresh=True)
        document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise net.DownloadError(f"{url}: the entry is not a JSON object")
    return document


def _updated_at(value: Any) -> str | None:
    """`updatedAt` as an ISO 8601 UTC timestamp, from the shapes the API uses for it."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return _moment(int(value))
    if isinstance(value, dict):
        seconds = value.get("_seconds", value.get("seconds"))
        return _moment(seconds) if isinstance(seconds, (int, float)) else None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        stamp = re.search(r"(?<![A-Za-z])_?seconds['\"]?\s*:\s*(\d+)", text)
        if stamp:
            return _moment(int(stamp.group(1)))
        try:
            moment = datetime.fromisoformat(text)
        except ValueError:
            return text
        return _iso(moment)
    return None


def _moment(seconds: int) -> str:
    return _iso(datetime.fromtimestamp(seconds, tz=UTC))


def _iso(moment: datetime) -> str:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _whole(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return None


def _write(
    out: Path, documents: list[Document], pages: list[Page], texts: list[PageText], command: str
) -> dict[str, int]:
    """Write the three tables and return their row counts, leaving the dataset manifest consistent.

    Each list is emptied once its table is on disk, since the merge that follows reads the tables
    back and the whole corpus does not fit in memory twice.
    """
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    written = {}
    for name, records, model in (
        ("documents", documents, Document),
        ("pages", pages, Page),
        ("page_texts", texts, PageText),
    ):
        written[name] = tables.write(out / f"{name}.parquet", records, model)
        records.clear()
    tables.Dataset(out).merge([], out, command=command)
    return written
