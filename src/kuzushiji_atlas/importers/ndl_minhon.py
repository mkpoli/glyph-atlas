"""Import the NDL古典籍OCR学習用データセット（みんなで翻刻加工データ）.

The pinned archive holds one JSON per page: `v1/<book>/<image>.json` and
`v2/<project>/<book>/<page>.json`. A JSON is a list of
`{boundingBox: [[x, y] × 4], id, isVertical, text, isTextline, confidence}`, where `isVertical` and
`isTextline` are the strings `"true"` and `"false"`; v2 wraps the list in `{"words": [...]}`. The
metadata CSVs are not part of the archive but sit beside it: `v1_metadata.csv` is comma-separated and
`v2_metadata.csv` tab-separated, and both name the book, the holder and the full-size URL of every
page file.

One page JSON becomes one page, keyed by the file id in its name; its textline rows become lines with
the rectangle around the four points and `koji.parse(text).plain` as the plain text; one book becomes
one document. v1 names its page files after the image (`File ID(NDL)`), so its metadata is joined on
that column; v2 names them after the transcription (`File ID(Minna De Honkoku)`), which for v2 is the
same id. A page JSON without a metadata row still becomes a page, with the image URL unknown.

A line id is `<document id>:<page file id>:<id>` of the upstream row. The upstream `id` is not unique
within a v1 page: 586 pages repeat one, 1,603 times with a different text on the same rectangle and
213 times with the row stated twice. Every row is kept, and the second and later occurrence of an id
takes its number in the file, `<document id>:<page file id>:<id>:<n>`, so that the ids stay unique and
deterministic; `meta.upstream_id` keeps the id without the suffix.

The archive's own numbers are 66,537 v1 rows and 575,095 v2 rows over 32,822 pages, every row a
textline. The release notes state 456,746 v2 rows; the previous release, whose notes match its
archive, holds 285,869, and the 2024 archive adds rows to pages it shares with it, mostly marginal
notes, so the notes' figure is lower than the archive it describes.

One row, `v2/iryotoyojo/8FA527044D5B51FDF2C2AE6F3EA7983F/062.json` id 40 (吐利不已), states four
collinear points, so the rectangle they enclose has no area. Such a line keeps the points in
`meta.points` and its text in `text_raw`, and has a null box, since a box is a rectangle with
positive sides.
"""

from __future__ import annotations

import csv
import io
import itertools
import json
import re
import warnings
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Self
from urllib.parse import urlsplit

from .. import images, koji, rights, tables
from ..schema import Box, Document, Line, Page, Rights

SOURCE = "ndl-minhon-ocr"
PREFIX = "ndl-minhon"
REVISION = "20240207"
MATCH_METHOD = "ndl-minhon-2024-02"
ARCHIVE = f"ndl-minhon-ocrdataset_{REVISION}.zip"
ROOT = f"ndl-minhon-ocrdataset_{REVISION}"
V1_METADATA = "v1_metadata.csv"
V2_METADATA = "v2_metadata.csv"
TEXT_LICENCE = "https://github.com/ndl-lab/ndl-minhon-ocrdataset/blob/main/LICENSE"
IMAGE_LICENCE = "https://dl.ndl.go.jp/ja/iiif_license.html"

#: The directory of the repository that holds the zip, its unpacked tree and the metadata CSVs.
CACHE = Path(__file__).resolve().parents[3] / "cache" / "ndl-minhon"
UNPACKED = Path("unpacked") / ROOT
NDL_HOST = "dl.ndl.go.jp"

_PAGE = re.compile(r"(?:.*/)?(?P<version>v[12])/(?P<rest>.+)\.json$")
_TRUE = frozenset({"true", "1", "yes"})
_FALSE = frozenset({"false", "0", "no"})


class MissingMetadataWarning(UserWarning):
    """A page JSON that no metadata row describes; the page keeps an unknown image URL."""


@dataclass(frozen=True)
class _Page:
    """One page JSON of the archive: the document it belongs to and the file id that names it."""

    version: str
    book: str
    file_id: str
    name: str
    project: str | None = None

    @property
    def document_id(self) -> str:
        return document_id(self.version, self.book, self.project)


@dataclass(frozen=True)
class _Row:
    """One metadata row: the title, the holder and the image of one page file."""

    title: str
    holder: str
    image: str
    github: str
    minna: str


