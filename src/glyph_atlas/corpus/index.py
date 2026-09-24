"""A bounded, persistent occurrence index over the local corpora.

What the gallery needs is *"show me every place this character occurs"* without
loading 1.4M units into memory six times or rescanning every Parquet file on each
keypress. So the index is split in two, and only one half is ever built wholesale:

``chars.parquet``
    One row per distinct character seen in a text column, with counts and the corpora
    that hold it. Bounded summary: ~15k rows, about 1 MB. This is what a character
    picker or a find-landing page reads. One full pass builds it; after that it is
    static.

``occ/<codepoint>.parquet``
    Every occurrence of one character, in full detail, built on demand and cached.
    A query for one character scans once and afterwards reads one small file, so a
    kilobyte-scale question never forces a gigabyte-scale index.

Nothing here re-implements the table layer: corpora are read through
:mod:`glyph_atlas.corpus.sources`, so sharding and locking stay the property of
:mod:`glyph_atlas.tables`.
"""

from __future__ import annotations

import json
import re
import time
import unicodedata
from collections import OrderedDict
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from threading import RLock
from typing import Any

import numpy as np

from ..unit_scope import unit_scope
from . import sources as corpus_sources
from .occurrence import (
    TOMO,
    Occurrence,
    Rect,
    Source,
    classify,
    codepoint,
    codepoint_of_occurrence_id,
    find_occurrences,
    normalise_occurrence_id,
    occurrence_id,
)

INDEX_DIR = "corpus-index"
CHARS_FILE = "chars.parquet"
OCC_DIR = "occ"
META_FILE = "index.json"

#: Ceiling on rows written for one character. A common character occurs over a
#: million times; materialising that would cost gigabytes and buy the gallery nothing
#: it can display. The exact total is already known from the character summary, so the
#: index stores a bounded, retrievable head and says so.
DEFAULT_MAX_RECORDS = 200_000

#: Rows written per Parquet row group.
WRITE_BATCH = 4096


#: Registers in the per-character distinct-page sketch. 1024 registers is a 1 KiB
#: fixed cost per character and about 3% standard error, which is the trade that
#: keeps a whole-corpus build inside a few tens of MB instead of holding a page set
#: per character (a common character is on most of 350k pages; a set per character
#: would run to gigabytes).
PAGE_SKETCH_REGISTERS = 1024

#: Columns of the bounded character summary.
CHARS_COLUMNS = (
    "char",
    "codepoint",
    "name",
    "block",
    "n_occurrences",
    "n_literal",
    "n_annotated",
    "n_located",
    "n_line_hits",
    "n_page_hits",
    "n_units",
    "n_pages_approx",
    "n_documents",
    "corpora",
    "sample_documents",
    "in_units",
)


#: Any 〖…：合字〗 / 【…：合字】 marker, whatever character it names.
_ANNOTATION_ANY = re.compile(
    "[\u3016\u3010]\\s*([^\u3017\u3011]{1,3})\\s*[:\uff1a]\\s*\u5408\u5b57\\s*[\u3017\u3011]"
)


def _split_literal_annotated(text: str, char: str) -> tuple[int, int]:
    """How many occurrences of `char` are the text, and how many are the marker."""
    literal = annotated = 0
    for match in re.finditer(re.escape(char), text):
        if classify(text, match.start(), char) == "annotated_ligature":
            annotated += 1
        else:
            literal += 1
    return literal, annotated


def _annotated_chars(text: str) -> frozenset[str]:
    """Characters this text names inside a ligature marker.

    Computed once per row rather than once per character per row: a line has the same
    annotation set for every character in it, and recompiling a pattern tens of
    millions of times is the difference between a one-minute build and an hour.
    """
    if "\u5408\u5b57" not in text:
        return frozenset()
    found: set[str] = set()
    for match in _ANNOTATION_ANY.finditer(text):
        marker = match.group(1).strip()
        if len(marker) == 1:
            found.add(marker)
    return frozenset(found)


def _sketch_add(registers: np.ndarray, value: int) -> None:
    """Add one page id to a fixed-size distinct-count sketch (HyperLogLog style)."""
    h = hash64(value)
    index = h & (PAGE_SKETCH_REGISTERS - 1)
    rest = h >> 10
    rank = 1
    while rest and not (rest & 1):
        rank += 1
        rest >>= 1
    registers[index] = max(registers[index], rank)


def _sketch_count(registers: np.ndarray) -> int:
    """Estimate the distinct count from the sketch registers.

    Small-range correction first (accurate while most registers are still zero),
    then the standard HyperLogLog estimator ``alpha_m * m^2 / sum(2^-M[j])``.
    """
    m = float(PAGE_SKETCH_REGISTERS)
    zeros = int((registers == 0).sum())
    if zeros == m:
        return 0
    if zeros:
        small = m * float(np.log(m / zeros))
        if small <= 2.5 * m:
            return round(small)
    alpha = 0.7213 / (1.0 + 1.079 / m)
    harmonic = float(np.power(2.0, -registers.astype(np.float64)).sum())
    return round(alpha * m * m / harmonic)


def hash64(value: int) -> int:
    """SplitMix64: a stable, dependency-free integer hash."""
    x = (value + 0x9E3779B97F4A7C15) & 0xFFFFFFFFFFFFFFFF
    x = ((x ^ (x >> 30)) * 0xBF58476D1CE4E5B9) & 0xFFFFFFFFFFFFFFFF
    x = ((x ^ (x >> 27)) * 0x94D049BB133111EB) & 0xFFFFFFFFFFFFFFFF
    return x ^ (x >> 31)


@dataclass
class IndexStats:
    """What an index costs, so a caller can decide before building one."""

    root: str
    directory: str
    chars: int = 0
    bytes_on_disk: int = 0
    corpora: list[dict[str, Any]] = field(default_factory=list)
    built_at: str | None = None
    seconds: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "root": self.root,
            "directory": self.directory,
            "chars": self.chars,
            "bytes_on_disk": self.bytes_on_disk,
            "megabytes_on_disk": round(self.bytes_on_disk / 1e6, 2),
            "corpora": self.corpora,
            "built_at": self.built_at,
            "seconds": self.seconds,
            "largest_occurrence_file_bytes": self.largest_occurrence_bytes(),
        }

    def largest_occurrence_bytes(self) -> int:
        """Bytes of the biggest per-character file now on disk.

        Reported instead of an invented resident figure: what a query holds
        depends on the character asked for, and a common character's file is
        much larger than a rare one's, so one number would be a fiction.
        """
        directory = Path(self.directory) / OCC_DIR
        if not directory.is_dir():
            return 0
        return max((p.stat().st_size for p in directory.glob("*.parquet")), default=0)


