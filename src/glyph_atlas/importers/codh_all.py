"""Import every book of the 日本古典籍くずし字データセット into the tables.

The dataset is 44 books. Each book is published as `{bid}.zip` under
`http://codh.rois.ac.jp/char-shape/dataset/v2/` and lands in `cache/codh/{bid}.zip`, or all of them
together in one archive that holds a directory per book. A book's archive holds
`{bid}/{bid}_coordinate.csv` (columns Unicode, Image, X, Y, Block ID, Char ID, Width, Height),
`{bid}/{bid}_report.csv` where the annotators reported damage (columns Image, X, Y, Report), the
corrected page images under `{bid}/images/` and one crop per character under `{bid}/characters/`.
Only the two CSVs and the page images are read; the crops are addressed through CODH's IIIF server.

`data/sources/codh-books.tsv` carries the identifier, the title, the type and the published counts
of every book, read from the CODH book list, and the NIJL IIIF manifest `cache/codh-manifests/
{bid}.json` carries the collection a scan belongs to in `DCTERMS.relation`. The three books whose
identifiers are CODH-local carry no manifest, so they have no NIJL 書誌ID and no holder.

One book becomes one document, one page per image of its `images/` directory, blank pages included,
and one unit per row of its coordinate CSV. A page image that the cache does not hold yet is written
under `out/images/{image}.jpg` and registered in the image cache under the CODH IIIF URL
`https://codh.rois.ac.jp/char-shape/iiif/{bid}/{image}.tif`, which is the key its page is addressed
by, so every page record reaches its image whether or not the file was ever cached. An image the
cache already holds is read for its size and checksum and not written out again.

`register_images` is the second pass and copies the images into the cache. A page image of the
corpus is a full-size scan of about 4 MB, near 26 GB for the 6,151 pages, so the pass registers a
bounded set: the pages named by its `pages` argument, or, by default, every page of the test and
validation books of `data/splits/codh.tsv` and of a sample of its train books. Any other page
is fetched later through its IIIF URL without another import. The staged copy of an image that
reaches the cache is deleted, since the cache holds it content-addressed; only the images of pages
that are still not cached stay under `out/images/`.

A row of `{bid}_report.csv` is a note the annotators left on a spot of a page rather than a
character. It becomes a unit `codh:{bid}:{image}:report:{n}` of kind `unreadable`, review state
`rejected`, with no text, the note in `upstream["report"]` and the position it states in
`upstream["x"]` and `upstream["y"]`. The CSV gives a point, not a box, so the unit carries no box.
Report units are counted apart from the character units.

A coordinate box that reaches past its page image is cut at the page edge, since a table validates
that a box lies inside its page; the coordinates upstream states stay in the unit's
`upstream["box"]`. One box of the first 589,084 units does:
`codh:200003076:200003076_00027_2:B0001:C0017`, stated as 2035,2368,129,122 on a 2159×3158 page,
5 px past the right edge.

Every code point of the coordinate CSV is a modern one: CODH files every hentaigana under the
hiragana or katakana code point of its reading, so the form behind the crop is unknown (section 3 of
`docs/plan.md`, `data/sources/codh-char-shape.yaml`). A hiragana or katakana letter is therefore
`unassessed`, and a kanji, a mark, a ligature or punctuation is `identified`, which is what the
source gives.
"""

from __future__ import annotations

import csv
import io
import json
import shutil
import sys
import time
import warnings
import zipfile
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import NamedTuple
from urllib.parse import urlsplit

import yaml
from PIL import Image

from .. import images, production, rights, tables
from ..registry import SOURCES
from ..schema import (
    Box,
    Classification,
    Document,
    Licence,
    Page,
    ReviewState,
    Rights,
    Unit,
    UnitKind,
)
from . import codh

#: The source id of `data/sources/codh-char-shape.yaml`.
SOURCE = "codh-char-shape"
SOURCE_FILE = SOURCES / f"{SOURCE}.yaml"
#: The book list read from the CODH book page, one row per book.
BOOKS_FILE = SOURCES / "codh-books.tsv"
#: The evaluation split by book, written by `scripts/build_codh_split.py`: bid, production, split.
SPLITS_FILE = SOURCES.parent / "splits" / "codh.tsv"
#: The image service base of a page image, the key the image cache holds it under.
IIIF = "https://codh.rois.ac.jp/char-shape/iiif/{bid}/{image}.tif"
ATTRIBUTION = "『日本古典籍くずし字データセット』（国文研ほか所蔵／CODH加工）doi:10.20676/00000340"
HOLDER = "国文学研究資料館ほか"