@dataclass
class _Book:
    """The records of one book, with the rows the import could not place."""

    document: Document
    pages: list[Page] = field(default_factory=list)
    lines: list[Line] = field(default_factory=list)
    skipped: int = 0
    unknown: int = 0


class _Pages:
    """The page JSON files of the archive, read from the unpacked tree or from the zip itself."""

    def __init__(self, *, tree: Path | None = None, archive: Path | None = None) -> None:
        self.tree = tree
        self.archive = archive
        self._zip: zipfile.ZipFile | None = None
        self._members: dict[str, str] = {}

    def __enter__(self) -> Self:
        if self.archive is not None:
            self._zip = zipfile.ZipFile(self.archive)
        elif self.tree is None or not self.tree.is_dir():
            raise FileNotFoundError(f"{self.tree}: neither an unpacked archive nor a zip")
        return self

    def __exit__(self, *_: object) -> None:
        if self._zip is not None:
            self._zip.close()

    def entries(self) -> list[_Page]:
        """Every `v1/**.json` and `v2/**.json` of the source, ordered by name."""
        found: dict[str, _Page] = {}
        if self.tree is not None:
            for path in sorted(self.tree.rglob("*.json")):
                self._add(found, path.relative_to(self.tree).as_posix())
        elif self._zip is not None:
            for member in self._zip.namelist():
                self._add(found, member)
        return [found[name] for name in sorted(found)]

    def read(self, page: _Page) -> bytes:
        if self._zip is not None:
            return self._zip.read(self._members[page.name])
        if self.tree is None:
            raise RuntimeError("the source is not open")
        return (self.tree / page.name).read_bytes()

    def _add(self, found: dict[str, _Page], name: str) -> None:
        """Read one path in the archive, whatever directory the zip wraps around it."""
        match = _PAGE.fullmatch(name)
        if match is None:
            return
        version, rest = match.group("version"), match.group("rest")
        parts = rest.split("/")
        if version == "v1" and len(parts) == 2:
            page = _Page(version=version, book=parts[0], file_id=parts[1], name=f"{version}/{rest}.json")
        elif version == "v2" and len(parts) == 3:
            page = _Page(
                version=version, project=parts[0], book=parts[1], file_id=parts[2], name=f"{version}/{rest}.json"
            )
        else:
            return
        found[page.name] = page
        if self._zip is not None:
            self._members[page.name] = name


def document_id(version: str, book: str, project: str | None = None) -> str:
    """The id of a book: `ndl-minhon:v1:<book>` or `ndl-minhon:v2:<project>:<book>`."""
    return f"{PREFIX}:{version}:{project}:{book}" if project else f"{PREFIX}:{version}:{book}"


def import_all(out: Path, *, zip_path: Path | None = None, unpacked: Path | None = None) -> dict[str, int]:
    """Write `documents`, `pages` and `lines` of the dataset into `out` and return the row counts.

    The page JSONs come from `zip_path` when it is given, else from `unpacked` when it is given, else
    from `cache/ndl-minhon/unpacked/<release>` and, when that is absent, from the pinned zip. The two
    metadata CSVs are read from the directory that holds the archive or the tree, else from
    `cache/ndl-minhon`. The returned counts name the tables, the two versions separately, the rows
    that are not textlines and the page files that no metadata row describes.
    """
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    if zip_path is not None:
        reader, directory = _Pages(archive=Path(zip_path)), Path(zip_path).parent
    elif unpacked is not None:
        tree = Path(unpacked)
        reader, directory = _Pages(tree=tree), tree.parent
    elif (CACHE / UNPACKED).is_dir():
        reader, directory = _Pages(tree=CACHE / UNPACKED), CACHE
    else:
        reader, directory = _Pages(archive=CACHE / ARCHIVE), CACHE

    index = {
        "v1": _index("v1", _metadata(directory / V1_METADATA)),
        "v2": _index("v2", _metadata(directory / V2_METADATA)),
    }
    text_rights = rights.resolve(licence="CC-BY-SA-4.0", url=TEXT_LICENCE)
    documents: list[Document] = []
    pages: list[Page] = []
    lines: list[Line] = []
    counts = dict.fromkeys(
        ("v1_documents", "v2_documents", "v1_pages", "v2_pages", "v1_lines", "v2_lines", "skipped", "unknown_url"), 0
    )
    with reader:
        for key, group in itertools.groupby(reader.entries(), key=_book_key):
            book = _book(key, list(group), index[key[0]], reader, text_rights)
            documents.append(book.document)
            pages.extend(book.pages)
            lines.extend(book.lines)
            counts[f"{key[0]}_documents"] += 1
            counts[f"{key[0]}_pages"] += len(book.pages)
            counts[f"{key[0]}_lines"] += len(book.lines)
            counts["skipped"] += book.skipped
            counts["unknown_url"] += book.unknown

    written = {
        "documents": tables.write(out / "documents.parquet", documents, Document),
        "pages": tables.write(out / "pages.parquet", pages, Page),
        "lines": tables.write(out / "lines.parquet", lines, Line),
    }
    written.update(counts)
    return written


