"""Page coverage of the みんなで翻刻 entries across the transcription and line datasets.

`build` reads the documents, pages, texts and lines of the dataset directories it is given, groups
them by the みんなで翻刻 entry id and writes one row per entry with the number of pages that carry a
transcription and the number of pages that carry lines from each line dataset. A page counts as
carrying a transcription when its `page_texts` row is not blank, and for a line dataset when a line
record names it; the two numbers beside `pages_with_text` say which dataset covers how much of the
entry.

The entry id is the みんなで翻刻 id: `hk:<entry>` documents (`work/honkoku-data`) hold the
transcriptions, `hl:<entry>` documents (`work/honkoku-lines`) and `ndl-minhon:` documents
(`work/ndl-minhon`) hold lines. A document may also state the entry in `source_refs`, which decides
first. Ids compare without case, since the upstreams spell them in both cases, and the entry is
written as the transcription dataset spells it.

Pages are compared by the number they carry within their entry, `page.seq`: the platform numbers the
canvases of an entry from one and so does 日本古典籍OCR学習用データセット, while Honkoku-Lines numbers
its pages from zero (`image_index`), which `build` adds one to.

`pages_with_text_no_lines` counts the pages of an entry that a transcription covers and no line
dataset does, and it counts two different situations together: a page of an entry that the line
datasets carry but do not reach, and every text page of an entry that no line dataset carries at
all. The counts `pages_with_text_no_lines_shared` and `entries_with_text_without_lines` separate
them, and the two line columns of a row with no lines at all read 0, which is what an entry no line
dataset carries looks like.
"""

from __future__ import annotations

import warnings
from collections import defaultdict
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pyarrow.parquet as pq

from . import tables
from .schema import Document

#: The document id prefixes that name a みんなで翻刻 entry, and the column each dataset counts into.
TEXT_PREFIX = "hk:"
LINE_COLUMNS = {
    "hl:": "pages_with_lines_honkoku_lines",
    "ndl-minhon:": "pages_with_lines_ndl_minhon",
}
PREFIXES = (TEXT_PREFIX, *LINE_COLUMNS)
#: `source_refs` keys that hold the entry id, most specific first.
ENTRY_KEYS = ("honkoku-data", "honkoku", "honkoku-entry", "minna-de-honkoku", "entry")
#: The prefixes whose page numbers start at zero.
ZERO_BASED = frozenset({"hl:"})
#: The columns of `work/coverage.tsv`, preceded by the lines that say what the counts mean.
COLUMNS = ("entry", "pages_with_text", *LINE_COLUMNS.values(), "pages_with_text_no_lines")
NOTES = (
    "# Page coverage of the みんなで翻刻 entries, one row per entry id: pages_with_text counts the",
    "# pages whose transcription is not blank, and the two columns beside it the pages each line",
    "# dataset names. pages_with_text_no_lines counts the pages of the entry that no line dataset",
    "# names, which includes every text page of an entry that no line dataset carries at all; those",
    "# entries are the rows whose two line columns are both 0.",
)


