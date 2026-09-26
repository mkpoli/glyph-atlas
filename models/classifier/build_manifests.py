"""Cut one crop per unit and write the classifier's data manifests.

The input is the CODH importer's tables (`work/codh-full`): one row per unit, with the box it occupies
on its page. Every unit that is an active single character with a box becomes one crop, cut from the
image that holds it. The 1024x1024 tiles the detector's training data build materialised under
`work/detector/tiles/` are read
whenever a unit falls inside one, which is the cheaper source: a tile is a 200 KB JPEG against the
several megabytes of a full page. A unit outside every tile is cut from the staged page image under
`work/codh-full/images/`, or from the checksum cache through `glyph_atlas.images`.

Crops are written grey, as JPEG, under `work/classifier/crops/<split>/<bucket>/<unit id>.jpg`, cut
exactly to the unit's box: the box is CODH's own and the classifier is measured on the same kind of
box the detector returns, so no margin is added and nothing is resized here. The preprocessing that
resizes happens in `glyph_atlas.classify`, once, for training and for serving alike.

The split is by book, from `data/splits/codh.tsv`, so a hand, a block and a scan stay on one
side of the split. The classes are the code points with at least `--min-train` crops in `train`, plus
`other`, which carries every code point below that line and every code point the classifier was not
trained on. The class order is written to `work/classifier/classes.json`: by descending number of
training crops, then by code point, with `other` last. The served class list comes from
`build_combined.py`, which adds the HI Lab crops to these manifests.

    python models/classifier/build_manifests.py
    python models/classifier/build_manifests.py --limit 2000 --only train,test

The manifests are `work/classifier/{train,val,test}.parquet`, with the unit id, the crop path and the
label of every crop, plus the code point the unit actually is, the document, the page, the split and
the production type, which the metrics of `train.py` break down by. `work/classifier/stats.json`
holds the counts the README reports. Two runs over the same tables write the same files; a crop that
is already on disk is kept unless `--refresh` is given.

Nothing here touches the network, and nothing here reads the HI Lab tables: that experiment has its
own builder in `train_with_hilab.py` and never mixes into the baseline.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from glyph_atlas import images, tables
from glyph_atlas.schema import Box

ROOT = Path(__file__).resolve().parents[2]

#: Tile geometry of the detector's training data build, which names the materialised tiles.
TILE = 1024
OVERLAP = 128
STRIDE = TILE - OVERLAP

#: Unit kinds that are one character on one crop. A ligature is one code point here (`U+309F` is a
#: digraph written as one glyph), so it is a class like any other.
KINDS = ("char", "iteration-mark", "ligature", "punctuation")

#: Image suffixes a staged page may carry.
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png")

#: JPEG quality of a crop: high enough that the resize to 96 square sees the strokes.
QUALITY = 95

SPLITS = ("train", "val", "test")

#: Characters kept as they are in a crop file name; the rest, ':' included, become '_'.
UNSAFE = re.compile(r"[^A-Za-z0-9._-]")

#: How many page images are decoded at once by default.
WORKERS = min(8, os.cpu_count() or 1)

#: The columns of `units` the builder reads.
UNIT_COLUMNS = [
    "id",
    "document_id",
    "page_id",
    "box",
    "kind",
    "unicode",
    "script",
    "active",
    "split_into",
    "merged_into",
]


class Crop(BaseModel):
    """One crop of the classifier's data: the unit it is, where it is, and what it should read."""

    model_config = ConfigDict(extra="forbid")

    unit_id: str = Field(description="the CODH unit the crop was cut from")
    crop: str = Field(description="path of the crop file, relative to the repository root")
    label: str = Field(description="the class the crop is trained as: a code point, or `other`")
    code_point: str = Field(description="the code point the unit actually is, `other` included")
    document_id: str
    page_id: str
    split: str
    production: str = "unknown"
    kind: str = "char"
    script: str | None = None


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