def _book_key(page: _Page) -> tuple[str, str | None, str]:
    """The book a page JSON belongs to: its version, its project and its book id."""
    return (page.version, page.project, page.book)


def _book(
    key: tuple[str, str | None, str],
    entries: list[_Page],
    index: dict[tuple[str, str, str], _Row],
    reader: _Pages,
    text_rights: Rights,
) -> _Book:
    """One book: its document, its pages and its lines, in reading order within each page."""
    version, project, book = key
    identifier = document_id(version, book, project)
    holder = title = github = ""
    ndl = False
    pages: list[Page] = []
    lines: list[Line] = []
    skipped = unknown = 0
    for entry in entries:
        row = index.get((project or "", book, entry.file_id))
        if row is None:
            unknown += 1
            warnings.warn(
                f"{entry.name}: no metadata row; the page keeps an unknown image URL",
                MissingMetadataWarning,
                stacklevel=2,
            )
            image, page_meta = "unknown", {"url_unknown": True}
        else:
            title = title or row.title
            holder = holder or row.holder
            github = github or row.github
            image = images.service_of(row.image) or row.image or "unknown"
            ndl = ndl or _is_ndl(row.image)
            page_meta = {"minna_file_id": row.minna} if row.minna and row.minna != entry.file_id else {}
        page = Page(
            id=f"{identifier}:{entry.file_id}",
            document_id=identifier,
            seq=_integer(entry.file_id, len(pages) + 1),
            image=image,
            width=0,
            height=0,
            transcription={"source": SOURCE, "entry": entry.name.removesuffix(".json"), "revision": REVISION},
            meta=page_meta,
        )
        found, missed = _page_lines(page, reader.read(entry))
        pages.append(page)
        lines.extend(found)
        skipped += missed

    image_rights = rights.resolve(holder=holder or None, url=IMAGE_LICENCE if ndl else None)
    document = Document(
        id=identifier,
        title=title or book,
        source_refs={SOURCE: _dataset_ref(version, book, project), "honkoku-data": _honkoku_ref(book, project)},
        holder=image_rights.holder,
        image_rights=image_rights,
        text_rights=text_rights,
        meta={name: value for name, value in (("attribution", holder), ("github", github)) if value},
    )
    return _Book(document=document, pages=pages, lines=lines, skipped=skipped, unknown=unknown)


def _dataset_ref(version: str, book: str, project: str | None) -> str:
    """The path of the book in the archive, which keys the page JSONs."""
    return f"{version}/{project}/{book}" if project else f"{version}/{book}"


def _honkoku_ref(book: str, project: str | None) -> str:
    """The id of the book in みんなで翻刻データ, whose page files the archive was built from."""
    return f"{project}/{book}" if project else book


def _page_lines(page: Page, payload: bytes) -> tuple[list[Line], int]:
    """The lines of one page JSON in reading order, and the number of rows that are not textlines."""
    lines: list[Line] = []
    skipped = 0
    seen: dict[str, int] = {}
    for position, row in enumerate(_rows(payload)):
        if not _flag(row.get("isTextline")):
            skipped += 1
            continue
        ident = str(row.get("id", position + 1))
        seen[ident] = seen.get(ident, 0) + 1
        lines.append(_line(page, row, ident, seen[ident]))
    lines.sort(key=_reading_order)
    for seq, line in enumerate(lines):
        line.seq = seq
    return lines, skipped


