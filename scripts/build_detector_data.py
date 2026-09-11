"""Build the character detector's training data from the CODH tables.

Every page is cut into 1024x1024 tiles with 128 px of overlap, the strip past the right and bottom
edge padded with white, and a source box is placed in every tile it overlaps, clipped to the tile.
The clipped part is dropped when it is under 40% of the source box's area. A box wider or taller
than one tile is kept once, in the tile that holds its centre, because clipping it to every tile it
crosses would train the detector on fragments it never sees whole. A unit with `kind=unreadable`
becomes an ignore region (COCO `iscrowd=1`), which teaches the detector to leave unreadable paper
alone without counting it as a character. Tiles that hold no annotation at all are kept up to 5% of
a split's tiles, taken at an even stride over the whole corpus, so the negatives are blank paper
from every book rather than the margin of one.

The output is one COCO document per split under `work/detector/`. An image entry names the tile's
origin on its page, the page it was cut from and the checksum-addressed cache file or staged file
that holds that page. With `--materialise` the tiles are written as JPEGs under
`work/detector/tiles/`, named `<page id with ':' replaced by '_'>_<tile x>_<tile y>.jpg`, and the
entry also names the tile file, so a training run reads one small image instead of decoding a
multi-megabyte page for every epoch. Without `--materialise` the origin is enough for the training
run to cut the tile from the cached page.

The split is by book and comes from `data/splits/codh.tsv`; every tile of a book goes to the split
that file states, so a hand, a block and a scan stay on one side of the split. Nothing in the output
depends on the order the tables are read in: the documents, the tiles and the annotations are
written in page order, and two runs over the same tables produce byte-identical JSON.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import statistics
import sys
import time
from bisect import bisect_right
from collections import Counter, defaultdict
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from PIL import Image

from kuzushiji_atlas import images, tables
from kuzushiji_atlas.schema import Box, Document, Unit

ROOT = Path(__file__).resolve().parents[1]

#: Tile geometry, the same one `kuzushiji_atlas.detect` cuts pages with at inference time.
TILE = 1024
OVERLAP = 128
STRIDE = TILE - OVERLAP
PAD = 255

#: Share of a source box's area a clipped part has to keep to be an annotation of its tile.
MIN_COVERAGE = 0.4
#: Most of a split's tiles that may hold no annotation, as a share of the split's tiles.
EMPTY_FRACTION = 0.05
#: JPEG quality of a materialised tile.
QUALITY = 90
#: The one class the detector is trained for.
CATEGORY = "character"
#: Unit kinds that mark a place where no character is to be detected: unreadable paper, and a gap
#: the transcription records where no character is written.
IGNORE_KINDS = frozenset({"unreadable", "gap"})
SPLITS = ("train", "val", "test")
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png")
#: Characters kept as they are in a tile file name; the rest, ':' included, become '_'.
UNSAFE = re.compile(r"[^A-Za-z0-9._-]")

#: One annotation of a tile: unit id, unit kind, tile-local box, iscrowd, coverage, clipped.
Annotation = tuple[str, str, int, int, int, int, int, float, bool]


# --- geometry ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Tile:
    """The origin of one tile on its page, top-left, in page pixels."""

    x: int
    y: int


@dataclass(frozen=True)
class Placement:
    """A source box inside one tile: the part that lies in it, and how much of the box that is."""

    tile: Tile
    box: Box
    coverage: float
    clipped: bool


def tile_origins(length: int, tile: int = TILE, overlap: int = OVERLAP) -> list[int]:
    """The origins along one axis of the tiles that cover `length` pixels.

    The first tile starts at 0 and each following tile starts one stride further on, so the tiles
    overlap by `overlap` and the last one reaches the end of the page. A length at or under one tile
    gives the single origin 0.
    """
    if length <= 0:
        raise ValueError(f"length must be positive, got {length}")
    stride = tile - overlap
    if stride <= 0:
        raise ValueError(f"overlap {overlap} must be smaller than the tile size {tile}")
    if length <= tile:
        return [0]
    return [index * stride for index in range(math.ceil((length - overlap) / stride))]


def tile_grid(width: int, height: int, tile: int = TILE, overlap: int = OVERLAP) -> list[Tile]:
    """Every tile of a `width` x `height` page, in reading order."""
    if width <= 0 or height <= 0:
        raise ValueError(f"page must have a positive size, got {width}x{height}")
    return [Tile(x, y) for y in tile_origins(height, tile, overlap) for x in tile_origins(width, tile, overlap)]


def place_box(
    box: Box,
    width: int,
    height: int,
    *,
    tile: int = TILE,
    overlap: int = OVERLAP,
    min_coverage: float = MIN_COVERAGE,
) -> list[Placement]:
    """Every tile that holds an annotation of `box`, with the part of the box that lies in it.

    A box that fits in one tile goes to every tile it overlaps with at least one pixel, and a part
    that keeps under `min_coverage` of the box's area is dropped. A box wider or taller than one tile
    goes to the single tile that holds its centre, clipped to that tile whatever is left of it.
    """
    source = _clamp(box, width, height)
    if source is None:
        return []
    xs, ys = tile_origins(width, tile, overlap), tile_origins(height, tile, overlap)
    area = source.w * source.h
    if source.w > tile or source.h > tile:
        centre = Tile(_holding(xs, source.x + source.w // 2), _holding(ys, source.y + source.h // 2))
        part = _clip(source, centre, tile)
        if part is None:
            return []
        return [Placement(centre, part, round(part.w * part.h / area, 4), part.w != source.w or part.h != source.h)]
    found: list[Placement] = []
    for y in ys:
        if y >= source.y + source.h or y + tile <= source.y:
            continue
        for x in xs:
            if x >= source.x + source.w or x + tile <= source.x:
                continue
            part = _clip(source, Tile(x, y), tile)
            if part is None:
                continue
            coverage = round(part.w * part.h / area, 4)
            if coverage < min_coverage:
                continue
            found.append(Placement(Tile(x, y), part, coverage, part.w != source.w or part.h != source.h))
    return found


def _holding(origins: Sequence[int], value: int) -> int:
    """The origin of the tile that holds `value`: the last origin at or before it."""
    return origins[max(0, bisect_right(origins, value) - 1)]


def overlapped_tiles(
    box: Box,
    width: int,
    height: int,
    *,
    tile: int = TILE,
    overlap: int = OVERLAP,
) -> int:
    """How many tiles a source box touches, which is how many parts `place_box` may drop.

    A box larger than one tile is counted once: it is an annotation of the single tile holding its
    centre, whatever share of it lies there.
    """
    source = _clamp(box, width, height)
    if source is None:
        return 0
    if source.w > tile or source.h > tile:
        return 1
    xs, ys = tile_origins(width, tile, overlap), tile_origins(height, tile, overlap)
    return sum(
        1
        for y in ys
        if source.y < y + tile and y < source.y + source.h
        for x in xs
        if source.x < x + tile and x < source.x + source.w
    )


def _clamp(box: Box, width: int, height: int) -> Box | None:
    """`box` inside a `width` x `height` page, or None when nothing of it is on the page."""
    left, top = max(0, box.x), max(0, box.y)
    right, bottom = min(width, box.x + box.w), min(height, box.y + box.h)
    if right <= left or bottom <= top:
        return None
    return Box(x=left, y=top, w=right - left, h=bottom - top)


def _clip(source: Box, tile: Tile, size: int) -> Box | None:
    """`source` in the coordinates of `tile`, or None when the two do not share a pixel."""
    left, top = max(source.x, tile.x), max(source.y, tile.y)
    right, bottom = min(source.x + source.w, tile.x + size), min(source.y + source.h, tile.y + size)
    if right <= left or bottom <= top:
        return None
    return Box(x=left - tile.x, y=top - tile.y, w=right - left, h=bottom - top)


def empty_tile_limit(annotated: int, *, fraction: float = EMPTY_FRACTION) -> int:
    """The most tiles without an annotation a split of `annotated` annotated tiles may keep.

    The largest `empty` with `empty / (annotated + empty) <= fraction`, so that the share holds of
    the split's tiles as written and not only of the annotated ones.
    """
    if annotated <= 0 or fraction <= 0:
        return 0
    if fraction >= 1:
        return annotated
    return int(fraction * annotated / (1 - fraction))


def spread_indices(total: int, keep: int) -> list[int]:
    """`keep` indexes spread at an even stride over `total`, increasing and without repetition."""
    if keep <= 0 or total <= 0:
        return []
    if keep >= total:
        return list(range(total))
    return [index * total // keep for index in range(keep)]


def tile_name(page_id: str, x: int, y: int) -> str:
    """The file name of a materialised tile: the page id, the tile origin, `.jpg`."""
    return f"{UNSAFE.sub('_', page_id)}_{x}_{y}.jpg"


# --- split and input ---------------------------------------------------------------


@dataclass(frozen=True)
class Book:
    """One row of the split table: the book, how it was produced and the split it belongs to."""

    bid: str
    title: str
    production: str
    split: str


def read_splits(path: Path) -> dict[str, Book]:
    """The split table as `bid` to book. Comment lines and a blank line are skipped."""
    books: dict[str, Book] = {}
    header: list[str] | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        fields = line.rstrip("\n").split("\t")
        if header is None:
            header = fields
            missing = {"bid", "production", "split"} - set(header)
            if missing:
                raise ValueError(f"{path}: the header names no {', '.join(sorted(missing))}")
            continue
        row = dict(zip(header, fields, strict=False))
        split = row["split"]
        if split not in SPLITS:
            raise ValueError(f"{path}: {row['bid']} has split {split!r}, expected one of {', '.join(SPLITS)}")
        books[row["bid"]] = Book(
            bid=row["bid"],
            title=row.get("title", ""),
            production=row.get("production", "unknown"),
            split=split,
        )
    if not books:
        raise ValueError(f"{path}: no rows")
    return books


def book_id(document: Document) -> str:
    """The upstream book id of a document: the CODH source reference, or the id after `codh:`."""
    for key in ("codh-char-shape", "nijl-bid"):
        value = document.source_refs.get(key)
        if value:
            return str(value)
    return document.id.split(":", 1)[1] if document.id.startswith("codh:") else document.id


def cache_file(cache: Path, sha256: str) -> Path | None:
    """The file of a cache row, found by checksum: the extension is not part of the row."""
    folder = cache / sha256[:2]
    if not folder.is_dir():
        return None
    return next((entry for entry in sorted(folder.glob(f"{sha256}.*")) if entry.is_file()), None)


class PageImages:
    """Where a page image can be read: the staged page directory first, the checksum cache second.

    The staged file is the one the import wrote, so the tiles are cut from the image the tables were
    imported with; the cache is used for a page the import left no file for. The index is read once
    and kept, so that resolving thousands of pages does not reread it. A page that names an Image API
    service is matched through the service when its exact URL is not in the index, which is how a
    page whose full-size request was fetched is found from the service base.
    """

    def __init__(self, cache: Path | None = None, staged: Path | None = None) -> None:
        self.cache = cache
        self.staged = staged
        self._by_url: dict[str, Path] = {}
        self._by_service: dict[str, Path] = {}
        if cache is None or not (cache / "index.parquet").exists():
            return
        for row in images.index(cache):
            if row.superseded_by is not None:
                continue
            path = cache_file(cache, row.sha256)
            if path is None:
                continue
            self._by_url.setdefault(row.url, path)
            if row.service:
                self._by_service.setdefault(row.service, path)

    def find(self, url: str) -> tuple[tuple[Path, ...], str | None]:
        """The files that hold `url`, most preferred first, and its path under the cache root.

        The staged file the import wrote comes first and the cached copy second, so a page whose
        staged file is missing or unreadable is still cut from the cache, and a page whose staged
        file is there is cut from the image the tables were imported with.
        """
        cached = self._by_url.get(url)
        if cached is None:
            service = images.service_of(url)
            cached = self._by_service.get(service) if service else None
        relative = cached.relative_to(self.cache).as_posix() if cached is not None and self.cache else None
        found: list[Path] = []
        if self.staged is not None:
            stem = Path(urlsplit(url).path).stem
            for suffix in IMAGE_SUFFIXES:
                candidate = self.staged / f"{stem}{suffix}"
                if candidate.is_file():
                    found.append(candidate)
                    break
        if cached is not None and cached not in found:
            found.append(cached)
        return tuple(found), relative


# --- the build ---------------------------------------------------------------------------------


@dataclass
class Options:
    """Where the tables are and how the tiles are cut."""

    dataset: Path = ROOT / "work" / "codh-full"
    splits: Path = ROOT / "data" / "splits" / "codh.tsv"
    out: Path = ROOT / "work" / "detector"
    images: Path | None = None
    image_cache: Path | None = None
    tile: int = TILE
    overlap: int = OVERLAP
    min_coverage: float = MIN_COVERAGE
    empty_fraction: float = EMPTY_FRACTION
    materialise: bool = False
    quality: int = QUALITY
    workers: int = field(default_factory=lambda: min(8, os.cpu_count() or 1))
    limit: int | None = None
    documents: tuple[str, ...] = ()
    command: str = ""

    @property
    def staged(self) -> Path | None:
        """The staged page directory: `--images`, or `images/` inside the dataset."""
        return self.images if self.images is not None else self.dataset / "images"

    @property
    def tiles(self) -> Path:
        return self.out / "tiles"

    @property
    def cache(self) -> Path:
        return self.image_cache if self.image_cache is not None else images.images_root()


@dataclass
class SplitStats:
    """What one split holds, as written."""

    pages: int = 0
    tiles: int = 0
    annotated: int = 0
    empty: int = 0
    empty_kept: int = 0
    annotations: int = 0
    ignores: int = 0
    boxes: int = 0
    dropped: int = 0
    parts_dropped: int = 0
    densest: int = 0
    densest_at: str = ""
    sides: list[int] = field(default_factory=list)


@dataclass
class Report:
    """What the build found and wrote."""

    options: Options
    pages: int = 0
    pages_with_boxes: int = 0
    pages_without_image: int = 0
    boxes: int = 0
    dropped: int = 0
    parts_dropped: int = 0
    tiles: int = 0
    empty: int = 0
    empty_kept: int = 0
    annotations: int = 0
    ignores: int = 0
    tiles_written: int = 0
    tiles_failed: int = 0
    tile_bytes: int = 0
    skipped: Counter = field(default_factory=Counter)
    kinds: Counter = field(default_factory=Counter)
    missing: list[str] = field(default_factory=list)
    splits: dict[str, SplitStats] = field(default_factory=dict)

    @property
    def unique_boxes(self) -> int:
        """Source boxes that at least one tile kept, which is every box the tiling did not drop."""
        return self.boxes - self.dropped


@dataclass
class TileRecord:
    """One tile of a split: its COCO image entry, its annotations and where it is cut from."""

    entry: dict[str, Any]
    annotations: list[Annotation]
    empty: bool
    sources: tuple[Path, ...]
    tile: Tile


def load_units(path: Path | None) -> tuple[dict[str, list[tuple[str, str, Box]]], Counter]:
    """The boxes of every page, keyed by page id, and the units that carry no box."""
    units: dict[str, list[tuple[str, str, Box]]] = defaultdict(list)
    skipped: Counter = Counter()
    if path is None:
        return {}, skipped
    columns = ["id", "page_id", "box", "kind", "active"]
    for batch in tables.scan(path, Unit, columns=columns):
        for unit in batch:
            if not unit.active:
                skipped["inactive"] += 1
                continue
            if unit.box is None or unit.page_id is None:
                skipped["no box"] += 1
                continue
            units[unit.page_id].append((unit.id, str(unit.kind), unit.box))
    return units, skipped


def build(options: Options) -> Report:
    """Cut every page of the dataset into tiles and return what was found, JSON written."""
    options.dataset = options.dataset.resolve()
    options.splits = options.splits.resolve()
    options.out = options.out.resolve()
    options.images = options.images.resolve() if options.images is not None else None
    options.image_cache = options.image_cache.resolve() if options.image_cache is not None else None
    report = Report(options=options)
    report.splits = {name: SplitStats() for name in SPLITS}
    dataset = tables.Dataset(options.dataset)
    if dataset.tables["pages"] is None:
        raise SystemExit(f"{options.dataset}: no pages table")
    books = read_splits(options.splits)
    documents = {document.id: document for document in dataset.read("documents")}
    pages = [page for batch in dataset.scan("pages") for page in batch]
    units, report.skipped = load_units(dataset.tables["units"])

    unknown: set[str] = set()
    for page in pages:
        document = documents.get(page.document_id)
        if document is None:
            raise SystemExit(f"{page.id}: page names document {page.document_id}, which the table has not")
        if book_id(document) not in books:
            unknown.add(book_id(document))
    if unknown:
        raise SystemExit(f"{options.splits}: no row for {len(unknown)} book(s): {', '.join(sorted(unknown))}")

    where = PageImages(options.cache, options.staged)
    wanted = set(options.documents)
    records: dict[str, list[TileRecord]] = {name: [] for name in SPLITS}
    for page in pages:
        if options.limit is not None and report.pages >= options.limit:
            break
        if wanted and page.document_id not in wanted:
            continue
        document = documents[page.document_id]
        book = books[book_id(document)]
        stats = report.splits[book.split]
        sources, cached = where.find(page.image)
        if not sources:
            report.pages_without_image += 1
            report.missing.append(page.id)
            continue
        if not page.width or not page.height:
            report.pages_without_image += 1
            report.missing.append(page.id)
            continue
        report.pages += 1
        stats.pages += 1
        licence = str(document.image_rights.licence) if document.image_rights else "unknown"
        boxes: list[tuple[str, str, Box]] = []
        for unit_id, kind, box in units.get(page.id, ()):
            report.kinds[kind] += 1
            clipped = _clamp(box, page.width, page.height)
            if clipped is None:
                report.skipped["outside the page"] += 1
                continue
            if clipped != box:
                report.skipped["clamped to the page"] += 1
            boxes.append((unit_id, kind, clipped))
        if boxes:
            report.pages_with_boxes += 1
        report.boxes += len(boxes)
        for _, _, box in boxes:
            stats.sides.append(max(box.w, box.h))
        placed: dict[Tile, list[tuple[str, str, Placement]]] = defaultdict(list)
        for unit_id, kind, box in boxes:
            placements = place_box(
                box,
                page.width,
                page.height,
                tile=options.tile,
                overlap=options.overlap,
                min_coverage=options.min_coverage,
            )
            overlapped = overlapped_tiles(box, page.width, page.height, tile=options.tile, overlap=options.overlap)
            dropped = overlapped - len(placements)
            report.parts_dropped += dropped
            stats.parts_dropped += dropped
            if not placements:
                report.dropped += 1
                stats.dropped += 1
                continue
            stats.boxes += 1
            for placement in placements:
                placed[placement.tile].append((unit_id, kind, placement))
        for tile in tile_grid(page.width, page.height, options.tile, options.overlap):
            name = tile_name(page.id, tile.x, tile.y)
            entry = {
                "file_name": name,
                "tile_size": options.tile,
                "width": options.tile,
                "height": options.tile,
                "origin": [tile.x, tile.y],
                "page_id": page.id,
                "page_url": page.image,
                "page_width": page.width,
                "page_height": page.height,
                "document_id": page.document_id,
                "bid": book.bid,
                "production": book.production,
                "split": book.split,
                "licence": licence,
                "page_path": str(sources[0]),
            }
            if cached is not None:
                entry["cache_path"] = cached
            found = placed.get(tile, [])
            if options.materialise:
                entry["tile_path"] = str(options.tiles / name)
            annotations = [
                (unit_id, kind, placement.box.x, placement.box.y, placement.box.w, placement.box.h,
                 int(kind in IGNORE_KINDS), placement.coverage, placement.clipped)
                for unit_id, kind, placement in found
            ]
            records[book.split].append(
                TileRecord(entry=entry, annotations=annotations, empty=not found, sources=sources, tile=tile)
            )
            stats.tiles += 1
            if found:
                stats.annotated += 1
                if len(found) > stats.densest:
                    stats.densest = len(found)
                    stats.densest_at = f"{page.id} tile {tile.x},{tile.y}"
            else:
                stats.empty += 1

    for name in SPLITS:
        stats = report.splits[name]
        limit = empty_tile_limit(stats.annotated, fraction=options.empty_fraction)
        empty = [index for index, record in enumerate(records[name]) if record.empty]
        keep = {empty[index] for index in spread_indices(len(empty), min(len(empty), limit))}
        records[name] = [
            record for index, record in enumerate(records[name]) if not record.empty or index in keep
        ]
        stats.empty_kept = len(keep)

    if options.materialise:
        _materialise(records, report)
    _recount(records, report)
    options.out.mkdir(parents=True, exist_ok=True)
    for name in SPLITS:
        write_split(options.out / f"{name}.json", records[name], options)
    (options.out / "stats.md").write_text(stats_markdown(report), encoding="utf-8")
    return report


def _materialise(records: dict[str, list[TileRecord]], report: Report) -> None:
    """Write every tile as a JPEG, drop the tiles that could not be written, prune stale files."""
    options = report.options
    options.tiles.mkdir(parents=True, exist_ok=True)
    jobs = [
        (record.sources, record.tile.x, record.tile.y, options.tiles / record.entry["file_name"])
        for name in SPLITS
        for record in records[name]
    ]

    def run(job: tuple[tuple[Path, ...], int, int, Path]) -> tuple[str, int | None]:
        return job[3].name, _write_tile(job, options.tile, options.quality)

    failed: set[str] = set()
    sizes: list[int] = []
    with ThreadPoolExecutor(max_workers=max(1, options.workers)) as pool:
        for name, size in pool.map(run, jobs):
            if size is None:
                failed.add(name)
            else:
                sizes.append(size)
    if failed:
        for name in SPLITS:
            records[name] = [record for record in records[name] if record.entry["file_name"] not in failed]
    report.tiles_written = len(sizes)
    report.tiles_failed = len(failed)
    report.tile_bytes = sum(sizes)
    keep = {job[3].name for job in jobs} - failed
    for stale in options.tiles.glob("*.jpg"):
        if stale.name not in keep:
            stale.unlink()


def _write_tile(job: tuple[tuple[Path, ...], int, int, Path], size: int, quality: int) -> int | None:
    """One materialised tile: the page read, the tile cut, the white padding added. Bytes written.

    The sources are tried in order, so a page whose staged file was removed after the page was read
    is still cut from its cached copy.
    """
    sources, x, y, target = job
    for source in sources:
        try:
            with Image.open(source) as handle:
                page = handle.convert("RGB")
                canvas = Image.new("RGB", (size, size), (PAD, PAD, PAD))
                left, top = max(0, x), max(0, y)
                right, bottom = min(x + size, page.width), min(y + size, page.height)
                if right > left and bottom > top:
                    canvas.paste(page.crop((left, top, right, bottom)), (left - x, top - y))
                canvas.save(target, format="JPEG", quality=quality)
        except (OSError, ValueError):
            continue
        return target.stat().st_size
    return None


def _recount(records: dict[str, list[TileRecord]], report: Report) -> None:
    """Fill the per-split tile and annotation counts from the records that are written.

    `empty` keeps the number of tiles without an annotation the pages hold; `empty_kept` is the
    number the cap left in the split.
    """
    report.tiles = report.empty = report.empty_kept = report.annotations = report.ignores = 0
    for name in SPLITS:
        stats = report.splits[name]
        stats.tiles = stats.annotated = stats.empty_kept = 0
        stats.annotations = stats.ignores = 0
        stats.densest = 0
        stats.densest_at = ""
        for record in records[name]:
            stats.tiles += 1
            if record.empty:
                stats.empty_kept += 1
                continue
            stats.annotated += 1
            stats.annotations += len(record.annotations)
            stats.ignores += sum(annotation[6] for annotation in record.annotations)
            if len(record.annotations) > stats.densest:
                stats.densest = len(record.annotations)
                stats.densest_at = f"{record.entry['page_id']} tile {record.tile.x},{record.tile.y}"
        report.tiles += stats.tiles
        report.empty += stats.empty
        report.empty_kept += stats.empty_kept
        report.annotations += stats.annotations
        report.ignores += stats.ignores


def write_split(path: Path, records: list[TileRecord], options: Options) -> int:
    """Write one split as a COCO document and return its number of tiles.

    Images and annotations are streamed, so a split of millions of annotations is written without
    holding its JSON text. Image ids run in page order and start at 1.
    """
    info = {
        "description": "CODH character boxes as detector training tiles",
        "dataset": str(options.dataset),
        "splits": str(options.splits),
        "tile": options.tile,
        "overlap": options.overlap,
        "stride": options.tile - options.overlap,
        "pad": PAD,
        "min_coverage": options.min_coverage,
        "empty_fraction": options.empty_fraction,
        "category": CATEGORY,
    }
    categories = [{"id": 1, "name": CATEGORY, "supercategory": "unit"}]
    names = sorted({record.entry["licence"] for record in records})
    licences = [{"id": index, "name": name} for index, name in enumerate(names, start=1)]
    licence_ids = {entry["name"]: entry["id"] for entry in licences}
    dump = json.dumps
    with path.open("w", encoding="utf-8") as handle:
        handle.write('{"info":')
        handle.write(dump(info, ensure_ascii=False, separators=(",", ":")))
        handle.write(',"licenses":')
        handle.write(dump(licences, ensure_ascii=False, separators=(",", ":")))
        handle.write(',"categories":')
        handle.write(dump(categories, ensure_ascii=False, separators=(",", ":")))
        handle.write(',"images":[')
        for index, record in enumerate(records, start=1):
            entry = dict(record.entry)
            entry["id"] = index
            entry["licence_id"] = licence_ids[entry["licence"]]
            if index > 1:
                handle.write(",")
            handle.write(dump(entry, ensure_ascii=False, separators=(",", ":")))
        handle.write('],"annotations":[')
        number = 0
        for index, record in enumerate(records, start=1):
            for unit_id, kind, x, y, w, h, iscrowd, coverage, clipped in record.annotations:
                number += 1
                annotation = {
                    "id": number,
                    "image_id": index,
                    "category_id": 1,
                    "bbox": [x, y, w, h],
                    "area": w * h,
                    "iscrowd": iscrowd,
                    "source_unit_id": unit_id,
                    "kind": kind,
                    "coverage": coverage,
                    "clipped": clipped,
                }
                handle.write("," if number > 1 else "")
                handle.write(dump(annotation, ensure_ascii=False, separators=(",", ":")))
        handle.write("]}")
    return len(records)


# --- the report --------------------------------------------------------------------------------


def deciles(values: list[int]) -> list[int]:
    """The ten deciles of `values`: the 10% cut to the 90% cut, then the largest value."""
    if len(values) < 2:
        return []
    cuts = statistics.quantiles(sorted(values), n=10, method="inclusive")
    return [round(cut) for cut in cuts] + [max(values)]


def stats_markdown(report: Report) -> str:
    """`work/detector/stats.md`: what each split holds, in the numbers the cards quote."""
    options = report.options
    lines = [
        "# Detector data",
        "",
        f"Dataset `{options.dataset}`, split `{options.splits}`, written to `{options.out}`.",
        f"Command `{options.command or _command()}`.",
        "",
        f"Tiles {options.tile}x{options.tile} with {options.overlap} px of overlap, padded with white; a",
        f"clipped part under {options.min_coverage:.0%} of its source box is dropped, a box larger than one",
        "tile is kept in the tile that holds its centre, and tiles without an annotation are kept up to",
        f"{options.empty_fraction:.0%} of a split's tiles.",
        "",
        "| split | pages | tiles | of them empty | annotations | ignore regions | unique boxes | dropped boxes | dropped parts |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for name in SPLITS:
        stats = report.splits[name]
        lines.append(
            f"| {name} | {stats.pages} | {stats.tiles} | {stats.empty_kept} | {stats.annotations} | "
            f"{stats.ignores} | {stats.boxes} | {stats.dropped} | {stats.parts_dropped} |"
        )
    lines.append(
        f"| all | {report.pages} | {report.tiles} | {report.empty_kept} | {report.annotations} | "
        f"{report.ignores} | {report.unique_boxes} | {report.dropped} | {report.parts_dropped} |"
    )
    lines += [
        "",
        (
            f"Pages: {report.pages} of {report.pages + report.pages_without_image} with an image"
            f" ({report.pages_without_image} without)."
        ),
        (
            f"Source boxes: {report.boxes}; dropped boxes: {report.dropped}; unique boxes:"
            f" {report.unique_boxes}; clipped parts under {options.min_coverage:.0%} dropped:"
            f" {report.parts_dropped}."
        ),
        f"Tile annotations: {report.annotations}, of them {report.ignores} ignore regions.",
        (
            f"Tiles: {report.tiles}, of them {report.empty_kept} of {report.empty} without an"
            f" annotation, kept under the {options.empty_fraction:.0%} cap."
        ),
    ]
    if report.missing:
        lines.append(
            f"Pages left out, their image in neither the staged directory nor the cache:"
            f" {len(report.missing)} ({', '.join(report.missing[:10])}{', …' if len(report.missing) > 10 else ''})."
        )
    if options.materialise:
        lines.append(
            f"Materialised tiles: {report.tiles_written} files, {report.tile_bytes} bytes"
            f" ({report.tile_bytes / 1e9:.2f} GB), under `{options.tiles}`."
        )
        if report.tiles_failed:
            lines.append(f"Tiles that could not be written: {report.tiles_failed}.")
    else:
        lines.append("Tiles are not materialised; a training run cuts them from the page at the recorded origin.")
    lines += ["", "## Box size deciles (larger side of the source box, page pixels)", ""]
    lines.append("| split | " + " | ".join(f"{tenth}%" for tenth in range(10, 101, 10)) + " |")
    lines.append("| --- |" + " --- |" * 10)
    for name in SPLITS:
        cuts = deciles(report.splits[name].sides)
        row = " | ".join(str(cut) for cut in cuts) if cuts else " | ".join("—" for _ in range(10))
        lines.append(f"| {name} | {row} |")
    lines += ["", "## Densest tile", ""]
    for name in SPLITS:
        stats = report.splits[name]
        lines.append(f"- {name}: {stats.densest} annotations, {stats.densest_at or '—'}.")
    lines += ["", "## Units by kind", "", "| kind | units |", "| --- | --- |"]
    for kind, count in sorted(report.kinds.items()):
        lines.append(f"| {kind} | {count} |")
    lines += [
        "",
        f"Kinds that become ignore regions (`iscrowd=1`): {', '.join(sorted(IGNORE_KINDS))}.",
    ]
    if report.skipped:
        lines += ["", "## Units left out", "", "| reason | units |", "| --- | --- |"]
        for reason, count in sorted(report.skipped.items()):
            lines.append(f"| {reason} | {count} |")
    lines.append("")
    return "\n".join(lines)


def _command() -> str:
    """The command line, as the script would be run with the arguments it was given."""
    argv = sys.argv[1:] if sys.argv and Path(sys.argv[0]).name.startswith("build_detector_data") else []
    return "python scripts/build_detector_data.py " + " ".join(argv)


def print_report(report: Report, seconds: float) -> None:
    """The numbers the card's acceptance names, then the per-split table."""
    options = report.options
    print(f"dataset         {options.dataset}", flush=True)
    print(f"splits          {options.splits}", flush=True)
    print(f"pages           {report.pages}", flush=True)
    print(f"boxes           {report.boxes}", flush=True)
    print(f"dropped boxes   {report.dropped}", flush=True)
    print(f"dropped parts   {report.parts_dropped}", flush=True)
    print(f"unique boxes    {report.unique_boxes}", flush=True)
    print(f"annotations     {report.annotations}", flush=True)
    print(f"ignore regions  {report.ignores}", flush=True)
    print(f"tiles           {report.tiles}", flush=True)
    print(f"empty tiles     {report.empty_kept} kept of {report.empty}", flush=True)
    if options.materialise:
        print(f"tile files      {report.tiles_written}, {report.tile_bytes} bytes", flush=True)
        if report.tiles_failed:
            print(f"tile failures   {report.tiles_failed}", flush=True)
    if report.missing:
        listing = ", ".join(report.missing[:5]) + (", …" if len(report.missing) > 5 else "")
        print(f"missing images  {len(report.missing)} pages, in neither the staged directory nor the cache: {listing}", flush=True)
    for name in SPLITS:
        stats = report.splits[name]
        print(
            f"{name:<15} pages {stats.pages}, tiles {stats.tiles} ({stats.empty_kept} empty), "
            f"annotations {stats.annotations}, boxes {stats.boxes}, dropped {stats.dropped}, "
            f"parts dropped {stats.parts_dropped}",
            flush=True,
        )
    print(f"written         {options.out} in {seconds:.1f}s", flush=True)


