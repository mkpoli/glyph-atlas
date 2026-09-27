"""Import collected user projects from みんなで翻刻 as plain text tables.

The collector has already done the slow work: each completed book under `work/honkoku-collection`
is a tiny tables dataset with one document, its pages, and the page transcriptions returned by the
platform API. This importer is the publishing step for a curated set of user-created projects. It
does not ask the platform for anything; it reads those book datasets and the queue database, keeps
only pages that have an image and text, and turns the page text into unboxed line records.

Honkoku-Lines already supplies located lines for some of the same platform entries. Those pages are
left out here so the plain-text import only fills the gap that alignment can later process.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path
from typing import Any

import yaml

from .. import images, koji, rights, tables
from ..schema import Document, Licence, Line, Page, PageText, Rights
from . import honkoku_data
from .ainu_records import lines_of, plain
from .honkoku_queue import QUEUE_FILE

MATCH_METHOD = "honkoku-collection-2026-09"
PROJECT_META_TYPE = "user"
#: A question mark is never a glyph of the pre-modern pages these projects transcribe: transcribers
#: write it after a character they are unsure of (やさしい漢文 and やさしい変体仮名の読み物 say so
#: in their rules, and berabou does it without saying). Alignment would take it for a character.
QUERY_MARKS = frozenset("？?")
#: The koji roles that are text of the document; a mark in a note or a reading is already out of
#: alignment's way.
DOCUMENT_ROLES = frozenset({"main", "warigaki", "inserted"})
#: The IIIF size a canvas asks for when the platform recorded a width of 0; the server refuses it.
_ZERO_SIZE = re.compile(r"/full/(?:0,|,0|0,0)/", re.IGNORECASE)


def load_selection(path: Path) -> list[dict[str, Any]]:
    """The curated project rows, in the order the file lists them."""
    document = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    projects = document.get("projects") if isinstance(document, dict) else None
    if not isinstance(projects, list):
        raise TypeError(f"{path}: no projects list")
    rows: list[dict[str, Any]] = []
    for position, row in enumerate(projects):
        if not isinstance(row, dict) or not str(row.get("id", "")).strip():
            raise ValueError(f"{path}: project row {position} names no id")
        rows.append(row)
    return rows


def import_all(
    out: Path,
    *,
    collection: Path,
    selection: Path,
    honkoku_lines: Path | None = None,
    command: str | None = None,
) -> dict[str, int]:
    """Import the selected collected books into `out` and return the counts written."""
    collection = Path(collection)
    selection = Path(selection)
    rows = load_selection(selection)
    covered = honkoku_lines_coverage(honkoku_lines)
    documents: list[Document] = []
    pages: list[Page] = []
    page_texts: list[PageText] = []
    lines: list[Line] = []
    counts: Counter[str] = Counter()
    counts["projects"] = len(rows)
    for project in rows:
        project_id = str(project["id"]).strip()
        books = done_books(collection, project_id)
        if books:
            counts["projects_with_books"] += 1
        for book in books:
            book_documents, book_pages, book_texts = read_book(collection / book["dataset"])
            if len(book_documents) != 1:
                raise ValueError(f"{book['dataset']}: expected one document, found {len(book_documents)}")
            document, applied = document_of(book_documents[0], project)
            if applied:
                counts["manifest_rights_applied"] += 1
            documents.append(document)
            text_by_page = {text.page_id: text for text in book_texts}
            counts["books"] += 1
            for page in book_pages:
                text = text_by_page.get(page.id)
                without_image = not page.image.strip()
                without_text = text is None or not text.text_raw.strip()
                covered_by_lines = (entry_id(document).casefold(), page.seq - 1) in covered
                if without_image:
                    counts["pages_without_image"] += 1
                if without_text:
                    counts["pages_without_text"] += 1
                if covered_by_lines:
                    counts["pages_covered_by_honkoku_lines"] += 1
                if without_image or without_text or covered_by_lines:
                    continue
                image = fetchable_image(page.image)
                if image != page.image:
                    counts["pages_image_repaired"] += 1
                    page = page.model_copy(update={"image": image})
                pages.append(page)
                page_texts.append(text)
                lines.extend(lines_of_page(page, text.text_raw))

    written = _write(
        out,
        documents,
        pages,
        page_texts,
        lines,
        command or f"atlas import honkoku-collection --collection {collection} --selection {selection} --out {out}",
    )
    for name in (
        "books",
        "documents",
        "pages",
        "page_texts",
        "lines",
        "pages_without_image",
        "pages_without_text",
        "pages_covered_by_honkoku_lines",
        "manifest_rights_applied",
        "pages_image_repaired",
        "projects_with_books",
    ):
        counts.setdefault(name, 0)
    for name, rows_written in written.items():
        counts[name] = rows_written
    return {name: counts[name] for name in sorted(counts)}


def done_books(collection: Path, project_id: str) -> list[sqlite3.Row]:
    """Completed books of one project, ordered deterministically."""
    queue = Path(collection) / QUEUE_FILE
    with closing(sqlite3.connect(f"{queue.absolute().as_uri()}?mode=ro&immutable=1", uri=True)) as db:
        db.row_factory = sqlite3.Row
        return db.execute(
            "SELECT entry_id, dataset FROM books WHERE project_id=? AND state='done' ORDER BY entry_id",
            (project_id,),
        ).fetchall()


def read_book(directory: Path) -> tuple[list[Document], list[Page], list[PageText]]:
    """The three tables that one collected book publishes."""
    directory = Path(directory)
    return (
        tables.read(directory / "documents.parquet", Document),
        tables.read(directory / "pages.parquet", Page),
        tables.read(directory / "page_texts.parquet", PageText),
    )


def honkoku_lines_coverage(directory: Path | None) -> set[tuple[str, int]]:
    """Pages already present in Honkoku-Lines, keyed by platform entry id and 0-based image index."""
    if directory is None:
        return set()
    dataset = tables.Dataset(Path(directory))
    if dataset.tables["documents"] is None or dataset.tables["pages"] is None:
        return set()
    by_document: dict[str, str] = {}
    for batch in dataset.scan("documents", columns=["id", "source_refs"]):
        for document in batch:
            entry = document.source_refs.get("honkoku-data")
            if entry:
                by_document[document.id] = entry.casefold()
    covered: set[tuple[str, int]] = set()
    if not by_document:
        return covered
    for batch in dataset.scan("pages", columns=["document_id", "seq"]):
        for page in batch:
            entry = by_document.get(page.document_id)
            if entry is not None:
                covered.add((entry, page.seq))
    return covered


def document_of(document: Document, project: dict[str, Any]) -> tuple[Document, bool]:
    """A collected document with this selection's project reference and metadata added."""
    project_id = str(project["id"]).strip()
    refs = dict(document.source_refs)
    refs["honkoku-project"] = project_id
    meta = dict(document.meta)
    meta["honkoku_project"] = {
        "id": project_id,
        "type": PROJECT_META_TYPE,
        "practice": bool(project.get("practice")),
        "guidelines": str(project.get("guidelines") or ""),
    }
    image_rights, applied = manifest_image_rights(document)
    return document.model_copy(update={"source_refs": refs, "meta": meta, "image_rights": image_rights}), applied