def build(dirs: list[Path], out: Path) -> dict[str, int]:
    """Join the datasets in `dirs` on the みんなで翻刻 entry id and write `out`.

    A directory that does not exist is skipped with a warning; one row per entry is written with the
    counts of the `COLUMNS`, behind the lines of `NOTES` that say what they mean. The returned
    mapping holds the totals: the three page counts, `pages_with_text_no_lines` and its split into
    `pages_with_text_no_lines_shared` (pages of an entry a line dataset carries and does not reach)
    and `entries_with_text_without_lines` (entries that carry text and that no line dataset carries).
    """
    text_pages: dict[str, set[Any]] = defaultdict(set)
    line_pages: dict[str, dict[str, set[Any]]] = {column: defaultdict(set) for column in LINE_COLUMNS.values()}
    entries: set[str] = set()
    spelled: dict[str, str] = {}
    read = skipped = orphans = 0

    for directory in dirs:
        path = Path(directory)
        if not path.exists():
            warnings.warn(f"{path}: no such dataset; skipped", RuntimeWarning, stacklevel=2)
            continue
        dataset = tables.Dataset(path)
        if dataset.tables["documents"] is None:
            raise FileNotFoundError(f"{path}: no documents table")
        read += 1
        kinds: dict[str, tuple[str, str]] = {}
        column: str | None = None
        for document in dataset.read("documents"):
            prefix = _prefix(document.id)
            entry = _entry_of(document) if prefix is not None else None
            if prefix is None or entry is None:
                skipped += 1
                continue
            key = entry.casefold()
            kinds[document.id] = (prefix, key)
            entries.add(key)
            column = LINE_COLUMNS.get(prefix, column)
            if prefix == TEXT_PREFIX or key not in spelled:
                spelled[key] = entry
        if not kinds:
            continue
        pages = dataset.read("pages") if dataset.tables["pages"] is not None else []
        numbers: dict[str, tuple[str, int]] = {}
        for page in pages:
            known = kinds.get(page.document_id)
            if known is None:
                continue
            prefix, key = known
            numbers[page.id] = (key, _ordinal(prefix, page.seq))
        if column is None:
            if dataset.tables["page_texts"] is None:
                raise FileNotFoundError(f"{path}: no page_texts table")
            for batch in dataset.scan("page_texts", columns=["page_id", "source", "text_raw"]):
                for row in batch:
                    known = numbers.get(row.page_id)
                    if known is not None and row.text_raw.strip():
                        text_pages[known[0]].add(known[1])
        if column is None or dataset.tables["lines"] is None:
            continue
        for page_id in _column(dataset.tables["lines"], "page_id"):
            known = numbers.get(page_id)
            if known is None:
                orphans += 1
                continue
            line_pages[column][known[0]].add(known[1])

    rows = []
    shared_uncovered = unshared_entries = 0
    for key in sorted(entries):
        entry = spelled.get(key, key)
        text = text_pages.get(key, set())
        covered: set[Any] = set()
        counts = []
        for column in LINE_COLUMNS.values():
            found = line_pages[column].get(key, set())
            counts.append(len(found))
            covered |= found
        uncovered = text - covered
        if covered:
            shared_uncovered += len(uncovered)
        elif text:
            unshared_entries += 1
        rows.append((entry, len(text), *counts, len(uncovered)))

    lines = [*NOTES, "\t".join(COLUMNS)]
    lines.extend("\t".join(str(value) for value in row) for row in rows)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")

    totals = {column: sum(row[position + 1] for row in rows) for position, column in enumerate(COLUMNS[1:-1])}
    if orphans:
        warnings.warn(f"{orphans} lines name a page outside the pages tables; not counted", RuntimeWarning, stacklevel=2)
    return {
        "datasets": read,
        "entries": len(rows),
        "documents_skipped": skipped,
        "lines_without_pages": orphans,
        **totals,
        "entries_with_text_without_lines": unshared_entries,
        "pages_with_text_no_lines_shared": shared_uncovered,
        "pages_with_text_no_lines": sum(row[-1] for row in rows),
    }


def _prefix(document_id: str) -> str | None:
    """The みんなで翻刻 prefix of a document id, or None for a dataset this command does not join."""
    for prefix in PREFIXES:
        if document_id.startswith(prefix):
            return prefix
    return None


def _entry_of(document: Document) -> str | None:
    """The entry id of a document: what its `source_refs` state, otherwise the tail of its id."""
    for field in ENTRY_KEYS:
        value = document.source_refs.get(field)
        if isinstance(value, str) and value.strip():
            return _entry_id(value)
    return _entry_id(document.id.rsplit(":", 1)[-1])


def _entry_id(value: str) -> str | None:
    """The entry id a reference names.

    The line datasets write it as the path the entry has in the みんなで翻刻データ repository
    (`aizuda2022/1305651A43E0A1886FA934F6C8F33761`), which is the last segment of the reference.
    """
    text = value.strip()
    if not text:
        return None
    if "://" not in text:
        return text.rsplit("/", 1)[-1].strip() or None
    parts = [part for part in urlsplit(text).path.split("/") if part]
    for marker in ("transcription", "entries"):
        if marker in parts and parts.index(marker) + 1 < len(parts):
            return parts[parts.index(marker) + 1]
    return parts[-1] if parts else None


def _ordinal(prefix: str, seq: int) -> int:
    """The number of a page within its entry, counting the canvases of an entry from one."""
    return seq + 1 if prefix in ZERO_BASED else seq


def _column(path: Path, name: str) -> Iterator[str]:
    """One string column of a table, read without building the records it belongs to."""
    for file in sorted(path.glob("*.parquet")) if path.is_dir() else [path]:
        for batch in pq.ParquetFile(file).iter_batches(columns=[name], batch_size=1 << 16):
            for value in batch.column(name).to_pylist():
                if isinstance(value, str):
                    yield value