def _line(page: Page, row: dict[str, Any], ident: str, occurrence: int) -> Line:
    """One textline row as a line; a repeated upstream id carries its occurrence as a suffix."""
    points = _points(row.get("boundingBox"))
    text = row.get("text")
    text = text if isinstance(text, str) else ""
    return Line(
        id=f"{page.id}:{ident}" if occurrence == 1 else f"{page.id}:{ident}:{occurrence}",
        page_id=page.id,
        seq=0,
        box=_box(points),
        vertical=_flag(row.get("isVertical"), default=True),
        text_raw=text,
        text=koji.parse(text).plain,
        match_method=MATCH_METHOD,
        meta={"points": points, "confidence": row.get("confidence"), "upstream_id": ident},
    )


def _rows(payload: bytes) -> list[dict[str, Any]]:
    """The line rows of one page JSON: v1 lists them, v2 holds them under `words`."""
    document = json.loads(payload)
    rows = document.get("words") if isinstance(document, dict) else document
    if not isinstance(rows, list):
        raise TypeError("a page JSON is neither a list of lines nor an object with `words`")
    return [row for row in rows if isinstance(row, dict)]


def _points(raw: Any) -> list[list[int]]:
    """The corners of a row's box as upstream states them, whole-number pairs kept in order."""
    if not isinstance(raw, list):
        return []
    return [[int(item[0]), int(item[1])] for item in raw if isinstance(item, list) and len(item) >= 2]


def _box(points: list[list[int]]) -> Box | None:
    """The bounding rectangle of the four points, or None when they enclose no area.

    A row states no points, or states four collinear ones: `v2/iryotoyojo/8FA527044D5B51FDF2C2AE6F3EA7983F/062.json`
    id 40 puts both x at 0. Such a line keeps its points in `meta.points` and its text, and carries no
    box, because a box is a rectangle with positive sides.
    """
    if not points:
        return None
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    width, height = max(xs) - min(xs), max(ys) - min(ys)
    if width <= 0 or height <= 0:
        return None
    return Box(x=min(xs), y=min(ys), w=width, h=height)


def _reading_order(line: Line) -> tuple[int, int, int]:
    """Right to left across the columns of a page, and downwards within one column."""
    if line.box is None:
        return (1, 0, 0)
    if line.vertical:
        return (0, -line.box.x, line.box.y)
    return (0, line.box.y, line.box.x)


def _is_ndl(url: str) -> bool:
    """Whether an image is served by the NDL, whose IIIF pages state the licence of the images."""
    host = (urlsplit(url).hostname or "").lower()
    return host == NDL_HOST or host.endswith(f".{NDL_HOST}")


def _integer(text: str, fallback: int) -> int:
    """A file id as a number; an id that is not a number falls back to the position of the page."""
    try:
        return int(text)
    except ValueError:
        return fallback


def _flag(value: Any, *, default: bool = False) -> bool:
    """An upstream flag, which the page JSONs state as the string `"true"` or `"false"`."""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    return default


def _metadata(path: Path) -> list[dict[str, str]]:
    """The rows of a metadata CSV, with its BOM and the spaces around its column names removed.

    v1 names its columns with commas, v2 with tabs, and several v2 headers carry a trailing space.
    """
    text = path.read_text(encoding="utf-8-sig")
    header = text.split("\n", 1)[0]
    delimiter = "\t" if header.count("\t") > header.count(",") else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    reader.fieldnames = [name.strip() for name in reader.fieldnames or []]
    return [{name: (value or "").strip() for name, value in row.items() if name} for row in reader]


def _index(version: str, rows: Iterable[dict[str, str]]) -> dict[tuple[str, str, str], _Row]:
    """The metadata of one version by the page file it describes.

    v1 keys a page on its book and its `File ID(NDL)`, which names the JSON file, because its
    transcription id differs; v2 keys it on the project, the book and `File ID(Minna De Honkoku)`.
    Rows that repeat a key are dropped, which v1 does for 919 pages.
    """
    index: dict[tuple[str, str, str], _Row] = {}
    for row in rows:
        project = row.get("Project ID", "") if version == "v2" else ""
        file_id = row.get("File ID(Minna De Honkoku)", "") if version == "v2" else row.get("File ID(NDL)", "")
        book = row.get("Book ID", "")
        if not book or not file_id:
            continue
        index.setdefault(
            (project, book, file_id),
            _Row(
                title=row.get("Book Name", ""),
                holder=row.get("Attribution") or row.get("attribution") or "",
                image=row.get("Image URL", ""),
                github=row.get("GitHub URL", ""),
                minna=row.get("File ID(Minna De Honkoku)", ""),
            ),
        )
    return index