class CorpusIndex:
    """Read side of the index. Cheap to construct; loads lazily."""

    def __init__(
        self,
        directory: str | Path = INDEX_DIR,
        root: str | Path = corpus_sources.DEFAULT_ROOT,
        *,
        cache_size: int = 8,
        cache_rows: int = 20_000,
    ):
        self.directory = Path(directory)
        self.root = Path(root)
        self.cache_size = cache_size
        # A row ceiling as well as a file count: eight common characters can be far
        # more than eight rare ones, and a browsing session must not grow without
        # bound.
        self.cache_rows = cache_rows
        self._cached_rows = 0
        self._chars: dict[str, dict[str, Any]] | None = None
        self._chars_stamp: tuple[int, int] | None = None
        self._chars_lock = RLock()
        self._glyphs: Any = None
        # Bounded: holding every queried character's occurrences would grow without
        # limit over a browsing session, which is the thing this index exists to avoid.
        self._occ_cache: OrderedDict[str, list[Occurrence]] = OrderedDict()

    # ------------------------------------------------------------- inspection
    @property
    def chars_path(self) -> Path:
        return self.directory / CHARS_FILE

    @property
    def meta_path(self) -> Path:
        return self.directory / META_FILE

    def exists(self) -> bool:
        return self.chars_path.exists()

    def stats(self) -> IndexStats:
        corpora: list[dict[str, Any]] = []
        if self.meta_path.exists():
            try:
                corpora = json.loads(self.meta_path.read_text(encoding="utf-8")).get("corpora", [])
            except (OSError, ValueError):
                corpora = []
        size = sum(p.stat().st_size for p in self.directory.rglob("*") if p.is_file())
        stats = IndexStats(root="", directory=str(self.directory), bytes_on_disk=size, corpora=corpora)
        if self.chars_path.exists():
            import pyarrow.parquet as pq

            stats.chars = pq.ParquetFile(self.chars_path).metadata.num_rows
            stats.built_at = _mtime(self.chars_path)
        return stats

    # ------------------------------------------------------------------ read
    def _load_chars(self) -> dict[str, dict[str, Any]]:
        with self._chars_lock:
            try:
                stat = self.chars_path.stat()
                stamp = (stat.st_mtime_ns, stat.st_size)
            except FileNotFoundError:
                stamp = None
            if stamp != self._chars_stamp:
                self._chars = None
                self._occ_cache.clear()
                self._cached_rows = 0
                self._chars_stamp = stamp
            if self._chars is None:
                self._chars = {}
                if self.chars_path.exists():
                    import pyarrow.parquet as pq
    
                    for batch in pq.ParquetFile(self.chars_path).iter_batches(batch_size=8192):
                        data = batch.to_pydict()
                        for i in range(batch.num_rows):
                            self._chars[data["char"][i]] = {c: data[c][i] for c in data}
            return self._chars
    def has_char(self, char: str) -> bool:
        return char in self._load_chars()

    def summary(self, char: str) -> dict[str, Any] | None:
        return self._load_chars().get(char)

    def summary_by_codepoint(self, value: str) -> dict[str, Any] | None:
        """Look up by ``U+XXXX``, ``𪜈`` or a bare code point."""
        text = value.strip()
        if text in self._load_chars():
            return self._load_chars()[text]
        if len(text) == 1:
            return self._load_chars().get(text)
        import re as _re

        m = _re.fullmatch(r"[Uu]\+([0-9A-Fa-f]{4,6})", text)
        if m:
            try:
                return self._load_chars().get(chr(int(m.group(1), 16)))
            except ValueError:
                return None
        return None

    def by_reading(self, reading: str) -> list[dict[str, Any]]:
        """Characters whose ligature reading starts with `reading` (e.g. トモ -> 𪜈)."""
        from .api import LIGATURE_READINGS

        out = []
        for char, (value, script) in LIGATURE_READINGS.items():
            if value.startswith(reading) or reading.startswith(value):
                row = self.summary(char)
                if row is not None:
                    out.append(row)
        return out

    def characters(self) -> list[dict[str, Any]]:
        return sorted(self._load_chars().values(), key=lambda r: -(r.get("n_occurrences") or 0))

    def occurrences(self, char: str) -> list[Occurrence]:
        """Occurrences of `char` from the per-character file, if it was built."""
        if char not in self._occ_cache:
            rows = _read_occurrences(self.occ_path(char))
            self._attach_glyphs(rows)
            self._occ_cache[char] = rows
            self._cached_rows += len(rows)
            while len(self._occ_cache) > self.cache_size or (
                self._cached_rows > self.cache_rows and len(self._occ_cache) > 1
            ):
                _, evicted = self._occ_cache.popitem(last=False)
                self._cached_rows -= len(evicted)
        else:
            self._occ_cache.move_to_end(char)
        return self._occ_cache[char]

    def occurrences_page(self, char: str, offset: int, limit: int) -> tuple[list[Occurrence], int]:
        """A page of occurrences read straight from Parquet.

        The full list is never materialised for a paged request: batches are streamed
        and skipped, so a common character costs the rows asked for rather than every
        row on disk.
        """
        path = self.occ_path(char)
        if not path.exists():
            return [], 0
        rows: list[Occurrence] = []
        total = 0
        for batch in _iter_occurrences(path):
            for occurrence in batch:
                if total >= offset and len(rows) < limit:
                    rows.append(occurrence)
                total += 1
        self._attach_glyphs(rows)
        return rows, total

    def occ_path(self, char: str) -> Path:
        return self.directory / OCC_DIR / f"{codepoint(char)}.parquet"

    @property
    def glyphs(self):
        """Machine-located glyph rectangles measured on top of text occurrences."""
        if self._glyphs is None:
            from .glyphs import GlyphRegistry

            self._glyphs = GlyphRegistry.load(self.directory / "glyphs.json")
        return self._glyphs

    def _attach_glyphs(self, occurrences: list[Occurrence]) -> None:
        """Attach a glyph rectangle only where the identity key matches exactly.

        The registry is keyed by strict identity, so a rectangle measured on one line
        cannot land on another line of the same page.
        """
        registry = self.glyphs
        if not len(registry):
            return
        for occurrence in occurrences:
            entry = registry.get(occurrence.identity_key)
            if entry is None:
                continue
            occurrence.rects = [r for r in occurrence.rects if r.role != "glyph"]
            occurrence.rects.append(entry.as_rect())
            if entry.review and not occurrence.review_events:
                occurrence.review_events = [dict(e) for e in (entry.review.get("events") or [])]
            if entry.inspection_note:
                occurrence.meta["glyph_inspection_note"] = entry.inspection_note
            if entry.crop_file:
                occurrence.meta["crop_file"] = entry.crop_file
                occurrence.meta["crop_sha256"] = entry.crop_sha256
            occurrence.meta["glyph_rect_origin"] = "machine_located_registry"

    def has_occurrences(self, char: str) -> bool:
        return self.occ_path(char).exists()

    def build_occurrences(
        self,
        char: str,
        root: str | Path | None = None,
        corpora: Sequence[str] | None = None,
        max_records: int = DEFAULT_MAX_RECORDS,
    ) -> dict[str, Any]:
        """Build this character's file on demand, once, under a lock.

        A cold query pays one scan and every later query reads one small file. Without
        this a first-time search for a rare character would come back empty purely
        because nobody had asked for it before.
        """
        target = self.occ_path(char)
        # Per-character lock only: building one character must never block a count, a
        # glyph lookup, or a query for a different character.
        lock = target.with_suffix(".parquet.lock")
        lock.parent.mkdir(parents=True, exist_ok=True)
        with open(lock, "w") as handle:
            try:
                import fcntl

                fcntl.flock(handle, fcntl.LOCK_EX)
                if target.exists():
                    return {
                        "char": char,
                        "codepoint": codepoint(char),
                        "path": str(target),
                        "records": None,
                        "already_built": True,
                        "truncated": None,
                    }
                return build_occurrences(
                    char,
                    root if root is not None else self.root,
                    self.directory,
                    corpora=corpora,
                    max_records=max_records,
                )
            finally:
                try:
                    import fcntl

                    fcntl.flock(handle, fcntl.LOCK_UN)
                except (ImportError, OSError):
                    pass

    def occurrence(self, occurrence_id: str) -> Occurrence | None:
        """One occurrence by id.

        The id carries the code point, so this is one file plus a scan of it rather
        than opening every per-character file to find a single row.
        """
        occurrence_id = normalise_occurrence_id(occurrence_id)
        cp = codepoint_of_occurrence_id(occurrence_id)
        char = _char_of_codepoint(cp) if cp else None
        if char is not None:
            for occ in self.occurrences(char):
                if occ.occurrence_id == occurrence_id:
                    return occ
            return None
        # Legacy or foreign id: fall back to a bounded search of the built files.
        for path in sorted((self.directory / OCC_DIR).glob("*.parquet")):
            for occ in _read_occurrences(path):
                if occ.occurrence_id == occurrence_id:
                    return occ
        return None