#: Where the per-book zips and the NIJL manifests sit under the cache, and the page images under `out`.
ZIP_DIR = "codh"
MANIFEST_DIR = "codh-manifests"
IMAGE_DIR = "images"

#: The columns of `data/sources/codh-books.tsv`, after the comment lines.
COLUMNS = ("bid", "title", "code_points", "characters", "released", "type", "production", "collection", "issued")
PAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png"})
#: The seconds between the two size samples that tell a finished download from a running one.
SETTLE = 1.0
#: The number of train books of the split whose pages the default registration set holds.
TRAIN_BOOKS = 2
#: The splits the default registration set holds whole.
REGISTERED_SPLITS = ("test", "val")

#: `time.sleep`, so that a test can watch a file grow without waiting for it.
_sleep = time.sleep


class Book(NamedTuple):
    """One book of the dataset: the row of the book list, with its production."""

    bid: str
    title: str
    code_points: str
    characters: str
    released: str
    kind: str
    production: str
    collection: str
    issued: str

    @property
    def expected(self) -> int | None:
        """The published number of characters, or None when the row states none."""
        return int(self.characters) if self.characters.isdigit() else None


def book_list(path: Path | None = None) -> list[Book]:
    """Every book of the book list, in the order the file lists them.

    The header line and the comment lines start with `#`; the data lines are tab-separated and hold
    the columns of `COLUMNS`.
    """
    source = BOOKS_FILE if path is None else Path(path)
    books: list[Book] = []
    for number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip() or line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) != len(COLUMNS):
            raise ValueError(f"{source}:{number}: expected {len(COLUMNS)} columns, found {len(fields)}")
        row = dict(zip(COLUMNS, fields))
        books.append(
            Book(
                bid=row["bid"],
                title=row["title"] or row["bid"],
                code_points=row["code_points"],
                characters=row["characters"],
                released=row["released"],
                kind=row["type"],
                production=production.check(row["production"]),
                collection=row["collection"],
                issued=row["issued"],
            )
        )
    return books


def unlisted(bid: str) -> Book:
    """A book the book list does not carry, imported under its identifier."""
    return Book(
        bid=bid,
        title=bid,
        code_points="",
        characters="",
        released="",
        kind="",
        production="unknown",
        collection="",
        issued="",
    )


def zip_path(bid: str, *, root: Path | None = None) -> Path:
    """Where `{bid}.zip` lands: `cache/codh/{bid}.zip`."""
    return (images.cache_root() if root is None else Path(root)) / ZIP_DIR / f"{bid}.zip"


def manifest_of(bid: str, *, root: Path | None = None) -> dict | None:
    """The NIJL IIIF manifest of a book from `cache/codh-manifests/{bid}.json`, or None.

    The three books whose identifiers are CODH-local have no manifest, and a manifest that does not
    parse is treated as absent.
    """
    folder = (images.cache_root() if root is None else Path(root)) / MANIFEST_DIR
    path = folder / f"{bid}.json"
    if not path.is_file():
        return None
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None
    return document if isinstance(document, dict) else None


def holder_of(bid: str, *, root: Path | None = None) -> str | None:
    """The `DCTERMS.relation` of a book's manifest: the collection its scan belongs to."""
    return relation_of(manifest_of(bid, root=root))


def relation_of(manifest: dict | None) -> str | None:
    """The `DCTERMS.relation` of a manifest that is in hand, or None."""
    if manifest is None:
        return None
    fields = {
        row.get("label"): row.get("value")
        for row in manifest.get("metadata", [])
        if isinstance(row, dict)
    }
    value = fields.get("DCTERMS.relation")
    return value.strip() if isinstance(value, str) and value.strip() else None