def book_id(document_id: str, source_refs: Any = None) -> str:
    """The upstream book id of a document: its CODH source reference, or the id between the colons."""
    refs = source_refs
    if isinstance(refs, str):
        try:
            refs = json.loads(refs)
        except ValueError:
            refs = None
    if isinstance(refs, dict):
        for key in ("codh-char-shape", "nijl-bid"):
            value = refs.get(key)
            if value:
                return str(value)
    parts = document_id.split(":")
    return parts[1] if document_id.startswith("codh:") and len(parts) > 2 else document_id


def tile_index(directory: Path) -> dict[str, dict[tuple[int, int], Path]]:
    """The materialised tiles of the detector's training data build by page key and origin, when they
    are on disk.

    A tile file is named `<page id, ':' replaced by '_'>_<x>_<y>.jpg`, which is how
    `scripts/build_detector_data.py` writes it, so the origin the split file records is read back
    from the name.
    """
    index: dict[str, dict[tuple[int, int], Path]] = defaultdict(dict)
    if not directory.is_dir():
        return {}
    for file in sorted(directory.glob("*.jpg")):
        parts = file.stem.rsplit("_", 2)
        if len(parts) != 3 or not parts[1].isdigit() or not parts[2].isdigit():
            continue
        index[parts[0]][(int(parts[1]), int(parts[2]))] = file
    return index


def tile_for(tiles: dict[tuple[int, int], Path], box: Box) -> tuple[Path, int, int] | None:
    """The tile that holds a whole unit box, with the origin to subtract, or None.

    A tile covers `[origin, origin + 1024)`; the strip past the page edge is white padding, and a box
    that lies on the page never reaches it. The tile with the largest origin at or before the box is
    tried first, then the origins after it.
    """
    for left in _candidates({origin[0] for origin in tiles}, box.x):
        if not (left <= box.x and box.x + box.w <= left + TILE):
            continue
        for top in _candidates({origin[1] for origin in tiles}, box.y):
            if not (top <= box.y and box.y + box.h <= top + TILE):
                continue
            found = tiles.get((left, top))
            if found is not None:
                return found, left, top
    return None


def _candidates(origins: Iterable[int], value: int) -> list[int]:
    """The origins to try for a coordinate: the last at or before it first, then the ones after."""
    before = sorted((origin for origin in origins if origin <= value), reverse=True)
    after = sorted(origin for origin in origins if origin > value)
    return before + after


class PageImages:
    """Where a page image can be read: the staged page directory first, the checksum cache second.

    A page names the Image API service it comes from rather than a fetched request, so a cache row is
    matched through the service as well as through the exact URL. The index is read once and kept.
    """

    def __init__(self, staged: Path | None, cache: Path | None) -> None:
        self.staged = staged
        self._by_url: dict[str, Path] = {}
        self._by_service: dict[str, Path] = {}
        if cache is None or not (cache / "index.parquet").exists():
            return
        for row in images.index(cache):
            if row.superseded_by is not None:
                continue
            folder = cache / row.sha256[:2]
            file = next((entry for entry in sorted(folder.glob(f"{row.sha256}.*")) if entry.is_file()), None)
            if file is None:
                continue
            self._by_url.setdefault(row.url, file)
            if row.service:
                self._by_service.setdefault(row.service, file)

    def find(self, url: str) -> Path | None:
        """The file that holds `url`, or None when the page image is not on disk."""
        if self.staged is not None:
            stem = Path(urlsplit(url).path).stem
            for suffix in IMAGE_SUFFIXES:
                candidate = self.staged / f"{stem}{suffix}"
                if candidate.is_file():
                    return candidate
        path = self._by_url.get(url)
        if path is None:
            service = images.service_of(url)
            path = self._by_service.get(service) if service else None
        return path


@dataclass
class Options:
    """Where the tables are and where the crops go."""

    dataset: Path = ROOT / "work" / "codh-full"
    splits: Path = ROOT / "data" / "splits" / "codh.tsv"
    out: Path = ROOT / "work" / "classifier"
    classes: Path = ROOT / "work" / "classifier" / "classes.json"
    detector: Path = ROOT / "work" / "detector"
    image_cache: Path = ROOT / "cache" / "images"
    min_train: int = 20
    other: str = "other"
    limit: int | None = None
    only: tuple[str, ...] = SPLITS
    refresh: bool = False
    workers: int = WORKERS
    command: str = ""

    @property
    def crops(self) -> Path:
        return self.out / "crops"

    @property
    def staged(self) -> Path:
        return self.dataset / "images"

    @property
    def tiles(self) -> Path:
        return self.detector / "tiles"