# --------------------------------------------------------------------- build
def build_chars(
    root: str | Path = corpus_sources.DEFAULT_ROOT,
    directory: str | Path = INDEX_DIR,
    corpora: Sequence[str] | None = None,
    progress: Any = None,
) -> IndexStats:
    """One full pass over every corpus, writing the bounded character summary.

    Memory is bounded on purpose: per character we keep a bit per document, not a
    set of ids. With ~15k characters and ~12k documents that is a few tens of MB at
    worst, and it stays flat as corpora grow.
    """
    started = time.time()
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    found = corpus_sources.discover(root)
    if corpora:
        wanted = set(corpora)
        found = [c for c in found if c.name in wanted]

    doc_ids: dict[str, int] = {}
    for corpus in found:
        for doc_id in corpus_sources.documents_of(corpus):
            doc_ids.setdefault(doc_id, len(doc_ids))
    n_docs = max(1, len(doc_ids))

    char_index: dict[str, int] = {}
    bitmaps: list[np.ndarray] = []  # one bit per document per character
    counts = np.zeros(0, dtype=np.int64)
    sketches: list[np.ndarray] = []  # fixed-size distinct-page registers
    corpora_seen: list[set[str]] = []
    samples: list[list[str]] = []
    in_units = np.zeros(0, dtype=bool)
    # Evidence split, so a count answers "how many real ligature glyphs" rather than
    # "how many times these two kana happen to sit next to each other". A literal
    # U+2A708 is one character cell; a ト followed by a モ is two, and only the first
    # is a ligature.
    literal = np.zeros(0, dtype=np.int64)
    annotated = np.zeros(0, dtype=np.int64)
    located = np.zeros(0, dtype=np.int64)
    line_hits = np.zeros(0, dtype=np.int64)
    page_hits = np.zeros(0, dtype=np.int64)
    unit_rows = np.zeros(0, dtype=np.int64)

    def slot(char: str) -> int:
        nonlocal counts, bitmaps, in_units, literal, annotated, located
        nonlocal line_hits, page_hits, unit_rows
        i = char_index.get(char)
        if i is None:
            i = len(char_index)
            char_index[char] = i
            bitmaps.append(np.zeros((n_docs + 7) // 8, dtype=np.uint8))
            sketches.append(np.zeros(PAGE_SKETCH_REGISTERS, dtype=np.uint8))
            corpora_seen.append(set())
            samples.append([])
            counts = np.append(counts, 0)
            in_units = np.append(in_units, False)
            literal = np.append(literal, 0)
            annotated = np.append(annotated, 0)
            located = np.append(located, 0)
            line_hits = np.append(line_hits, 0)
            page_hits = np.append(page_hits, 0)
            unit_rows = np.append(unit_rows, 0)
        return i

    page_ids: dict[tuple[str, str], int] = {}
    reports = []
    for corpus in found:
        if not corpus.searchable:
            reports.append({"corpus": corpus.name, "skipped": corpus.problem})
            continue
        docs = corpus_sources.documents_of(corpus)
        seen_rows = 0
        seen_chars = 0
        for table, column, row in corpus_sources.iter_text_rows(
            corpus, corpus_sources.TEXT_OCCURRENCE_TABLES + ("units",)
        ):
            text = row.get(column)
            if not isinstance(text, str) or not text:
                continue
            seen_rows += 1
            doc_id = _doc_of_row(corpus.name, row)
            d = doc_ids.get(doc_id)
            pid = row.get("page_id")
            key = (corpus.name, str(pid)) if pid else None
            if key is not None and key not in page_ids:
                page_ids[key] = len(page_ids)
            annotated_here = _annotated_chars(text)
            has_rect = bool(row.get("box")) and bool(_json(row.get("meta")).get("iiif_region_url"))
            is_unit = table == "units"
            for ch in set(text):
                if ch in ("\n", "\r", "\t", " "):
                    continue
                i = slot(ch)
                n = text.count(ch)
                counts[i] += n
                seen_chars += 1
                if is_unit and len(text) == 1:
                    # Counted only if the row is renderable; the authoritative count is
                    # reconciled against the units tables after the scan.
                    unit_rows[i] += 1
                else:
                    if ch in annotated_here:
                        # Rare: this text carries a ligature marker. Split the count
                        # by span, so a literal glyph and the annotation's own copy of
                        # it in the same line are not both called annotated.
                        lit, ann = _split_literal_annotated(text, ch)
                        literal[i] += lit
                        annotated[i] += ann
                    else:
                        literal[i] += n
                    if has_rect:
                        located[i] += n
                    if table == "lines":
                        line_hits[i] += n
                    else:
                        page_hits[i] += n
                if d is not None:
                    bitmaps[i][d >> 3] |= 1 << (d & 7)
                if key is not None:
                    _sketch_add(sketches[i], page_ids[key])
                corpora_seen[i].add(corpus.name)
                if table == "units":
                    in_units[i] = True
                if d is not None and len(samples[i]) < 24:
                    title = (docs.get(doc_id) or {}).get("title")
                    if title and title not in samples[i]:
                        samples[i].append(title)
            if progress and seen_rows % 200000 == 0:
                progress(f"{corpus.name}: {seen_rows} rows")
        reports.append(
            {"corpus": corpus.name, "rows": seen_rows, "documents": len(docs), "distinct_chars": seen_chars}
        )

    # The authoritative unit count: renderable rows only, keyed by the *written*
    # identity (the `unicode` column), not by whatever text_source happened to hold.
    # This is the same predicate `located_units` applies, so counts match retrieval.
    authoritative_units = _renderable_unit_counts(found)
    for char, i in char_index.items():
        cp = codepoint(char)
        unit_rows[i] = authoritative_units.get(cp, 0)

    rows = []
    for char, i in char_index.items():
        cp = codepoint(char)
        rows.append(
            {
                "char": char,
                "codepoint": cp,
                "name": _name(char),
                "block": _block(char),
                "n_occurrences": int(counts[i]),
                "n_pages_approx": _sketch_count(sketches[i]),
                "n_literal": int(literal[i]),
                "n_annotated": int(annotated[i]),
                "n_located": int(located[i]),
                "n_line_hits": int(line_hits[i]),
                "n_page_hits": int(page_hits[i]),
                "n_units": int(unit_rows[i]),
                "n_documents": int(np.unpackbits(bitmaps[i]).sum()),
                "corpora": ",".join(sorted(corpora_seen[i])),
                "sample_documents": json.dumps(samples[i], ensure_ascii=False),
                "in_units": bool(in_units[i]),
            }
        )
    rows.sort(key=lambda r: (-r["n_occurrences"], r["codepoint"]))

    import pyarrow as pa
    import pyarrow.parquet as pq

    schema = pa.schema(
        [
            (c, pa.bool_() if c in ("in_units",) else pa.int64() if c.startswith("n_") else pa.string())
            for c in CHARS_COLUMNS
        ]
    )
    table = pa.Table.from_pylist(rows, schema=schema)
    # Written to a temporary path and moved into place, so a concurrent /counts request
    # keeps reading the previous complete summary instead of a partial file.
    summary_tmp = directory / (CHARS_FILE + ".part")
    pq.write_table(table, summary_tmp, compression="zstd")
    summary_tmp.replace(directory / CHARS_FILE)

    meta = {
        "root": str(root),
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "seconds": round(time.time() - started, 1),
        "documents": len(doc_ids),
        "pages": len(page_ids),
        "page_counts_are_approximate": True,
        "characters": len(char_index),
        "corpora": reports,
    }
    (directory / META_FILE).write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    (directory / OCC_DIR).mkdir(parents=True, exist_ok=True)
    stats = CorpusIndex(directory).stats()
    stats.seconds = meta["seconds"]
    stats.built_at = meta["built_at"]
    return stats


def build_occurrences(
    char: str,
    root: str | Path = corpus_sources.DEFAULT_ROOT,
    directory: str | Path = INDEX_DIR,
    corpora: Sequence[str] | None = None,
    dedupe: bool = True,
    max_records: int = DEFAULT_MAX_RECORDS,
) -> dict[str, Any]:
    """Stream every occurrence of one character into a bounded per-character file.

    Rows are written as they are scanned, so peak memory depends on the batch size and
    not on how common the character is. The scan stops at ``max_records``; the exact
    total is already recorded in the character summary, so a truncated file is
    reported as truncated rather than mistaken for the whole picture.

    Returns a status dict, not the records: the caller asked for an index, and the
    index is on disk.
    """
    import time as _time

    directory = Path(directory)
    (directory / OCC_DIR).mkdir(parents=True, exist_ok=True)
    found = corpus_sources.discover(root)
    if corpora:
        wanted = set(corpora)
        found = [c for c in found if c.name in wanted]

    target = directory / OCC_DIR / f"{codepoint(char)}.parquet"
    partial = target.with_suffix(".parquet.part")
    writer = _StreamingWriter(partial, max_records)
    started = _time.time()
    scanned = 0
    for corpus in found:
        if not corpus.searchable:
            continue
        for occurrence in _scan_corpus(corpus, char):
            scanned += 1
            writer.add(occurrence)
            if writer.truncated:
                break
        # Arrow's allocator keeps freed buffers for reuse, so a scan across several
        # large corpora accumulates resident memory that is no longer live. Returning
        # it at each corpus boundary keeps the peak a function of the largest single
        # corpus rather than of the whole set.
        _release_arrow_memory()
        if writer.truncated:
            break
    writer.close()
    # Publish atomically: a reader never sees a half-written per-character file.
    if writer.written:
        partial.replace(target)
    elif partial.exists():
        partial.unlink()
    return {
        "char": char,
        "codepoint": codepoint(char),
        "path": str(target),
        "records": writer.written,
        "scanned": scanned,
        "skipped_duplicates": writer.skipped_duplicates,
        "truncated": writer.truncated,
        "max_records": max_records,
        "seconds": round(_time.time() - started, 2),
    }


#: Rows of document/page context to keep resident while joining provenance onto hits.
#: Hits arrive grouped by page, so a small window covers them without holding a whole
#: corpus's metadata as Python dicts — which for 246k pages is hundreds of megabytes.
META_CACHE_ROWS = 512


class _MetaCache:
    """Lazily resolved document/page metadata with a bounded window.

    Reads only the ids actually asked for, in batches. Loading a corpus's whole page
    table up front is what put a query over a gigabyte; a streaming scan touches a
    handful of pages at a time.
    """

    def __init__(self, corpus: corpus_sources.Corpus, capacity: int = META_CACHE_ROWS):
        self.corpus = corpus
        self.capacity = capacity
        self._docs: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._pages: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._only: dict[str, Any] | None = None
        # One dataset per table, built once. Rebuilding it per lookup leaks: each
        # dataset holds open file handles and footer metadata, and a scan of a rare
        # character still performs hundreds of page lookups.
        self._tables: dict[str, tuple[Any, list[str]] | None] = {}

    def _table(self, name: str) -> tuple[Any, list[str]] | None:
        if name in self._tables:
            return self._tables[name]
        self._tables[name] = self._open_table(name)
        return self._tables[name]

    def _open_table(self, name: str) -> tuple[Any, list[str]] | None:
        path = self.corpus.table(name)
        if path is None:
            return None
        files = [path] if path.is_file() else sorted(path.glob("*.parquet"))
        if not files:
            return None
        dataset = _dataset_for(files)
        wanted = {
            "documents": [
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
            ],
            "pages": ["id", "document_id", "seq", "canvas", "image", "width", "height", "transcription"],
        }[name]
        columns = [c for c in wanted if c in dataset.schema.names]
        return dataset, columns

    def document(self, doc_id: str) -> dict[str, Any]:
        if doc_id in self._docs:
            self._docs.move_to_end(doc_id)
            return self._docs[doc_id]
        row = self._fetch("documents", self._docs, doc_id)
        if row:
            return row
        # A page id does not always decompose into the document id: Wikisource pages
        # are `ws:<pageid>` under one document `ws:ja`, so stripping the last segment
        # yields `ws` and no document. When a corpus holds exactly one document, that
        # document is the answer.
        only = self.only_document()
        self._docs[doc_id] = only
        self._evict(self._docs)
        return only

    def only_document(self) -> dict[str, Any]:
        """The corpus's sole document, or empty when it has several.

        Bounded: reads one row, once per corpus.
        """
        if self._only is not None:
            return self._only
        resolved = self._table("documents")
        self._only = {}
        if resolved is not None:
            dataset, columns = resolved
            head = dataset.head(2, columns=columns)
            if head.num_rows == 1:
                self._only = head.to_pylist()[0]
        return self._only

    def page(self, page_id: str | None) -> dict[str, Any]:
        if not page_id:
            return {}
        if page_id in self._pages:
            self._pages.move_to_end(page_id)
            return self._pages[page_id]
        return self._fetch("pages", self._pages, page_id) or {}

    def _fetch(self, name: str, cache: OrderedDict[str, dict[str, Any]], ident: str) -> dict[str, Any] | None:
        import pyarrow.dataset as ds

        resolved = self._table(name)
        if resolved is None:
            cache[ident] = {}
            self._evict(cache)
            return {}
        dataset, columns = resolved
        table = dataset.to_table(columns=columns, filter=ds.field("id") == ident)
        row = table.to_pylist()[0] if table.num_rows else {}
        cache[ident] = row
        self._evict(cache)
        return row

    def _evict(self, cache: OrderedDict[str, dict[str, Any]]) -> None:
        while len(cache) > self.capacity:
            cache.popitem(last=False)


def _release_arrow_memory() -> None:
    """Hand Arrow's unused pooled memory back to the OS."""
    try:
        import pyarrow as pa

        pa.default_memory_pool().release_unused()
    except (ImportError, AttributeError):  # pragma: no cover
        pass


def _dataset_for(files: list[Path]):
    import pyarrow.dataset as ds

    return ds.dataset([str(f) for f in files], format="parquet")


def _scan_corpus(corpus: corpus_sources.Corpus, char: str) -> Iterator[Occurrence]:
    meta = _MetaCache(corpus)
    for table, column, row in corpus_sources.iter_text_rows(corpus):
        text = row.get(column)
        if not isinstance(text, str) or char not in text:
            continue
        yield from _rows_for(corpus, table, column, row, text, char, meta)


def _rows_for(corpus, table, column, row, text, char, context) -> Iterator[Occurrence]:
    """One text row's occurrences, with provenance resolved from `context`.

    `context` is a :class:`_MetaCache`, so document and page metadata are fetched for
    the ids actually touched rather than for the whole corpus.
    """
    page_id = row.get("page_id")
    doc_id = _doc_of_row(corpus.name, row)
    doc = context.document(doc_id)
    page = context.page(str(page_id) if page_id else None)
    meta = _json(row.get("meta"))
    line_id = row.get("id") if table == "lines" else None

    rects: list[Rect] = []
    box = row.get("box")
    if isinstance(box, dict) and all(box.get(k) is not None for k in ("x", "y", "w", "h")):
        rects.append(
            Rect(
                x=int(box["x"]),
                y=int(box["y"]),
                w=int(box["w"]),
                h=int(box["h"]),
                role="line",
                basis="upstream_bbox",
                confirmed=False,
            )
        )
    iiif_region_url = meta.get("iiif_region_url")

    source = Source(
        corpus=corpus.name,
        id_family=corpus.id_family,
        document_id=doc_id,
        page_id=str(page_id) if page_id else None,
        line_id=line_id,
        title=doc.get("title"),
        holder=doc.get("holder"),
        shelfmark=doc.get("shelfmark"),
        production=_enum(doc.get("production")),
        text_register=_enum(doc.get("text_register")),
        dating=doc.get("dating") or [],
        revision=_revision(page, doc),
        source_refs=doc.get("source_refs") or {},
        image=page.get("image"),
        canvas=page.get("canvas"),
        iiif_region_url=iiif_region_url,
        image_rights=doc.get("image_rights"),
        text_rights=doc.get("text_rights"),
    )

    tier = (
        "line_rect"
        if (rects and meta.get("iiif_region_url"))
        else ("line_text" if table == "lines" else "page_text")
    )
    ocr = meta.get("ocr_text")

    for start, end, char_class, snippet in find_occurrences(text, char):
        occurrence = Occurrence(
            occurrence_id=occurrence_id(source, char, start, char_class),
            char=char,
            codepoint=codepoint(char),
            char_class=char_class,
            tier=tier,
            source=source,
            span_start=start,
            span_end=end,
            text_raw=text,
            context=snippet,
            rects=list(rects),
            ocr_text=ocr,
            ocr_agrees_with_ligature=_ocr_agrees(ocr, text, start, char) if ocr else None,
            meta={
                "table": table,
                "column": column,
                "edit_distance": meta.get("edit_distance"),
                "det_score": meta.get("det_score"),
                "line_meta": {
                    k: v
                    for k, v in meta.items()
                    if k
                    in ("iiif_region_url", "ocr_text", "split", "image_on_hf", "det_score", "edit_distance")
                },
            },
        )
        yield occurrence


def _ocr_agrees(ocr: str, text: str, start: int, char: str) -> bool | None:
    """Whether an independent OCR of the same line expands the ligature.

    For a ligature this is the useful corroboration: the transcription writes one
    character, the OCR writes kana for it, and both describe one glyph cell. Note the
    expansion is not always the full トモ — in 山𪜈云 the OCR writes 山ト云, because the
    モ is elided in that construction — so the signal is "the OCR wrote a ト that the
    transcription did not", not the literal string トモ.

    Corroboration, not proof: it is a separate flag and does not promote a hit to a
    located crop.
    """
    if char != TOMO or not ocr:
        return None
    return "ト" in ocr


def _doc_of_row(corpus_name: str, row: dict[str, Any]) -> str:
    """The document a text row belongs to.

    Only the units table carries ``document_id``; lines and page texts identify their
    document through the page id (``hl:<item>:<page>`` / ``hk:<entry>:<page>``), so
    fall back to that rather than losing the join.
    """
    direct = row.get("document_id")
    if direct:
        return str(direct)
    page_id = row.get("page_id")
    if page_id and ":" in str(page_id):
        return str(page_id).rsplit(":", 1)[0]
    return ""


def _revision(page: dict[str, Any], doc: dict[str, Any]) -> str | None:
    value = page.get("transcription")
    if isinstance(value, dict):
        return value.get("revision")
    if isinstance(value, str):
        return _json(value).get("revision")
    return None


def _enum(value: Any) -> str | None:
    if value is None:
        return None
    return getattr(value, "value", None) or str(value)


def _json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            loaded = json.loads(value)
            return loaded if isinstance(loaded, dict) else {}
        except ValueError:
            return {}
    return {}


def _name(char: str) -> str:
    try:
        return unicodedata.name(char)
    except ValueError:
        return ""


@lru_cache(maxsize=1)
def _blocks() -> dict[str, str]:
    """Code point -> Unicode block name, from the project's character table.

    ``unicodedata`` does not expose blocks, and the atlas already ships a curated
    table (``data/vocab/characters.tsv``) built from the Unicode Character Database,
    so read that rather than vendoring a second copy of the block ranges.
    """
    table = Path(__file__).resolve().parents[3] / "data" / "vocab" / "characters.tsv"
    out: dict[str, str] = {}
    if not table.exists():
        return out
    try:
        with open(table, encoding="utf-8") as fh:
            header = fh.readline().rstrip("\n").split("\t")
            if "code_point" not in header or "block" not in header:
                return out
            cp_at, block_at = header.index("code_point"), header.index("block")
            for line in fh:
                parts = line.rstrip("\n").split("\t")
                if len(parts) > max(cp_at, block_at) and parts[block_at]:
                    out[parts[cp_at].upper()] = parts[block_at]
    except OSError:
        return {}
    return out


def _block(char: str) -> str:
    return _blocks().get(codepoint(char), "")


def _mtime(path: Path) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(path.stat().st_mtime))


# -------------------------------------------------------------------- IO
OCC_SCHEMA_COLUMNS = (
    "occurrence_id",
    "char",
    "codepoint",
    "char_class",
    "tier",
    "corpus",
    "id_family",
    "document_id",
    "page_id",
    "line_id",
    "span_start",
    "span_end",
    "text_raw",
    "context",
    "rect_role",
    "rect_basis",
    "rect_x",
    "rect_y",
    "rect_w",
    "rect_h",
    "rect_confirmed",
    "iiif_region_url",
    "ocr_text",
    "ocr_agrees_with_ligature",
    "title",
    "holder",
    "shelfmark",
    "production",
    "text_register",
    "dating",
    "revision",
    "image",
    "canvas",
    "image_rights",
    "text_rights",
    "identity_key",
    "content_key",
    "also_seen_in",
    "meta",
)


#: Columns that are booleans rather than text or counts.
BOOL_COLUMNS = ("rect_confirmed", "ocr_agrees_with_ligature")
INT_COLUMNS = ("span_start", "span_end", "rect_x", "rect_y", "rect_w", "rect_h")


def _arrow_type(column: str) -> Any:
    import pyarrow as pa

    if column in BOOL_COLUMNS:
        return pa.bool_()
    if column in INT_COLUMNS:
        return pa.int64()
    return pa.string()


def _occurrence_row(o: Occurrence) -> dict[str, Any]:
    rect = next((r for r in o.rects if r.role == "line"), None)
    return {
        "occurrence_id": o.occurrence_id,
        "char": o.char,
        "codepoint": o.codepoint,
        "char_class": o.char_class,
        "tier": o.tier,
        "corpus": o.source.corpus,
        "id_family": o.source.id_family,
        "document_id": o.source.document_id,
        "page_id": o.source.page_id,
        "line_id": o.source.line_id,
        "span_start": o.span_start,
        "span_end": o.span_end,
        "text_raw": o.text_raw,
        "context": o.context,
        "rect_role": rect.role if rect else None,
        "rect_basis": rect.basis if rect else None,
        "rect_x": rect.x if rect else None,
        "rect_y": rect.y if rect else None,
        "rect_w": rect.w if rect else None,
        "rect_h": rect.h if rect else None,
        "rect_confirmed": bool(rect.confirmed) if rect else None,
        "iiif_region_url": o.source.iiif_region_url,
        "ocr_text": o.ocr_text,
        "ocr_agrees_with_ligature": o.ocr_agrees_with_ligature,
        "title": o.source.title,
        "holder": o.source.holder,
        "shelfmark": o.source.shelfmark,
        "production": o.source.production,
        "text_register": o.source.text_register,
        "dating": json.dumps(o.source.dating, ensure_ascii=False),
        "revision": o.source.revision,
        "image": o.source.image,
        "canvas": o.source.canvas,
        "image_rights": json.dumps(o.source.image_rights, ensure_ascii=False, default=str),
        "text_rights": json.dumps(o.source.text_rights, ensure_ascii=False, default=str),
        "identity_key": o.identity_key,
        "content_key": o.content_key,
        "also_seen_in": json.dumps(o.meta.get("also_seen_in") or [], ensure_ascii=False),
        "meta": json.dumps(o.meta, ensure_ascii=False, default=str),
    }


class _StreamingWriter:
    """Writes occurrence rows to Parquet as they arrive, under a hard row cap.

    Nothing accumulates: a bounded batch is converted and flushed, so peak memory is
    a function of the batch size rather than of how common the character is. Dedup
    keeps only identity keys, not whole records.
    """

    def __init__(self, path: Path, max_records: int):
        import pyarrow as pa
        import pyarrow.parquet as pq

        self.path = path
        self.max_records = max_records
        self.schema = pa.schema([(c, _arrow_type(c)) for c in OCC_SCHEMA_COLUMNS])
        self._pa = pa
        self._pq = pq
        self._writer = None
        self._buffer: list[dict[str, Any]] = []
        self._seen: set[str] = set()
        self.written = 0
        self.skipped_duplicates = 0
        self.truncated = False

    def add(self, occurrence: Occurrence) -> None:
        if self.written + len(self._buffer) >= self.max_records:
            self.truncated = True
            return
        key = occurrence.identity_key
        if key in self._seen:
            self.skipped_duplicates += 1
            return
        self._seen.add(key)
        self._buffer.append(_occurrence_row(occurrence))
        if len(self._buffer) >= WRITE_BATCH:
            self.flush()

    def flush(self) -> None:
        if not self._buffer:
            return
        table = self._pa.Table.from_pylist(self._buffer, schema=self.schema)
        if self._writer is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._writer = self._pq.ParquetWriter(self.path, self.schema, compression="zstd")
        self._writer.write_table(table)
        self.written += len(self._buffer)
        self._buffer = []

    def close(self) -> None:
        self.flush()
        if self._writer is not None:
            self._writer.close()
            self._writer = None


def _iter_occurrences(path: Path, batch_size: int = 4096) -> Iterator[list[Occurrence]]:
    """Stream occurrences from a per-character file, one batch at a time."""
    import pyarrow.parquet as pq

    if not path.exists():
        return
    for batch in pq.ParquetFile(path).iter_batches(batch_size=batch_size):
        out: list[Occurrence] = []
        for row in batch.to_pylist():
            rects = []
            if row.get("rect_role"):
                rects.append(
                    Rect(
                        x=row["rect_x"],
                        y=row["rect_y"],
                        w=row["rect_w"],
                        h=row["rect_h"],
                        role=row["rect_role"],
                        basis=row["rect_basis"],
                        confirmed=bool(row.get("rect_confirmed")),
                    )
                )
            out.append(
                Occurrence(
                    occurrence_id=row["occurrence_id"],
                    char=row["char"],
                    codepoint=row["codepoint"],
                    char_class=row["char_class"],
                    tier=row["tier"],
                    source=Source(
                        corpus=row["corpus"],
                        # Fall back for files written before the family was
                        # persisted, so an older index still attaches correctly.
                        id_family=row.get("id_family")
                        or corpus_sources.ID_FAMILIES.get(row["corpus"], "unknown"),
                        document_id=row["document_id"],
                        page_id=row["page_id"],
                        line_id=row["line_id"],
                        title=row["title"],
                        holder=row["holder"],
                        shelfmark=row["shelfmark"],
                        production=row["production"],
                        text_register=row["text_register"],
                        dating=_json(row.get("dating")),
                        revision=row["revision"],
                        image=row["image"],
                        canvas=row["canvas"],
                        iiif_region_url=row["iiif_region_url"],
                        image_rights=_json(row.get("image_rights")),
                        text_rights=_json(row.get("text_rights")),
                    ),
                    span_start=row["span_start"],
                    span_end=row["span_end"],
                    text_raw=row["text_raw"] or "",
                    context=row["context"] or "",
                    rects=rects,
                    ocr_text=row["ocr_text"],
                    ocr_agrees_with_ligature=row["ocr_agrees_with_ligature"],
                    meta=_json(row.get("meta")) | {"also_seen_in": _json_list(row.get("also_seen_in"))},
                )
            )
        yield out


def _read_occurrences(path: Path) -> list[Occurrence]:
    return [occurrence for batch in _iter_occurrences(path) for occurrence in batch]


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            loaded = json.loads(value)
            return loaded if isinstance(loaded, list) else []
        except ValueError:
            return []
    return []


def _renderable_unit_row(row: dict[str, Any]) -> bool:
    """Whether a unit row is something a glyph grid could actually show.

    A rectangle is the usual case, but not the only one: the HI Lab corpus publishes
    one pre-cut crop file per character with no coordinates at all, and those are just
    as renderable as a box. Requiring a box would silently drop 325k glyphs. Requiring
    *something* renderable is what keeps the count and the retrieval in step.
    """
    if row.get("active") is False:
        return False
    box = row.get("box")
    if box and box.get("w") and box.get("h"):
        return True
    return bool(row.get("crop"))


def _character_unit_row(row: dict[str, Any]) -> bool:
    """A drawable single-character occurrence; a source block is retained separately."""
    return _renderable_unit_row(row) and not unit_scope(row)["needs_segmentation"]


def _renderable_unit_counts(corpora: Sequence[Any]) -> dict[str, int]:
    """Renderable unit rows per code point: active, and carrying a real box.

    One bounded pass over the units tables, counting only what a glyph grid could
    actually show. Keeping this predicate identical to :func:`located_units` is what
    makes the autocomplete count agree with the gallery.
    """
    import pyarrow.dataset as ds

    counts: dict[str, int] = {}
    for corpus in corpora:
        files = corpus.parquet_files("units")
        if not files:
            continue
        dataset = ds.dataset([str(f) for f in files], format="parquet")
        names = dataset.schema.names
        columns = [c for c in ("unicode", "active", "box", "crop", "granularity",
                              "kind", "text_source", "reading") if c in names]
        if "unicode" not in columns or "box" not in columns:
            continue
        for row in dataset.to_table(columns=columns).to_pylist():
            if not _character_unit_row(row):
                continue
            cp = row.get("unicode")
            if cp:
                counts[cp] = counts.get(cp, 0) + 1
    return counts


def _char_of_codepoint(cp: str | None) -> str | None:
    if not cp:
        return None
    try:
        return chr(int(cp.removeprefix("U+"), 16))
    except (ValueError, IndexError):
        return None


# ------------------------------------------------------------- located units
#: Unit tables that carry a real box, and the corpus that owns each.
UNIT_CORPORA = ("codh-full", "hilab", "kokatsuji", "honkoku-lines", "ainu-records")

#: Rows read per scanner batch. Small enough that a common character's scan stays
#: flat in memory while still amortising the Arrow round trip.
UNIT_SCAN_BATCH = 512


def located_units(
    char: str,
    root: str | Path = corpus_sources.DEFAULT_ROOT,
    corpora: Sequence[str] | None = None,
    *,
    offset: int = 0,
    limit: int = 60,
    labels: dict[str, str] | None = None,
    scope: str | None = None,
    identity_counts: dict[str, int] | None = None,
    visual_group: str | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """Imported character *units* whose glyph is `char`, with real rectangles.

    This is what a glyph grid may render. A unit row comes from a detector/alignment
    pass, so its box is a measured rectangle and its ``unicode`` was assigned by that
    pass; both facts are reported rather than assumed.

    Three properties the pager depends on:

    * **The total is exact for what retrieval can reach.** Every matching row is
      examined, and only *active, renderable* rows are counted — the same predicate
      the window uses — so `total` never promises rows the grid cannot show.
    * **The offset is global.** One counter runs across all corpora, so page two does
      not restart each corpus's numbering.
    * **The order is the scan order and nothing else.** Rows are streamed in corpus,
      then file, then row order — which is stable for a fixed corpus — and are *not*
      re-sorted after slicing. Sorting a page in isolation is what makes page two
      repeat or skip rows.

    Only the rows inside the window are materialised with their provenance joined.
    """
    import pyarrow.dataset as ds

    cp = codepoint(char)
    from .identity import family_of, identity_fields
    family = family_of(cp) if scope else None
    family_points = [member["code_point"] for member in family["members"]] if family else [cp]
    family_chars = [member["char"] for member in family["members"]] if family else [char]
    counts = identity_counts if identity_counts is not None else {}
    for key in ("assigned_count", "unassigned_count", "family_total"):
        counts.setdefault(key, 0)
    wanted = set(corpora) if corpora else set(UNIT_CORPORA)
    found = [c for c in corpus_sources.discover(root) if c.name in wanted]
    rows: list[dict[str, Any]] = []
    total = 0
    for corpus in found:
        files = corpus.parquet_files("units")
        if not files:
            continue
        # Joins stay lazy: a whole page table as Python dicts is what put a glyph
        # query over the memory budget.
        context = _MetaCache(corpus)
        dataset = ds.dataset([str(f) for f in files], format="parquet")
        columns = [
            c
            for c in (
                "id",
                "document_id",
                "page_id",
                "line_id",
                "seq",
                "box",
                "crop",
                "crop_sha256",
                "kind",
                "granularity",
                "text_source",
                "reading",
                "unicode",
                "method",
                "review",
                "active",
                "upstream",
            )
            if c in dataset.schema.names
        ]
        predicate = ds.field("unicode").isin(family_points)
        if labels:
            incoming = [key for key, value in labels.items() if value in family_chars]
            outgoing = [key for key, value in labels.items() if value not in family_chars]
            if incoming:
                predicate = predicate | ds.field("id").isin(incoming)
            if outgoing:
                predicate = predicate & ~ds.field("id").isin(outgoing)
        scanner = dataset.scanner(columns=columns, filter=predicate, batch_size=UNIT_SCAN_BATCH)
        for batch in scanner.to_batches():
            for row in batch.to_pylist():
                if not _character_unit_row(row):
                    continue
                identity = identity_fields(row, corpus.name, human_character=(labels or {}).get(row["id"])) if scope else None
                if identity:
                    group = identity.get("visual_group") or {}
                    written = identity["written_character"]
                    if written is not None and written not in family_chars:
                        continue
                    counts["family_total"] += 1
                    counts["assigned_count" if written else "unassigned_count"] += 1
                    if visual_group == "unassigned" and written is not None:
                        continue
                    if visual_group and visual_group != "unassigned" and group.get("id") != visual_group:
                        continue
                    if scope == "character" and written != char:
                        continue
                index = total
                total += 1
                if index < offset or len(rows) >= limit:
                    continue
                shown = identity["written_character"] or _char_of_codepoint(identity["source_code_point"]) or identity["source_label"] if identity else char
                result = _unit_row(corpus, context, row, shown, codepoint(shown) if shown and len(shown) == 1 else cp)
                if identity:
                    result.update(identity)
                rows.append(result)
    return rows, total


def _unit_row(
    corpus: corpus_sources.Corpus, context: _MetaCache, row: dict[str, Any], char: str, cp: str
) -> dict[str, Any]:
    """One grid-safe unit row, with its provenance joined in."""
    from .identity import production_fields
    doc_id = row.get("document_id") or ""
    page_id = row.get("page_id") or ""
    doc = context.document(doc_id)
    page = context.page(page_id)
    rights = doc.get("image_rights") or {}
    if isinstance(rights, str):
        rights = _json(rights)
    upstream = row.get("upstream") or {}
    if isinstance(upstream, str):
        upstream = _json(upstream)
    box = row.get("box")
    box = {k: box[k] for k in ("x", "y", "w", "h")} if box and box.get("w") and box.get("h") else None
    return {
        "unit_id": row.get("id"),
        "char": char,
        "codepoint": cp,
        "source_code_point": row.get("unicode"),
        "source_label": row.get("text_source"),
        "corpus": corpus.name,
        "document_id": doc_id,
        "page_id": page_id,
        "line_id": row.get("line_id"),
        "seq": row.get("seq"),
        "kind": row.get("kind"),
        **unit_scope(row),
        "text_source": row.get("text_source"),
        "reading": row.get("reading"),
        "box": box,
        "crop": row.get("crop"),
        "crop_sha256": row.get("crop_sha256"),
        "method": row.get("method"),
        "review": row.get("review"),
        "title": doc.get("title"),
        "holder": doc.get("holder"),
        **production_fields(doc),
        "shelfmark": doc.get("shelfmark"),
        "image": page.get("image"),
        "canvas": page.get("canvas"),
        "image_service": _service_base(page.get("image")),
        "record_url": upstream.get("url"),
        "image_licence": (rights or {}).get("licence"),
        "image_rights": rights,
        "grid_safe": True,
        "note": (
            "detector/alignment unit with a measured rectangle"
            if box
            else "pre-cut character crop published without coordinates"
        ),
    }


def _service_base(image: Any) -> str | None:
    """The IIIF service base of a page image, when it has one."""
    if not isinstance(image, str) or not image:
        return None
    try:
        from ..images import service_of
    except ImportError:  # pragma: no cover
        return None
    return service_of(image)


#: Corpora a homepage sample may draw from. Ainu is excluded so the gallery is not
#: dominated by one pilot, and every corpus here publishes located character units.
SAMPLE_CORPORA = ("codh-full", "hilab", "kokatsuji", "honkoku-lines")

#: How many rows to read per corpus per sample build. Bounded on purpose: a homepage
#: must not scan a million-row table.
SAMPLE_ROWS_PER_CORPUS = 4000

SAMPLE_FILE = "sample-units.json"
SAMPLE_VERSION = 2


def sample_units(
    root: str | Path = corpus_sources.DEFAULT_ROOT,
    directory: str | Path = INDEX_DIR,
    *,
    limit: int = 120,
    seed: int = 20260101,
    corpora: Sequence[str] | None = None,
    rebuild: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """A bounded, cached random sample of located units for a homepage gallery.

    Reads a few *random row groups* from each corpus rather than the whole table, then
    samples within them. The result is cached next to the index, so a homepage load
    reads one small JSON file instead of touching Parquet at all.

    Every returned row has a real box (``grid_safe``); page and line rectangles are
    never included, so nothing here can leak a whole line into a glyph grid.
    """
    import random

    directory = Path(directory)
    cache = directory / SAMPLE_FILE
    if cache.exists() and not rebuild:
        payload = json.loads(cache.read_text(encoding="utf-8"))
        if payload.get("version") == SAMPLE_VERSION and payload.get("corpora") == sorted(corpora or SAMPLE_CORPORA):
            return [r for r in payload.get("items", []) if _character_unit_row(r)][:limit], payload.get("meta", {})

    import pyarrow.parquet as pq

    wanted = set(corpora) if corpora else set(SAMPLE_CORPORA)
    rng = random.Random(seed)
    rows: list[dict[str, Any]] = []
    per_corpus: dict[str, int] = {}
    for corpus in corpus_sources.discover(root):
        if corpus.name not in wanted:
            continue
        files = corpus.parquet_files("units")
        if not files:
            continue
        context = _MetaCache(corpus)
        for file in files:
            handle = pq.ParquetFile(file)
            n_groups = handle.metadata.num_row_groups
            if not n_groups:
                continue
            picks = rng.sample(range(n_groups), min(3, n_groups))
            for index in picks:
                table = handle.read_row_group(index)
                take = min(SAMPLE_ROWS_PER_CORPUS // max(1, len(picks)), table.num_rows)
                if take <= 0:
                    continue
                for row in table.slice(0, take).to_pylist():
                    if not _character_unit_row(row):
                        continue  # nothing to render means nothing to show
                    box = row.get("box")
                    box = (
                        {k: box[k] for k in ("x", "y", "w", "h")}
                        if box and box.get("w") and box.get("h")
                        else None
                    )
                    doc_id = row.get("document_id") or ""
                    page_id = row.get("page_id") or ""
                    doc = context.document(doc_id)
                    page = context.page(page_id)
                    rights = doc.get("image_rights") or {}
                    if isinstance(rights, str):
                        rights = _json(rights)
                    rows.append(
                        {
                            "unit_id": row.get("id"),
                            "char": _char_of_codepoint(row.get("unicode") or "") or row.get("text_source") or row.get("reading"),
                            "codepoint": row.get("unicode"),
                            "corpus": corpus.name,
                            "document_id": doc_id,
                            "page_id": page_id,
                            "kind": row.get("kind"),
                            **unit_scope(row),
                            "box": box,
                            "crop": row.get("crop"),
                            "crop_sha256": row.get("crop_sha256"),
                            "method": row.get("method"),
                            "review": row.get("review"),
                            "title": doc.get("title"),
                            "holder": doc.get("holder"),
                            "image": page.get("image"),
                            "image_service": _service_base(page.get("image")),
                            "image_licence": rights.get("licence"),
                            "grid_safe": True,
                            "render_available": True,
                            "source_kind": "imported_unit",
                            "note": (
                                "detector/alignment unit with a measured rectangle"
                                if box
                                else "pre-cut character crop, no coordinates"
                            ),
                        }
                    )
        per_corpus[corpus.name] = sum(1 for r in rows if r["corpus"] == corpus.name)
    rng.shuffle(rows)
    items = rows[: max(limit, 1)]
    meta = {
        "seed": seed,
        "sampled_rows": len(rows),
        "per_corpus": per_corpus,
        "corpora": sorted(per_corpus),
        "bounded_rows_per_corpus": SAMPLE_ROWS_PER_CORPUS,
        "note": "random row groups only; the full corpus is never scanned",
    }
    cache.parent.mkdir(parents=True, exist_ok=True)
    from uuid import uuid4
    temporary = cache.with_name(cache.name + "." + uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps({"version": SAMPLE_VERSION, "corpora": sorted(wanted), "items": items, "meta": meta}, ensure_ascii=False), encoding="utf-8")
        temporary.replace(cache)
    finally:
        temporary.unlink(missing_ok=True)
    return items, meta