def split_bids(path: Path | None = None) -> list[tuple[str, str]]:
    """The `(bid, split)` rows of the evaluation split file, or nothing when it is not there yet.

    The file is `data/splits/codh.tsv`, written by `scripts/build_codh_split.py`: `bid`, `title`,
    `production` and `split`, tab-separated under a header line, with comment lines above it. A file
    without a header is read as `bid`, `production` and `split`.
    """
    source = SPLITS_FILE if path is None else Path(path)
    if not source.is_file():
        return []
    lines = [
        line for line in source.read_text(encoding="utf-8").splitlines() if line.strip() and not line.startswith("#")
    ]
    if not lines:
        return []
    header = [field.strip() for field in lines[0].split("\t")]
    if "bid" in header and "split" in header:
        bid_at, split_at, body = header.index("bid"), header.index("split"), lines[1:]
    else:
        bid_at, split_at, body = 0, 2, lines
    rows: list[tuple[str, str]] = []
    for line in body:
        fields = line.split("\t")
        if len(fields) <= max(bid_at, split_at):
            continue
        rows.append((fields[bid_at].strip(), fields[split_at].strip()))
    return rows


def default_pages(pages: list[Page], splits: list[tuple[str, str]] | None = None) -> list[Page]:
    """The pages the default registration set holds: a bounded sample of the corpus.

    Every page of the test and validation books of the split, and of the first `TRAIN_BOOKS` train
    books, since the split is by book. The pages of the whole corpus are about 26 GB, which the
    detector work reads through the image cache rather than from the imported tables.
    """
    rows = split_bids() if splits is None else splits
    chosen = [bid for bid, split in rows if split in REGISTERED_SPLITS]
    chosen += [bid for bid, split in rows if split == "train"][:TRAIN_BOOKS]
    documents = {f"codh:{bid}" for bid in chosen}
    return [page for page in pages if page.document_id in documents]


def settled(path: Path, *, interval: float | None = None, sleep: Callable[[float], None] | None = None) -> bool:
    """Whether a file's size is the same in two samples an interval apart.

    A download that is still running grows between the samples, and a zip that is still growing is
    not read. `interval` defaults to `SETTLE` seconds; `sleep` is the waiter, which a test replaces
    to grow the file instead of waiting.
    """
    pause = SETTLE if interval is None else interval
    waiter = _sleep if sleep is None else sleep
    before = path.stat().st_size
    if pause > 0:
        waiter(pause)
    return path.stat().st_size == before


def source_rights() -> Rights:
    """The rights of the records, from `data/sources/codh-char-shape.yaml`."""
    raw = yaml.safe_load(SOURCE_FILE.read_text(encoding="utf-8"))
    checked = rights.resolve(licence=raw.get("licence"), url=raw.get("licence_evidence")).checked
    return Rights(
        licence=Licence(raw["licence"]),
        holder=HOLDER,
        attribution=raw["attribution"],
        evidence=raw.get("licence_evidence"),
        checked=checked,
    )


def document_of(book: Book, record_rights: Rights, manifest: dict | None = None) -> Document:
    """The document of one book.

    The title and the production type come from the book list, the NIJL 書誌ID from the manifest
    that resolves for the book, and the holder from that manifest's `DCTERMS.relation`. The fields
    of the book list with no column of their own go to `meta`.
    """
    identifier = f"codh:{book.bid}"
    refs = {SOURCE: book.bid}
    if manifest is not None:
        refs["nijl-bid"] = book.bid
    meta = {
        name: value
        for name, value in (
            ("released", book.released),
            ("type", book.kind),
            ("code_points", book.code_points),
            ("characters", book.characters),
            ("issued", book.issued),
        )
        if value
    }
    return Document(
        id=identifier,
        title=book.title or book.bid,
        origin="japan",
        source_refs=refs,
        holder=relation_of(manifest),
        production=book.production,
        image_rights=record_rights,
        text_rights=record_rights,
        meta=meta,
    )


def classification_of(code_point: int) -> Classification:
    """`unassessed` for a kana letter, whose hentaigana form the source does not record.

    Kanji classes spanning several curated written forms stay unassessed too.
    """
    return Classification.UNASSESSED if is_kana(code_point) else codh.classification_of(code_point)


def is_kana(code_point: int) -> bool:
    """Whether a code point is a hiragana or katakana letter."""
    return 0x3041 <= code_point <= 0x3096 or 0x30A1 <= code_point <= 0x30FA


def read(
    zip_path: Path,
    book: Book,
    *,
    images_dir: Path,
    manifest: dict | None = None,
    known: dict[str, images.ImageRecord] | None = None,
) -> tuple[Document, list[Page], list[Unit]]:
    """Read one book's archive into records, writing its page images under `images_dir`."""
    with zipfile.ZipFile(zip_path) as archive:
        return read_archive(archive, book, images_dir=images_dir, manifest=manifest, known=known)


