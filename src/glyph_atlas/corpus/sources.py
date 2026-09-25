"""Which local corpora can be searched, and what each one carries.

A corpus is a directory laid out the way :mod:`glyph_atlas.tables` writes one
(``documents.parquet``, ``pages.parquet``, ``lines/`` or ``lines.parquet``,
``page_texts.parquet``, ``units.parquet``). Nothing here assumes a corpus is
character-segmented: the broad transcription corpora have no units at all, and that
is exactly why they are worth indexing.

Rights travel with a corpus, not with a hit, so a search result can always say who
holds the image and under what licence it may be shown.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Default location of the shared read-only corpora, relative to the repo root.
DEFAULT_ROOT = "shared-work"

MANIFEST_NAME = "MANIFEST.json"
DOCUMENTS = "documents.parquet"
PAGES = "pages.parquet"
PAGE_TEXTS = "page_texts.parquet"
LINES = "lines.parquet"
LINES_DIR = "lines"
UNITS = "units.parquet"

#: Text columns per table, most authoritative first. The order matters: a line's
#: ``text_raw`` is what the transcriber wrote, ``text`` is the normalised reading.
TEXT_COLUMNS = {
    "lines": ("text_raw", "text"),
    "page_texts": ("text_raw",),
    "pages": ("transcription",),
    "units": ("text_source", "reading", "unicode"),
}


@dataclass
class Corpus:
    """One searchable dataset directory on disk."""

    name: str
    directory: Path
    #: Character-level rows, if the corpus has them.
    has_units: bool = False
    has_lines: bool = False
    has_page_texts: bool = False
    #: Rows per table, read from MANIFEST.json when present.
    counts: dict[str, int] = field(default_factory=dict)
    schema_version: int | None = None
    manifest_command: str | None = None
    #: Why this corpus is not searchable, when it is not.
    problem: str | None = None
    #: Free-form provenance notes for the API surface.
    note: str | None = None

    def table(self, name: str) -> Path | None:
        """The path of a table, accepting both a single file and a shard directory."""
        if name in ("lines", "units"):
            single = self.directory / f"{name}.parquet"
            if single.exists():
                return single
            shard_dir = self.directory / name
            if shard_dir.is_dir() and any(shard_dir.glob("*.parquet")):
                return shard_dir
            return None
        path = self.directory / f"{name}.parquet"
        return path if path.exists() else None

    def parquet_files(self, name: str) -> list[Path]:
        path = self.table(name)
        if path is None:
            return []
        if path.is_dir():
            return sorted(path.glob("*.parquet"))
        return [path]

    @property
    def searchable(self) -> bool:
        return self.problem is None and (self.has_lines or self.has_page_texts or self.has_units)

    @property
    def id_family(self) -> str:
        return ID_FAMILIES.get(self.name, self.name)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.name,
            "id_family": self.id_family,
            "directory": self.directory.name,
            "searchable": self.searchable,
            "has_units": self.has_units,
            "has_lines": self.has_lines,
            "has_page_texts": self.has_page_texts,
            "counts": self.counts,
            "schema_version": self.schema_version,
            "problem": self.problem,
            "note": self.note,
        }


#: Directories that look like a corpus but are *derived* from other corpora: a
#: gallery, a crop set, a merge. Counting them would double-count the same manuscript
#: glyphs and inflate every total, so they are never discovered as sources.
DERIVED_CORPORA = frozenset(
    {
        "ainu-gallery",  # crop gallery derived from the Ainu record imports
        "classifier",  # model artefacts
        "detector",
        "synthetic",  # rendered, not manuscript
    }
)

#: A manifest key that marks a directory as derived, so a future derived set is
#: excluded by declaring itself rather than by being added to the list above.
DERIVED_MARKER = "derived_from"

#: Which page/line numbering convention a corpus publishes under. Two records are only
#: ever merged inside one family. ``ainu-records`` numbers pages from 0 where the
#: Honkoku platform numbers from 1 — a real off-by-one, confirmed by comparing page
#: text — but that is a finding about one pair of corpora, not a general rule, so it
#: is recorded here as a family boundary rather than applied as an arithmetic fix.
ID_FAMILIES = {
    "honkoku-lines": "honkoku-platform",
    "honkoku-data": "honkoku-platform",
    "ndl-minhon": "ndl-minhon",
    "ainu-records": "ainu-records-0based",
    "codh": "codh-omt",
    "codh-full": "codh-omt",
    "kokatsuji": "codh-omt",
    "hilab": "hilab",
    "hng": "hng",
    "hng-kiridashi": "hng-kiridashi",
}

#: Known corpora, with the rights and shape a caller needs before showing a result.
#: ``note`` is what the gallery shows next to a source filter.
KNOWN: tuple[dict[str, Any], ...] = (
    {
        "name": "honkoku-lines",
        "note": "1.17M transcribed lines with per-line image rectangles; the only "
        "broad corpus that carries line geometry, so it is the preferred "
        "source for located occurrences.",
    },
    {
        "name": "honkoku-data",
        "note": "246k page transcriptions from the same platform, no geometry. "
        "Broader than honkoku-lines and reaches documents the line corpus "
        "does not cover; matches are page-level metadata only.",
    },
    {
        "name": "ainu-records",
        "note": "Ainu record pass over a slice of the same platform; lines and "
        "column detections but no per-line rectangles in the lines table.",
    },
    {
        "name": "codh-full",
        "note": "CODH char dataset: 1.08M located character units.",
    },
    {
        "name": "hilab",
        "note": "HI Lab char dataset: 325k located character units.",
    },
    {
        "name": "kokatsuji",
        "note": "Kokatsuji dataset: 36.8k located character units with page images.",
    },
    {
        "name": "hng",
        "note": "HNG char dataset: 49.8k located character crops, no page images.",
    },
    {
        "name": "hng-kiridashi",
        "note": "HNG 切り出しデータ: 10.3k character boxes on Gallica pages of Pelliot chinois 2334 and 2195.",
    },
)


def discover(root: str | Path = DEFAULT_ROOT) -> list[Corpus]:
    """Every corpus directory under `root`, with its shape filled in."""
    base = Path(root)
    found: list[Corpus] = []
    known = {k["name"]: k["note"] for k in KNOWN}
    if not base.is_dir():
        return found
    for child in sorted(p for p in base.iterdir() if p.is_dir()):
        if child.name in DERIVED_CORPORA or _declares_itself_derived(child):
            continue
        directory = child
        if child.name in {"honkoku-data", "wikisource"}:
            source = "honkoku" if child.name == "honkoku-data" else "wikisource"
            current = base / f"{source}-collection" / "current"
            if (current / "MANIFEST.json").is_file():
                directory = current.resolve()
        corpus = _describe(directory, known.get(child.name))
        if corpus is not None:
            corpus.name = child.name
            found.append(corpus)
    return found


def _declares_itself_derived(directory: Path) -> bool:
    """Whether a directory's own manifest says it was derived from other corpora."""
    manifest = directory / MANIFEST_NAME
    if not manifest.is_file():
        return False
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    marker = payload.get(DERIVED_MARKER)
    return bool(marker)