@dataclass
class Row:
    """One unit that becomes one crop, with everything the manifest records about it."""

    unit_id: str
    code_point: str
    split: str
    document_id: str
    page_id: str
    page_url: str
    box: Box
    production: str
    kind: str
    script: str | None


@dataclass
class Tally:
    """What the build did, for `stats.json` and for the lines it prints."""

    units: int = 0
    written: int = 0
    kept: int = 0
    from_tile: int = 0
    from_page: int = 0
    by_split: Counter = field(default_factory=Counter)
    skipped: Counter = field(default_factory=Counter)
    pages_missing: set = field(default_factory=set)

    def merge(self, other: Tally) -> None:
        self.units += other.units
        self.written += other.written
        self.kept += other.kept
        self.from_tile += other.from_tile
        self.from_page += other.from_page
        self.by_split.update(other.by_split)
        self.skipped.update(other.skipped)
        self.pages_missing |= other.pages_missing


def crop_path(root: Path, split: str, unit_id: str) -> Path:
    """Where the crop of a unit goes: one directory per split and per shard of its id."""
    bucket = hashlib.sha1(unit_id.encode("utf-8")).hexdigest()[:2]
    return root / split / bucket / f"{UNSAFE.sub('_', unit_id)}.jpg"


def manifest_path(root: Path, split: str, unit_id: str) -> str:
    """The crop path as the manifest records it: relative to the repository root where it can be."""
    target = root / crop_path(Path(), split, unit_id)
    try:
        return target.relative_to(ROOT).as_posix()
    except ValueError:
        return target.as_posix()


def read_units(path: Path, books: dict[str, Book]) -> tuple[list[Row], Counter, Counter]:
    """Every unit that becomes a crop, in table order, with the split of its book and what was left out."""
    dataset = tables.Dataset(path)
    document_splits: dict[str, tuple[str, str]] = {}
    for document in dataset.read("documents"):
        book = books.get(book_id(document.id, getattr(document, "source_refs", None)))
        if book is not None:
            document_splits[document.id] = (book.split, book.production)
    page_urls = {page.id: page.image for page in dataset.read("pages")}
    rows: list[Row] = []
    skipped: Counter = Counter()
    by_split: Counter = Counter()
    for batch in dataset.scan("units", columns=UNIT_COLUMNS):
        for unit in batch:
            found = document_splits.get(unit.document_id)
            if found is None:
                skipped["document not in the split table"] += 1
                continue
            split, production = found
            if not unit.active or unit.merged_into or unit.split_into:
                skipped["not an active unit"] += 1
                continue
            if unit.kind not in KINDS:
                skipped[f"kind {unit.kind}"] += 1
                continue
            if not unit.unicode or unit.box is None or unit.box.w < 1 or unit.box.h < 1:
                skipped["no code point or no box"] += 1
                continue
            url = page_urls.get(unit.page_id or "")
            if not url:
                skipped["no page image"] += 1
                continue
            rows.append(
                Row(
                    unit_id=unit.id,
                    code_point=unit.unicode,
                    split=split,
                    document_id=unit.document_id,
                    page_id=unit.page_id or "",
                    page_url=url,
                    box=unit.box,
                    production=production,
                    kind=str(unit.kind),
                    script=str(unit.script) if unit.script else None,
                )
            )
            by_split[split] += 1
    return rows, skipped, by_split


def class_order(counts: Counter, *, other: str = "other") -> list[str]:
    """The class order: by descending training crops, then by code point, `other` last."""
    return [name for name, _ in sorted(counts.items(), key=lambda item: (-item[1], item[0]))] + [other]