def read_archive(
    archive: zipfile.ZipFile,
    book: Book,
    *,
    images_dir: Path,
    manifest: dict | None = None,
    known: dict[str, images.ImageRecord] | None = None,
) -> tuple[Document, list[Page], list[Unit]]:
    """Read one book out of an open archive into its document, its pages and its units.

    The archive is a per-book zip or the one archive that holds every book, in which case the book's
    files are the ones under `{bid}/`. A page named by a CSV but absent from `images/` still becomes
    a page, with no size and a warning. `known` is the image cache index by URL; an image it holds
    is not written out again, its size and checksum being read from the cache.
    """
    prefix = _prefix(archive, book.bid)
    rows = _rows(archive, _coordinate_member(archive, prefix, book.bid))
    reports = _rows(archive, _report_member(archive, prefix, book.bid))
    index = registered() if known is None else known
    found = _extract_images(archive, prefix, book.bid, images_dir, index)
    document = document_of(book, source_rights(), manifest)
    return document, _pages(book, rows, reports, found), [*_units(book, rows, found), *_reports(book, reports)]


def import_all(
    out: Path,
    *,
    books: list[str] | None = None,
    from_zip: Path | None = None,
    limit: int | None = None,
    register: bool = False,
) -> dict[str, int]:
    """Import every book that is in the cache into `out` and return what was written.

    The books come from `data/sources/codh-books.tsv`, or from `books` when it is given; a book it
    does not list is imported under its identifier. Each book is read from `cache/codh/{bid}.zip`,
    or from the one archive `from_zip` holds every book in. A book whose zip is not there yet, or
    whose size still changes between two samples a second apart, is skipped with a warning, so a run
    over a partial download is useful and a later run picks up the rest. `limit` stops after that
    many books have been read.

    `documents`, `pages` and `units` are written and merged. A page image that is not in the cache is
    written under `out/images/`, and an image the cache already holds is left alone; `register=True`
    runs the `register_images` pass at the end of the call, which registers the default bounded set
    of pages and drops their staged copies, and running `register_images` on its own does the same
    after the tables are written.

    The returned mapping holds the row counts of `documents` and `pages`, the number of `units`
    character units, the number of `reports` units counted apart (the units table holds both), the
    number of `kana_unassessed` units, and the number of `books` imported. `MANIFEST.json` is written
    by the merge.
    """
    out = Path(out)
    (out / IMAGE_DIR).mkdir(parents=True, exist_ok=True)
    listing = {book.bid: book for book in book_list()}
    wanted = [listing.get(bid) or unlisted(bid) for bid in (books if books is not None else list(listing))]
    record_rights = source_rights()
    known = registered()
    documents: list[Document] = []
    pages: list[Page] = []
    units: list[Unit] = []
    imported = 0

    if from_zip is not None:
        source = Path(from_zip)
        if not source.is_file():
            warnings.warn(f"{source}: the archive is not there; nothing imported", UserWarning, stacklevel=2)
        elif not settled(source):
            warnings.warn(
                f"{source}: size changed between two samples a second apart; left for the next run",
                UserWarning,
                stacklevel=2,
            )
        else:
            with zipfile.ZipFile(source) as archive:
                names = set(archive.namelist())
                for book in wanted:
                    if limit is not None and imported >= limit:
                        break
                    if not any(name.startswith(f"{book.bid}/") for name in names):
                        warnings.warn(f"{source}: no {book.bid}/ in the archive; skipped", UserWarning, stacklevel=2)
                        continue
                    imported += _collect(book, archive, out, record_rights, known, documents, pages, units)
    else:
        for book in wanted:
            if limit is not None and imported >= limit:
                break
            path = zip_path(book.bid)
            if not path.is_file():
                warnings.warn(f"{path}: not in the cache yet; {book.bid} skipped", UserWarning, stacklevel=2)
                continue
            if not settled(path):
                warnings.warn(
                    f"{path}: size changed between two samples a second apart; {book.bid} left for the next run",
                    UserWarning,
                    stacklevel=2,
                )
                continue
            with zipfile.ZipFile(path) as archive:
                imported += _collect(book, archive, out, record_rights, known, documents, pages, units)

    tables.write(out / "documents.parquet", documents, Document)
    tables.write(out / "pages.parquet", pages, Page)
    tables.write(out / "units.parquet", units, Unit)
    written = tables.Dataset(out).merge([], out, command=_command(books, from_zip, limit))
    reports = sum(1 for unit in units if unit.kind is UnitKind.UNREADABLE)
    counts = {
        "documents": written.get("documents", 0),
        "pages": written.get("pages", 0),
        "units": len(units) - reports,
        "reports": reports,
        "kana_unassessed": sum(
            1
            for unit in units
            if unit.kind is not UnitKind.UNREADABLE and unit.classification is Classification.UNASSESSED
        ),
        "books": imported,
    }
    if register:
        counts.update(register_images(out))
    return counts