def _describe(directory: Path, note: str | None) -> Corpus | None:
    manifest = directory / MANIFEST_NAME
    counts: dict[str, int] = {}
    version = None
    command = None
    if manifest.exists():
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            counts = {k: int(v) for k, v in (payload.get("tables") or {}).items()}
            version = payload.get("schema_version")
            command = payload.get("command")
        except (ValueError, OSError):
            counts = {}
    has_units = _has(directory, UNITS)
    has_lines = _has(directory, LINES) or _has_dir(directory, LINES_DIR)
    has_page_texts = _has(directory, PAGE_TEXTS)
    if not (has_units or has_lines or has_page_texts or _has(directory, DOCUMENTS)):
        return None
    problem = None
    if not (has_units or has_lines or has_page_texts):
        problem = "no searchable text table (documents/pages only)"
    return Corpus(
        name=directory.name,
        directory=directory,
        has_units=has_units,
        has_lines=has_lines,
        has_page_texts=has_page_texts,
        counts=counts,
        schema_version=version,
        manifest_command=command,
        problem=problem,
        note=note,
    )


def _has(directory: Path, name: str) -> bool:
    return (directory / name).exists()


def _has_dir(directory: Path, name: str) -> bool:
    path = directory / name
    return path.is_dir() and any(path.glob("*.parquet"))