# --- command line ------------------------------------------------------------------------------


def parse_args(argv: Sequence[str] | None = None) -> Options:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dataset", type=Path, default=Options.dataset, help="dataset directory of tables")
    parser.add_argument("--splits", type=Path, default=Options.splits, help="split table by book")
    parser.add_argument("--out", type=Path, default=Options.out, help="where the COCO splits are written")
    parser.add_argument("--images", type=Path, default=None, help="staged page images; default <dataset>/images")
    parser.add_argument("--image-cache", type=Path, default=None, help="the images directory of the cache")
    parser.add_argument("--materialise", action="store_true", help="write the tiles as JPEGs under <out>/tiles")
    parser.add_argument("--tile", type=int, default=TILE, help=f"tile side in pixels (default {TILE})")
    parser.add_argument("--overlap", type=int, default=OVERLAP, help=f"overlap in pixels (default {OVERLAP})")
    parser.add_argument("--min-coverage", type=float, default=MIN_COVERAGE, help="least part of a box to keep")
    parser.add_argument("--empty-fraction", type=float, default=EMPTY_FRACTION, help="most empty tiles per split")
    parser.add_argument("--quality", type=int, default=QUALITY, help="JPEG quality of a materialised tile")
    parser.add_argument("--workers", type=int, default=Options().workers, help="threads that write tiles")
    parser.add_argument("--limit", type=int, default=None, help="stop after this many pages")
    parser.add_argument("--document", action="append", default=None, help="only this document id (repeatable)")
    given = list(argv) if argv is not None else sys.argv[1:]
    args = parser.parse_args(given)
    command = "python scripts/build_detector_data.py " + " ".join(str(value) for value in given)
    return Options(
        dataset=args.dataset,
        splits=args.splits,
        out=args.out,
        images=args.images,
        image_cache=args.image_cache,
        tile=args.tile,
        overlap=args.overlap,
        min_coverage=args.min_coverage,
        empty_fraction=args.empty_fraction,
        materialise=args.materialise,
        quality=args.quality,
        workers=args.workers,
        limit=args.limit,
        documents=tuple(args.document or ()),
        command=command,
    )


def main(argv: Sequence[str] | None = None) -> int:
    options = parse_args(argv)
    started = time.monotonic()
    report = build(options)
    print_report(report, time.monotonic() - started)
    return 1 if report.tiles_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