def register_images(
    out: Path,
    *,
    limit: int | None = None,
    pages: list[str] | None = None,
) -> dict[str, int]:
    """Put a bounded set of the page images of `out/images` in the image cache.

    `pages` names the pages to register, each as a page id, as an image name or as a document id;
    without it the set is `default_pages`: every page of the test and validation books of
    `data/splits/codh.tsv` and of a sample of its train books, and a warning when that file is not
    there yet. `limit` stops after that many pages have been looked at, cached ones included.

    Every selected page is looked up under its CODH IIIF URL. A page whose image is already in the
    cache is skipped, and its `sha256` and any size it lacks are written back into the pages table;
    the file of a page that is not cached yet is registered under that URL through
    `images.register`. The staged copy of every page image the cache now holds is then deleted, the
    pages table having been written back first, so only what is not cached stays under `out/images/`.

    The returned mapping holds the number of `pages` of the table, of pages `selected`, `registered`,
    `skipped` and with a `missing` image file, the `bytes` registered, the staged files `deleted` and
    the `freed` bytes, and the bytes `kept` in the `kept_files` staged files that are not cached.
    """
    out = Path(out)
    table = out / "pages.parquet"
    if not table.is_file():
        raise FileNotFoundError(f"{table}: no pages table; import the books first")
    records = list(tables.read(table, Page))
    selected = _selected_pages(records, pages)
    folder = out / IMAGE_DIR
    known = registered()
    counts = {
        "pages": len(records),
        "selected": len(selected),
        "registered": 0,
        "skipped": 0,
        "missing": 0,
        "bytes": 0,
    }
    changed = False
    for page in sorted(selected, key=lambda record: record.id):
        seen = counts["registered"] + counts["skipped"] + counts["missing"]
        if limit is not None and seen >= limit:
            break
        cached = known.get(page.image)
        if cached is not None:
            counts["skipped"] += 1
            changed |= _fill(page, cached)
            continue
        file = staged(folder, page)
        if file is None:
            counts["missing"] += 1
            continue
        record = images.register(file, page.image)
        known[page.image] = record
        counts["registered"] += 1
        counts["bytes"] += record.bytes
        changed |= _fill(page, record)
    if changed:
        tables.write(table, records, Page)
    counts.update(_drop_staged(folder, records, known))
    return counts


def _drop_staged(folder: Path, records: list[Page], known: dict[str, images.ImageRecord]) -> dict[str, int]:
    """Delete the staged copy of every page image the cache now holds.

    The pages table is written back before this, so a size or a checksum that only the staged file
    could give is already on the page. What is not in the cache stays under `out/images/`, and its
    size is returned in `kept`.
    """
    root = images.images_root()
    dropped = {"deleted": 0, "freed": 0, "kept_files": 0, "kept": 0}
    for page in records:
        file = staged(folder, page)
        if file is None:
            continue
        if not _cached_file(page.image, known, root):
            dropped["kept_files"] += 1
            dropped["kept"] += file.stat().st_size
            continue
        dropped["freed"] += file.stat().st_size
        file.unlink()
        dropped["deleted"] += 1
    return dropped


def staged(folder: Path, page: Page) -> Path | None:
    """The staged file of a page under `out/images`, or None when it is not there."""
    image = image_of(page)
    for candidate in (folder / f"{image}.jpg", folder / f"{image}.jpeg", folder / f"{image}.png", folder / image):
        if candidate.is_file():
            return candidate
    return None


def _cached_file(url: str, known: dict[str, images.ImageRecord], root: Path) -> bool:
    """Whether the image cache holds the file of a URL, the question `images.path_for` answers."""
    record = known.get(url)
    if record is None:
        return False
    return any(entry.is_file() for entry in (root / record.sha256[:2]).glob(f"{record.sha256}.*"))