def manifest_image_rights(document: Document) -> tuple[Rights | None, bool]:
    """Image rights, upgraded from a cached manifest when the collected book did not state them."""
    current = document.image_rights
    if not _needs_manifest_rights(current):
        return current, False
    url = document.source_refs.get("iiif-manifest", "")
    path = honkoku_data.manifest_path(url)
    if not url or not path.is_file():
        return current, False
    try:
        document_data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, ValueError):
        return current, False
    holder_rights = rights.resolve(holder=document.holder)
    holder = holder_rights.holder
    manifest = rights.manifest_rights(document_data) if document_data is not None else None
    if manifest is None:
        return current, False
    if holder and not manifest.holder:
        manifest = manifest.model_copy(update={"holder": holder})
    return manifest, True


def _needs_manifest_rights(image_rights: Rights | None) -> bool:
    if image_rights is None or image_rights.licence is Licence.UNKNOWN:
        return True
    return image_rights.licence is Licence.PUBLIC_DOMAIN and image_rights.holder_terms is Licence.UNKNOWN


def entry_id(document: Document) -> str:
    """The platform entry id of a collected document."""
    found = document.source_refs.get("honkoku-data") or document.id.removeprefix("hk:")
    return found.strip()


def fetchable_image(url: str) -> str:
    """The page image as a request the server answers.

    For some servers (デジタルアーカイブ福井 among them) the platform records `imageUrl` with a
    width of 0, which the server refuses with HTTP 400. The service base lets `images.fetch` ask
    for the full image instead.
    """
    if _ZERO_SIZE.search(url):
        return images.service_of(url) or url
    return url


def query_marks_as_notes(text: str) -> str:
    """`text` with each question mark of the document text wrapped as a koji note `【？】`.

    A mark already inside a note, a reading or other notation is left alone, and so is a line whose
    markup does not parse, which alignment cannot read either.
    """
    try:
        chars = koji.parse(text).chars
    except (ValueError, RecursionError):
        return text
    starts = sorted(char.start for char in chars if char.text in QUERY_MARKS and char.role in DOCUMENT_ROLES)
    for start in reversed(starts):
        text = f"{text[:start]}【{text[start]}】{text[start + 1:]}"
    return text


def lines_of_page(page: Page, text: str) -> Iterator[Line]:
    """Line records for one kept page."""
    for position, transcriber_text in enumerate(lines_of(text)):
        body = query_marks_as_notes(transcriber_text)
        meta: dict[str, Any] = {
            "source": "honkoku-collection",
            "transcription_index": page.seq - 1,
            "line_position": position,
        }
        if body != transcriber_text:
            meta["transcriber_text"] = transcriber_text
        yield Line(
            id=f"{page.id}:L{position}",
            page_id=page.id,
            seq=position,
            box=None,
            vertical=True,
            text_raw=body,
            text=plain(body),
            match_method=MATCH_METHOD,
            meta=meta,
        )


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