def documents_of(corpus: Corpus) -> dict[str, dict[str, Any]]:
    """Document metadata keyed by id, for joining context onto a hit.

    Read column-projected so the join stays cheap even for a 7.5k-document corpus.
    """
    import pyarrow.parquet as pq

    path = corpus.table("documents")
    if path is None:
        return {}
    wanted = [
        "id",
        "title",
        "holder",
        "shelfmark",
        "production",
        "genre",
        "text_register",
        "dating",
        "image_rights",
        "text_rights",
        "source_refs",
    ]
    out: dict[str, dict[str, Any]] = {}
    for file in [path] if path.is_file() else sorted(path.glob("*.parquet")):
        schema = pq.ParquetFile(file).schema_arrow
        cols = [c for c in wanted if c in schema.names]
        for batch in pq.ParquetFile(file).iter_batches(batch_size=4096, columns=cols):
            data = batch.to_pydict()
            for i in range(batch.num_rows):
                out[data["id"][i]] = {c: _plain(data[c][i]) for c in cols}
    return out


def pages_of(corpus: Corpus) -> dict[str, dict[str, Any]]:
    """Page metadata keyed by id: image service, canvas and page size."""
    import pyarrow.parquet as pq

    path = corpus.table("pages")
    if path is None:
        return {}
    wanted = ["id", "document_id", "seq", "canvas", "image", "width", "height", "transcription"]
    out: dict[str, dict[str, Any]] = {}
    for batch in pq.ParquetFile(path).iter_batches(batch_size=8192, columns=wanted):
        data = batch.to_pydict()
        for i in range(batch.num_rows):
            out[data["id"][i]] = {c: _plain(data[c][i]) for c in wanted}
    return out


def _plain(value: Any) -> Any:
    """Arrow/pydantic scalars to something JSON can carry."""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


#: Tables that hold *text occurrences*: a transcription of a page or a line.
#: ``units`` is deliberately absent — a unit is a located character, retrieved through
#: the glyph path, and walking 1.4M of them to find text occurrences costs a full
#: scan and yields nothing.
TEXT_OCCURRENCE_TABLES = ("lines", "page_texts")


def iter_text_rows(
    corpus: Corpus,
    tables: Sequence[str] = TEXT_OCCURRENCE_TABLES,
) -> Iterator[tuple[str, str, dict[str, Any]]]:
    """Yield ``(table, column, row)`` for every text-bearing row of a corpus.

    Columns are projected so a scan touches the two or three text columns it needs
    rather than whole rows, and batches are streamed so peak memory stays flat.
    """
    import pyarrow.parquet as pq

    for table in tables:
        columns = TEXT_COLUMNS.get(table, ())
        for file in corpus.parquet_files(table):
            schema = pq.ParquetFile(file).schema_arrow
            names = schema.names
            # `box` and `meta` are projected too: without them a line hit cannot
            # carry the rectangle that makes it a located occurrence.
            keep = [
                c for c in ("id", "page_id", "document_id", "seq", "box", "meta", "vertical") if c in names
            ]
            use = keep + [c for c in columns if c in names]
            if len(use) == len(keep):
                continue
            for batch in pq.ParquetFile(file).iter_batches(batch_size=16384, columns=use):
                data = batch.to_pydict()
                for i in range(batch.num_rows):
                    row = {c: data[c][i] for c in use}
                    for column in columns:
                        if column in row and isinstance(row[column], str):
                            yield table, column, row
                            break