def _selected_pages(records: list[Page], pages: list[str] | None) -> list[Page]:
    """The pages a registration pass works on: the ones asked for, or the default bounded set."""
    if pages is None:
        splits = split_bids()
        if not splits:
            warnings.warn(
                f"{SPLITS_FILE}: the split file is not there yet, so no page was selected to register; "
                f"name the pages to register",
                UserWarning,
                stacklevel=3,
            )
        return default_pages(records, splits)
    asked = set(pages)
    found = [
        page
        for page in records
        if page.id in asked or image_of(page) in asked or (page.document_id or "") in asked
    ]
    unknown = asked - {page.id for page in found} - {image_of(page) for page in found}
    unknown -= {page.document_id for page in found if page.document_id is not None}
    if unknown:
        warnings.warn(
            f"{len(unknown)} of the {len(asked)} pages asked for are not in the pages table: "
            f"{', '.join(sorted(unknown)[:5])}",
            UserWarning,
            stacklevel=3,
        )
    return found


def registered(*, root: Path | None = None) -> dict[str, images.ImageRecord]:
    """The current row of every URL of the image cache index, by URL."""
    return {row.url: row for row in images.index(root) if row.superseded_by is None}


def image_of(page: Page) -> str:
    """The name of a page's image file: the last segment of its image URL without the suffix."""
    return Path(urlsplit(page.image).path).stem


def _fill(page: Page, record: images.ImageRecord) -> bool:
    """Copy what the cache knows about an image into its page; True when the page changed."""
    changed = False
    if page.sha256 != record.sha256:
        page.sha256 = record.sha256
        changed = True
    if not page.width or not page.height:
        page.width, page.height = record.width, record.height
        changed = True
    return changed


def _collect(
    book: Book,
    archive: zipfile.ZipFile,
    out: Path,
    record_rights: Rights,
    known: dict[str, images.ImageRecord],
    documents: list[Document],
    pages: list[Page],
    units: list[Unit],
) -> int:
    """Read one book into the tables in hand, print what it held and return 1.

    An image already in the cache fills the `sha256` of its page, so a reread of a book the images
    of which are registered writes the page whole.
    """
    document, book_pages, book_units = read_archive(
        archive, book, images_dir=out / IMAGE_DIR, manifest=manifest_of(book.bid), known=known
    )
    for page in book_pages:
        cached = known.get(page.image)
        if cached is not None:
            _fill(page, cached)
    characters = sum(1 for unit in book_units if unit.kind is not UnitKind.UNREADABLE)
    _check_count(book, characters)
    documents.append(document)
    pages.extend(book_pages)
    units.extend(book_units)
    print(f"{book.bid}: {len(book_pages)} pages, {characters} characters", file=sys.stderr, flush=True)
    return 1


def _check_count(book: Book, characters: int) -> None:
    """Warn when a book holds a different number of characters than the book list publishes."""
    expected = book.expected
    if expected is not None and expected != characters:
        warnings.warn(
            f"{book.bid}: {characters} character rows in the coordinate CSV, {expected} on the book list",
            UserWarning,
            stacklevel=3,
        )


def _command(books: list[str] | None, from_zip: Path | None, limit: int | None) -> str:
    """The command line the merge records in `MANIFEST.json`."""
    parts = ["atlas import codh --all"]
    if books:
        parts.append(f"--books {','.join(books)}")
    if from_zip is not None:
        parts.append(f"--from-zip {from_zip}")
    if limit is not None:
        parts.append(f"--limit {limit}")
    return " ".join(parts)


def _prefix(archive: zipfile.ZipFile, bid: str) -> str:
    """The directory a book's files sit in, `{bid}/` as the dataset publishes them, else empty."""
    return f"{bid}/" if any(name.startswith(f"{bid}/") for name in archive.namelist()) else ""


def _coordinate_member(archive: zipfile.ZipFile, prefix: str, bid: str) -> str:
    """The coordinate CSV of a book, which every archive holds under its own name."""
    name = _member(archive, prefix, f"{bid}_coordinate.csv")
    if name is not None:
        return name
    candidates = [entry for entry in archive.namelist() if Path(entry).name.endswith("_coordinate.csv")]
    if len(candidates) == 1:
        return candidates[0]
    raise ValueError(f"{archive.filename}: no {bid}_coordinate.csv in the archive")


def _report_member(archive: zipfile.ZipFile, prefix: str, bid: str) -> str | None:
    """The report CSV of a book, or None: only some books carry one."""
    return _member(archive, prefix, f"{bid}_report.csv")