def cut(rows: Iterable[Row], options: Options, pages: PageImages, tiles: Any) -> Tally:
    """Cut and write the crop of every row, from the tiles where one holds it and the page otherwise."""
    jobs: dict[tuple[str, int, int, bool], list[Row]] = defaultdict(list)
    tally = Tally()
    for row in rows:
        tally.units += 1
        tally.by_split[row.split] += 1
        found = tile_for(tiles.get(UNSAFE.sub("_", row.page_id), {}), row.box)
        if found is not None:
            jobs[(str(found[0]), found[1], found[2], True)].append(row)
            continue
        page = pages.find(row.page_url)
        if page is None:
            tally.skipped["page image not on disk"] += 1
            tally.pages_missing.add(row.page_id)
            continue
        jobs[(str(page), 0, 0, False)].append(row)
    ordered = sorted(jobs.items(), key=lambda item: item[0][0])
    with ThreadPoolExecutor(max_workers=max(1, options.workers)) as pool:
        for part in pool.map(lambda job: _cut_job(job, options), ordered):
            tally.merge(part)
    return tally


def _cut_job(job: tuple[tuple[str, int, int, bool], list[Row]], options: Options) -> Tally:
    """Cut every crop that comes from one image file.

    An image the import is rewriting under the run, or one that a partial import left out, is
    counted and skipped: the crops of the other images are still cut, and a second run picks the
    missing ones up.
    """
    (name, left, top, from_tile), rows = job
    tally = Tally()
    tally.units = len(rows)
    if from_tile:
        tally.from_tile = len(rows)
    else:
        tally.from_page = len(rows)
    try:
        handle = Image.open(name)
    except (FileNotFoundError, OSError, ValueError):
        tally.skipped["the source image cannot be read"] += len(rows)
        return tally
    with handle:
        image = handle.convert("L")
        for row in rows:
            target = options.crops / crop_path(Path(), row.split, row.unit_id)
            if target.exists() and not options.refresh:
                tally.kept += 1
                continue
            box = (
                max(0, row.box.x - left),
                max(0, row.box.y - top),
                min(image.width, row.box.x + row.box.w - left),
                min(image.height, row.box.y + row.box.h - top),
            )
            if box[2] <= box[0] or box[3] <= box[1]:
                tally.skipped["box outside the image"] += 1
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            image.crop(box).save(target, format="JPEG", quality=QUALITY)
            tally.written += 1
    return tally


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dataset", type=Path, default=Options.dataset)
    parser.add_argument("--split-table", type=Path, default=Options.splits)
    parser.add_argument("--out", type=Path, default=Options.out)
    parser.add_argument("--classes", type=Path, default=Options.classes)
    parser.add_argument("--detector", type=Path, default=Options.detector)
    parser.add_argument("--image-cache", type=Path, default=Options.image_cache)
    parser.add_argument("--min-train", type=int, default=Options.min_train)
    parser.add_argument("--limit", type=int, default=None, help="units per split, for a smoke run")
    parser.add_argument("--only", default="train,val,test", help="the splits to build")
    parser.add_argument("--refresh", action="store_true", help="rewrite crops that are on disk")
    parser.add_argument("--workers", type=int, default=WORKERS)
    args = parser.parse_args(argv)

    options = Options(
        dataset=args.dataset,
        splits=args.split_table,
        out=args.out,
        classes=args.classes,
        detector=args.detector,
        image_cache=args.image_cache,
        min_train=args.min_train,
        limit=args.limit,
        only=tuple(part.strip() for part in args.only.split(",") if part.strip()),
        refresh=args.refresh,
        workers=args.workers,
        command="python " + " ".join(shlex.quote(part) for part in (argv if argv is not None else sys.argv[1:])),
    )
    unknown = [name for name in options.only if name not in SPLITS]
    if unknown:
        raise SystemExit(f"--only names {', '.join(unknown)}; expected a subset of {', '.join(SPLITS)}")
    if not options.dataset.is_dir():
        raise SystemExit(f"{options.dataset} is missing: the CODH importer writes the tables there")

    books = read_splits(options.splits)
    rows, skipped, by_split = read_units(options.dataset, books)
    print(
        "units with a box: " + ", ".join(f"{name} {by_split[name]}" for name in SPLITS),
        flush=True,
    )
    for reason, count in sorted(skipped.items()):
        print(f"  left out: {count} units, {reason}", flush=True)

    wanted = [row for row in rows if row.split in options.only]
    if options.limit is not None:
        taken: Counter = Counter()
        trimmed: list[Row] = []
        for row in wanted:
            if taken[row.split] >= options.limit:
                continue
            taken[row.split] += 1
            trimmed.append(row)
        wanted = trimmed
    raw_counts: Counter = Counter(row.code_point for row in wanted if row.split == "train")
    counts: Counter = Counter(
        {name: count for name, count in raw_counts.items() if count >= options.min_train}
    )
    classes = class_order(counts, other=options.other)
    names = set(classes)
    print(
        f"{len(classes) - 1} classes with at least {options.min_train} train crops "
        f"({sum(counts.values())} crops), {len(raw_counts) - len(counts)} code points below the line, "
        f"plus {options.other}",
        flush=True,
    )

    pages = PageImages(options.staged, options.image_cache if options.image_cache.is_dir() else None)
    tiles = tile_index(options.tiles)
    print(f"{sum(len(entries) for entries in tiles.values())} materialised tiles over {len(tiles)} pages", flush=True)
    tally = cut(wanted, options, pages, tiles)
    print(
        f"crops: {tally.written} written, {tally.kept} already on disk; "
        f"sources tile {tally.from_tile}, page {tally.from_page}",
        flush=True,
    )
    for reason, count in sorted(tally.skipped.items()):
        print(f"  left out: {count} crops, {reason}", flush=True)
    if tally.pages_missing:
        print(f"  {len(tally.pages_missing)} pages have no image on disk", flush=True)

    manifests: dict[str, int] = {}
    labels: dict[str, dict[str, int]] = {}
    for split in SPLITS:
        members = [row for row in wanted if row.split == split]
        records = [
            Crop(
                unit_id=row.unit_id,
                crop=manifest_path(options.out / "crops", split, row.unit_id),
                label=row.code_point if row.code_point in names else options.other,
                code_point=row.code_point,
                document_id=row.document_id,
                page_id=row.page_id,
                split=split,
                production=row.production,
                kind=row.kind,
                script=row.script,
            )
            for row in members
        ]
        target = options.out / f"{split}.parquet"
        manifests[split] = tables.write(target, records, Crop, command=options.command)
        labels[split] = dict(
            sorted(Counter(record.label for record in records).items(), key=lambda item: (-item[1], item[0]))
        )
        print(f"-> {target}: {manifests[split]} crops", flush=True)

    options.classes.parent.mkdir(parents=True, exist_ok=True)
    options.classes.write_text(
        json.dumps(
            {
                "classes": classes,
                "other": options.other,
                "size": 96,
                "min_train_crops": options.min_train,
                "source": f"{options.out}/{{train,val,test}}.parquet",
                "built_at": datetime.now(tz=UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    stats = {
        "command": options.command,
        "dataset": str(options.dataset),
        "split_table": str(options.splits),
        "classes": len(classes),
        "identification_classes": len(classes) - 1,
        "min_train_crops": options.min_train,
        "other": options.other,
        "units": {split: int(by_split[split]) for split in SPLITS},
        "crops": manifests,
        "labels": labels,
        "built": {"written": tally.written, "kept": tally.kept, "from_tile": tally.from_tile, "from_page": tally.from_page},
        "skipped": dict(sorted(skipped.items())),
        "skipped_crops": dict(sorted(tally.skipped.items())),
        "pages_without_an_image": sorted(tally.pages_missing)[:50],
        "class_counts": dict(sorted(raw_counts.items(), key=lambda item: (-item[1], item[0]))),
    }
    (options.out / "stats.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"-> {options.classes}: {len(classes)} classes", flush=True)
    print(f"-> {options.out / 'stats.json'}", flush=True)


if __name__ == "__main__":
    main()