def _member(archive: zipfile.ZipFile, prefix: str, name: str) -> str | None:
    """The member that holds one file of a book, wherever the archive wraps it."""
    names = archive.namelist()
    for candidate in (f"{prefix}{name}", name):
        if candidate in names:
            return candidate
    matches = [entry for entry in names if not entry.endswith("/") and PurePosixPath(entry).name == name]
    return matches[0] if len(matches) == 1 else None


def _rows(archive: zipfile.ZipFile, name: str | None) -> list[dict[str, str]]:
    """Every row of one CSV of the archive, with a BOM and the spaces of its column names removed."""
    if name is None:
        return []
    with archive.open(name) as handle:
        reader = csv.DictReader(io.TextIOWrapper(handle, encoding="utf-8-sig"))
        reader.fieldnames = [field.strip() for field in reader.fieldnames or []]
        return [
            {key: (value or "").strip() for key, value in row.items() if key is not None}
            for row in reader
        ]


def _extract_images(
    archive: zipfile.ZipFile,
    prefix: str,
    bid: str,
    images_dir: Path,
    known: dict[str, images.ImageRecord],
) -> dict[str, tuple[Path | None, int, int]]:
    """Write the page images of a book under `images_dir` and return them by image name.

    A file that is already there with the size the archive states is left alone, so a second import
    writes no image again, and an image the cache holds is not written at all: its size comes from
    the cache row. The crops under `characters/` are not read.
    """
    images_dir.mkdir(parents=True, exist_ok=True)
    found: dict[str, tuple[Path | None, int, int]] = {}
    for entry in archive.infolist():
        if entry.is_dir() or not entry.filename.startswith(prefix):
            continue
        name = PurePosixPath(entry.filename)
        if name.parent.name != IMAGE_DIR or name.suffix.lower() not in PAGE_SUFFIXES:
            continue
        cached = known.get(IIIF.format(bid=bid, image=name.stem))
        if cached is not None:
            found[name.stem] = (None, cached.width, cached.height)
            continue
        target = images_dir / name.name
        if not target.is_file() or target.stat().st_size != entry.file_size:
            with archive.open(entry) as source, target.open("wb") as sink:
                shutil.copyfileobj(source, sink)
        found[name.stem] = (target, *_image_size(target))
    return found


def _image_size(path: Path) -> tuple[int, int]:
    """The pixel size of a page image, or 0 by 0 with a warning when it cannot be read."""
    try:
        with Image.open(path) as image:
            return image.width, image.height
    except Exception as exc:  # noqa: BLE001 - Pillow raises a family of errors for a file that is not an image
        warnings.warn(f"{path}: cannot read the page image ({exc})", UserWarning, stacklevel=3)
        return 0, 0


def _pages(
    book: Book,
    rows: list[dict[str, str]],
    reports: list[dict[str, str]],
    sizes: dict[str, tuple[Path | None, int, int]],
) -> list[Page]:
    """One page per image of a book, plus a sizeless page for an image a CSV names without one."""
    named = {row["Image"] for row in rows} | {row["Image"] for row in reports} | set(sizes)
    named.discard("")
    missing = sorted(image for image in named if image not in sizes)
    if missing:
        warnings.warn(
            f"{len(missing)} pages of {book.bid} have no image in the archive: {', '.join(missing)}",
            UserWarning,
            stacklevel=4,
        )
    pages = []
    for position, image in enumerate(sorted(named), start=1):
        found = sizes.get(image)
        width, height = (found[1], found[2]) if found is not None else (0, 0)
        pages.append(
            Page(
                id=f"codh:{book.bid}:{image}",
                document_id=f"codh:{book.bid}",
                seq=page_seq(image, position),
                image=IIIF.format(bid=book.bid, image=image),
                width=width,
                height=height,
                transcription={"source": SOURCE, "entry": book.bid},
            )
        )
    return pages


def page_seq(image: str, fallback: int) -> int:
    """The reading order of a page: `{bid}_00003_1` -> 5.

    The two halves of a photographed spread are numbered together, so `_1` takes the odd and `_2`
    the even place; an image whose name does not carry a page and a half keeps its position.
    """
    match = codh.IMAGE_NAME.match(image)
    if match is None:
        return fallback
    return int(match["page"]) * 2 - (2 - int(match["half"]))


def _units(book: Book, rows: list[dict[str, str]], sizes: dict[str, tuple[Path | None, int, int]]) -> list[Unit]:
    """One unit per row of the coordinate CSV, as the one-book importer makes them.

    A box that reaches past its page image is cut at the edge, and the coordinates upstream states
    stay in its `upstream["box"]`, since the tables validate that a box lies inside its page.
    """
    units = []
    cut = 0
    for row in rows:
        image = row["Image"]
        code_point = int(row["Unicode"].removeprefix("U+"), 16)
        char = chr(code_point)
        block, char_id = row["Block ID"], row["Char ID"]
        found = sizes.get(image)
        box, clipped = box_of(row, (found[1], found[2]) if found is not None else (0, 0))
        cut += clipped
        upstream = {"source": SOURCE, "ref": f"{book.bid}/{image}/{block}/{char_id}", "block": block,
                    "identity_basis": "normalized_transcription", "source_code_point": f"U+{code_point:04X}",
                    "normalization_evidence": "https://codh.rois.ac.jp/char-shape/#version"}
        if clipped:
            upstream["box"] = ",".join(row[column] for column in ("X", "Y", "Width", "Height"))
        units.append(
            Unit(
                id=f"codh:{book.bid}:{image}:{block}:{char_id}",
                document_id=f"codh:{book.bid}",
                page_id=f"codh:{book.bid}:{image}",
                line_id=None,
                seq=None,
                box=box,
                kind=codh.kind_of(code_point),
                text_source=char,
                reading=char,
                unicode=f"U+{code_point:04X}",
                script=codh.script_of(code_point),
                classification=classification_of(code_point),
                method="import",
                review=ReviewState.TRANSCRIBER,
                upstream=upstream,
            )
        )
    if cut:
        warnings.warn(
            f"{cut} boxes of {book.bid} reach past their page image and were cut at its edge; "
            f"the coordinates upstream states are in upstream[\"box\"]",
            UserWarning,
            stacklevel=3,
        )
    return units


def box_of(row: dict[str, str], size: tuple[int, int]) -> tuple[Box | None, bool]:
    """The box of one coordinate row, cut where it reaches past the page image.

    `codh:200003076:200003076_00027_2:B0001:C0017` of the 63,959 units of `200003076` is stated as
    2035,2368,129,122 on a 2159×3158 page, 5 px past the right edge, and the tables validate that a
    box lies inside its page. A box that reaches past the edge is therefore cut there and reported; a
    box that lies outside the page altogether keeps no box at all, its position staying in
    `upstream`. The second value says whether the box was cut.
    """
    x, y, w, h = int(row["X"]), int(row["Y"]), int(row["Width"]), int(row["Height"])
    width, height = size
    if not width or not height:
        return Box(x=x, y=y, w=w, h=h), False
    left, top = max(x, 0), max(y, 0)
    right, bottom = min(x + w, width), min(y + h, height)
    if (left, top, right - left, bottom - top) == (x, y, w, h):
        return Box(x=x, y=y, w=w, h=h), False
    if right <= left or bottom <= top:
        return None, True
    return Box(x=left, y=top, w=right - left, h=bottom - top), True


def _reports(book: Book, rows: list[dict[str, str]]) -> list[Unit]:
    """One unit per row of the report CSV: a note on a spot of a page, not a character.

    The CSV states the position of the note as a point, so the unit carries no box; the point and
    the note stay in `upstream`. `n` counts the reports of one image from one.
    """
    units = []
    seen: dict[str, int] = {}
    for row in rows:
        image = row.get("Image", "")
        if not image:
            warnings.warn(f"{book.bid}: a report row names no image; skipped", UserWarning, stacklevel=3)
            continue
        seen[image] = seen.get(image, 0) + 1
        number = seen[image]
        units.append(
            Unit(
                id=f"codh:{book.bid}:{image}:report:{number}",
                document_id=f"codh:{book.bid}",
                page_id=f"codh:{book.bid}:{image}",
                kind=UnitKind.UNREADABLE,
                text_source=None,
                reading=None,
                unicode=None,
                method="import",
                review=ReviewState.REJECTED,
                upstream={
                    "source": SOURCE,
                    "ref": f"{book.bid}/{image}/report/{number}",
                    "report": row.get("Report", ""),
                    "x": row.get("X", ""),
                    "y": row.get("Y", ""),
                },
            )
        )
    return units
