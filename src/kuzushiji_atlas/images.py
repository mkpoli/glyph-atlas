"""IIIF image services and the local image cache.

`service_of` turns an Image API request into the service base it belongs to, `info` reads `info.json`
for the pixel size, the API version and the tile sizes, and `full_url` builds the full-size request
for a version. `fetch` downloads a full-size image into `cache/images/<sha256[:2]>/<sha256>.<ext>`
and records it in `cache/images/index.parquet`; `register` puts a file that is already on disk in the
same cache under a URL key without a request, and `crop` reads a cached image and cuts a box from it
without writing anything.

Rows are keyed by checksum and never updated in place. A refetch whose image changed appends a new
row and marks the old one with `superseded_by`, so a checksum that a table already refers to stays
readable.

The cache directory is `cache/images` of the repository, or `$KUZUSHIJI_ATLAS_CACHE/images`, or the
`root` argument. The index keeps its model here rather than in `schema.py`, and goes to disk through
`tables.write` like every other table. The module reads and writes no dataset table of its own; the
three functions the command line calls, `fill_sizes`, `fetch_pages` and `write_crops`, take a pages
or units table as an argument and write back only what they were given.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from . import net, tables
from .schema import Box, Page, Unit

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_CACHE = "KUZUSHIJI_ATLAS_CACHE"
CHUNK = 1 << 20

IMAGE_FORMATS = frozenset({".jpg", ".jpeg", ".png", ".tif", ".tiff", ".gif", ".webp", ".jp2", ".bmp"})
MEDIA_SUFFIX = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/tiff": ".tif",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/jp2": ".jp2",
    "image/jpeg2000": ".jp2",
}

FORMAT = r"(?:jpg|jpeg|png|gif|webp|tif|tiff|jp2|bmp)"
QUALITY = r"(?:default|native|color|gray|grey|bitonal)"
REGION = r"(?:full|square|\d+,\d+,\d+,\d+|pct:\d+(?:\.\d+)?)"
SIZE = r"(?:full|max|square|\d+,|,\d+|\d+,\d+|pct:\d+(?:\.\d+)?|!\d+,\d+)"
ROTATION = r"!?\d+(?:\.\d+)?"
REQUEST_SUFFIX = re.compile(rf"/{REGION}/{SIZE}/{ROTATION}/{QUALITY}\.{FORMAT}$", re.IGNORECASE)
INFO_SUFFIX = re.compile(r"/info\.json$", re.IGNORECASE)
#: Path segments that mark an Image API service, so that a bare service URL is not read as a file.
IIIF_MARKERS = ("iiif", "image-api", "imageapi")


class ImageError(RuntimeError):
    """An image that cannot be served from the cache."""


class ImageRecord(BaseModel):
    """One image in the cache: a downloaded full-size image, or a local file registered under a key."""

    model_config = ConfigDict(extra="forbid")

    url: str = Field(description="the URL it was fetched from, or the key a local file was registered under")
    service: str | None = Field(
        default=None, description="Image API service base, when the URL belongs to one"
    )
    sha256: str = Field(description="checksum of the file, which is also its name in the cache")
    width: int = Field(description="pixels of the stored image, which is a crop when a box was asked for")
    height: int
    bytes: int = Field(description="size of the stored file")
    fetched_at: datetime
    etag: str | None = None
    last_modified: str | None = None
    superseded_by: str | None = Field(default=None, description="sha256 of the row that replaced this one")


def service_of(url: str) -> str | None:
    """The Image API service base of `url`, or None when it is a plain file URL.

    A request suffix (`/{region}/{size}/{rotation}/{quality}.{format}`), an `info.json` suffix, a
    query string and a trailing slash are removed. A URL whose path carries a IIIF marker and no
    request suffix is a service base itself, which is how the `.../{bid}/{image}.tif` services of CODH
    are read. Anything else is a file that is fetched as it stands.
    """
    base = url.split("#", 1)[0].split("?", 1)[0].rstrip("/")
    if not base:
        return None
    for pattern in (INFO_SUFFIX, REQUEST_SUFFIX):
        found = pattern.search(base)
        if found:
            return base[: found.start()].rstrip("/") or None
    parts = urlsplit(base)
    segments = [segment.lower() for segment in parts.path.split("/") if segment]
    if "iiif" in (parts.hostname or "").lower() or any(
        marker in segment for segment in segments for marker in IIIF_MARKERS
    ):
        return base
    return None


def info(
    service: str,
    *,
    client: httpx.Client | None = None,
    pause: float | None = None,
    clock: Callable[[], float] | None = None,
    sleeper: Callable[[float], None] | None = None,
) -> dict:
    """Read `info.json` of a service: `width`, `height`, `version` (1, 2 or 3), `tiles` and `sizes`.

    Image API 1 has no `sizes` and states its version in `@context`; it describes its tiles with
    `tile_width`, `tile_height` and `scale_factors`, which are returned in the shape version 2 uses.
    """
    base = service_of(service)
    if base is None:
        raise ImageError(f"{service}: not an Image API service URL")
    url = f"{base}/info.json"
    with tempfile.TemporaryDirectory(prefix="kuzushiji-info-") as scratch:
        path = Path(scratch) / "info.json"
        net.download(url, path, expected="json", client=client, pause=pause, clock=clock, sleeper=sleeper)
        document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ImageError(f"{url}: info.json is not a JSON object")
    width, height = document.get("width"), document.get("height")
    if not isinstance(width, int) or not isinstance(height, int):
        raise ImageError(f"{url}: info.json states no width and height")
    sizes = document.get("sizes")
    return {
        "width": width,
        "height": height,
        "version": _version_of(document),
        "tiles": _tiles_of(document),
        "sizes": [dict(size) for size in sizes if isinstance(size, dict)] if isinstance(sizes, list) else [],
    }


def full_url(service: str, version: int | str) -> str:
    """The full-size image request of a service: `/full/max/0/default.jpg` for 3, `full` for 1 and 2."""
    base = service_of(service) or service.split("#", 1)[0].split("?", 1)[0].rstrip("/")
    return f"{base}/full/{_size_keyword(version)}/0/default.jpg"


def fetch(
    url: str,
    *,
    box: Box | None = None,
    client: httpx.Client | None = None,
    refresh: bool = False,
    root: Path | None = None,
    pause: float | None = None,
    clock: Callable[[], float] | None = None,
    sleeper: Callable[[float], None] | None = None,
) -> ImageRecord:
    """Download the image at `url` into the cache and return its row.

    A service base is resolved through `info.json` to the full-size request of its API version, and a
    URL that already names a request is downloaded as it stands. `box` asks the service for that
    region instead of the whole image, and the row then describes the region. A URL already in the
    index is returned without a request unless `refresh` is set.
    """
    cache = images_root(root)
    if not refresh:
        known = _current(cache, url)
        if known is not None and _file_for(cache, known).exists():
            return known

    service = service_of(url)
    if box is not None and service is None:
        raise ImageError(f"{url}: a box needs an Image API service URL")
    version = None
    if service is not None and (box is not None or _is_bare(url, service)):
        version = info(service, client=client, pause=pause, clock=clock, sleeper=sleeper)["version"]
    request_url = _request_url(url, service=service, version=version, box=box)

    scratch = cache / ".tmp"
    scratch.mkdir(parents=True, exist_ok=True)
    temporary = scratch / uuid4().hex
    seen: dict[str, Any] = {}
    try:
        path = net.download(
            request_url,
            temporary,
            expected="image",
            client=client,
            refresh=True,
            pause=pause,
            clock=clock,
            sleeper=sleeper,
            meta=seen,
        )
        digest, size = _digest(path)
        target = cache / digest[:2] / f"{digest}{_extension(request_url, seen.get('content_type'))}"
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            path.unlink()
        else:
            os.replace(path, target)
        width, height = _image_size(target)
    finally:
        for leftover in (temporary, temporary.with_name(temporary.name + net.PART_SUFFIX)):
            leftover.unlink(missing_ok=True)

    record = ImageRecord(
        url=url,
        service=service,
        sha256=digest,
        width=width,
        height=height,
        bytes=size,
        fetched_at=datetime.now(UTC),
        etag=seen.get("etag"),
        last_modified=seen.get("last_modified"),
    )
    _upsert(cache, record)
    return record


def register(path: Path, url: str, **fields: Any) -> ImageRecord:
    """Put `path`, a file that is already on disk, in the cache under `url`, without a request.

    The file is copied to `cache/images/<sha256[:2]>/<sha256>.<ext>`, so that a later `fetch` of `url`
    finds it. `fields` are the remaining `ImageRecord` fields and fill in what the file cannot say:
    `etag`, `last_modified`, `fetched_at`, or `width` and `height` for a file Pillow cannot open.
    `root` names the cache directory.
    """
    root = fields.pop("root", None)
    cache = images_root(root)
    source = Path(path)
    if not source.is_file():
        raise ImageError(f"{source}: no such file")
    digest, size = _digest(source)
    if fields.get("width") is None or fields.get("height") is None:
        width, height = _image_size(source)
        fields.setdefault("width", width)
        fields.setdefault("height", height)
    suffix = source.suffix.lower()
    target = cache / digest[:2] / f"{digest}{suffix if suffix in IMAGE_FORMATS else '.bin'}"
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        shutil.copyfile(source, target)

    values: dict[str, Any] = {
        "url": url,
        "service": service_of(url),
        "sha256": digest,
        "bytes": size,
        "fetched_at": datetime.now(UTC),
    }
    values.update(fields)
    record = ImageRecord(**values)
    _upsert(cache, record)
    return record


def path_for(url: str, *, root: Path | None = None) -> Path | None:
    """The cached file of `url`, or None.

    A URL is matched as it stands first, then through the service it belongs to, so that a page which
    names a service base finds the full-size image fetched from a request of that service.
    """
    cache = images_root(root)
    rows = [row for row in index(cache) if row.superseded_by is None]
    found = [row for row in rows if row.url == url]
    if not found:
        service = service_of(url)
        found = [row for row in rows if service is not None and row.service == service]
    for row in reversed(found):
        path = _file_for(cache, row)
        if path.exists():
            return path
    return None


def crop(url: str, box: Box, *, root: Path | None = None) -> Image.Image:
    """Cut `box` out of the cached image of `url` and return it. Nothing is written.

    The box is clamped to the image; a box that lies outside it fails. The full-size image has to be
    in the cache already, through `fetch` or `register`.
    """
    path = path_for(url, root=root)
    if path is None:
        raise ImageError(f"{url}: not in the image cache")
    with Image.open(path) as image:
        left, top = max(0, box.x), max(0, box.y)
        right, bottom = min(image.width, box.x + box.w), min(image.height, box.y + box.h)
        if right <= left or bottom <= top:
            size = f"{image.width}x{image.height}"
            raise ImageError(f"{url}: box {box.x},{box.y},{box.w},{box.h} lies outside the {size} image")
        return image.crop((left, top, right, bottom))


def index(root: Path | None = None) -> list[ImageRecord]:
    """Every row of the cache index, superseded rows included, oldest first."""
    return _read_index(images_root(root))


def fill_sizes(pages: Path, *, limit: int | None = None) -> tuple[int, int]:
    """Fill `width` and `height` of a pages table from each page's IIIF service.

    A page whose `image` is a service base is read through `info.json`; a page that is already sized
    is left alone, so the command can be rerun. Returns the number of pages filled and the number
    that could not be read.
    """
    records = _pages_of(pages)
    filled = failed = 0
    for page in records:
        if page.width and page.height:
            continue
        service = service_of(page.image)
        if service is None:
            failed += 1
            continue
        try:
            found = info(service)
        except (ImageError, net.DownloadError):
            failed += 1
            continue
        page.width, page.height = int(found["width"]), int(found["height"])
        filled += 1
        if limit is not None and filled >= limit:
            break
    if filled:
        tables.write(_table_path(pages, "pages"), records, Page)
    return filled, failed


def fetch_pages(
    pages: Path,
    *,
    limit: int | None = None,
    document: str | None = None,
    pages_filter: list[str] | None = None,
) -> tuple[int, int, int]:
    """Fetch the full-size image of every page of a pages table into the cache.

    Pages already in the cache are skipped without a request, and a page that had no size of its own
    takes the size of the image. `limit` counts the pages fetched and skipped together. Returns the
    numbers fetched, skipped and failed.
    """
    records = _pages_of(pages)
    wanted = set(pages_filter) if pages_filter else None
    fetched = skipped = failed = 0
    for page in records:
        if document is not None and page.document_id != document:
            continue
        if wanted is not None and page.id not in wanted:
            continue
        if limit is not None and fetched + skipped >= limit:
            break
        try:
            if path_for(page.image) is not None:
                skipped += 1
                continue
            record = fetch(page.image)
        except (ImageError, net.DownloadError):
            failed += 1
            continue
        fetched += 1
        if not page.width or not page.height:
            page.width, page.height = record.width, record.height
    if fetched:
        tables.write(_table_path(pages, "pages"), records, Page)
    return fetched, skipped, failed


def write_crops(
    units: Path,
    out: Path,
    *,
    limit: int | None = None,
    images: dict[str, str] | None = None,
) -> tuple[int, int]:
    """Write one JPEG per unit that has a box, named after the unit id with `:` replaced by `_`.

    `units` is a units table, or a dataset directory that holds one beside its pages, from which the
    image URL of each page is read. `images` supplies page id to image URL for a table that travels
    alone. A unit with no box, whose page image is not cached, or whose crop is already on disk, is
    skipped and counted. Returns the numbers written and skipped.
    """
    records, page_images = _units_of(units, images)
    out.mkdir(parents=True, exist_ok=True)
    written = skipped = 0
    for unit in records:
        if unit.box is None:
            skipped += 1
            continue
        if limit is not None and written >= limit:
            break
        url = page_images.get(unit.page_id or "")
        if not url:
            skipped += 1
            continue
        target = out / f"{unit.id.replace(':', '_')}.jpg"
        if target.exists():
            skipped += 1
            continue
        try:
            crop(url, unit.box).convert("RGB").save(target, format="JPEG", quality=95)
        except (ImageError, OSError):
            skipped += 1
            continue
        written += 1
    return written, skipped


def _pages_of(path: Path) -> list[Page]:
    """The pages of a pages table, or of the pages table of a dataset directory."""
    path = Path(path)
    if path.is_dir():
        return list(tables.Dataset(path).read("pages"))
    return list(tables.read(path, Page))


def _units_of(path: Path, images: dict[str, str] | None) -> tuple[list[Unit], dict[str, str]]:
    """The units of a units table or dataset, and the image URL of each page they sit on.

    A dataset directory is read for both tables; a units table that travels alone has no pages, so
    `images` supplies them, and it overrides what the directory said.
    """
    path = Path(path)
    page_images: dict[str, str] = {}
    if path.is_dir():
        dataset = tables.Dataset(path)
        records = list(dataset.read("units"))
        if dataset.tables["pages"] is not None:
            page_images = {page.id: page.image for page in dataset.read("pages")}
    else:
        records = list(tables.read(path, Unit))
    page_images.update(images or {})
    return records, page_images


def _table_path(path: Path, name: str) -> Path:
    """The file a table is written to: the path itself, or `<name>.parquet` in a dataset directory."""
    path = Path(path)
    return path / f"{name}.parquet" if path.is_dir() else path


def cache_root() -> Path:
    """The directory that holds `images/`: `$KUZUSHIJI_ATLAS_CACHE`, or `cache/` of the repository."""
    override = os.environ.get(ENV_CACHE)
    return Path(override) if override else REPO_ROOT / "cache"


def images_root(root: Path | None = None) -> Path:
    """The image cache directory: `root`, or `$KUZUSHIJI_ATLAS_CACHE/images`, or `cache/images`."""
    return Path(root) if root is not None else cache_root() / "images"


def index_path(root: Path | None = None) -> Path:
    """The Parquet index of the cache."""
    return images_root(root) / "index.parquet"


def _request_url(url: str, *, service: str | None, version: int | None, box: Box | None) -> str:
    if service is None:
        return url
    if box is not None:
        return f"{service}/{box.iiif_region()}/{_size_keyword(version)}/0/default.jpg"
    if version is not None:
        return full_url(service, version)
    return url


def _is_bare(url: str, service: str) -> bool:
    """True when `url` names its service without a region, a size or `info.json`."""
    cleaned = url.split("#", 1)[0].split("?", 1)[0].rstrip("/")
    return cleaned in (service, f"{service}/info.json")


def _size_keyword(version: int | str | None) -> str:
    if version is None:
        return "full"
    return "max" if _version_number(version) >= 3 else "full"


def _version_number(version: int | str) -> int:
    """A version the caller stated, as a number: 3, "3", "v3" and a profile URL all give 3."""
    if isinstance(version, int):
        return version
    found = re.search(r"[123]", str(version))
    return int(found.group()) if found else 2


def _version_of(document: dict) -> int:
    """The Image API version of an `info.json`, 2 when it does not say."""
    for key in ("@context", "profile"):
        found = _version_in(document.get(key))
        if found is not None:
            return found
    if str(document.get("type", "")).lower() == "imageservice3":
        return 3
    if "tile_width" in document or "tile_height" in document:
        return 1
    return 2


def _version_in(value: Any) -> int | None:
    if isinstance(value, (list, tuple)):
        for item in value:
            found = _version_in(item)
            if found is not None:
                return found
        return None
    if isinstance(value, str):
        found = re.search(r"/image/(\d)", value)
        if found:
            return int(found.group(1))
    return None


def _tiles_of(document: dict) -> list[dict]:
    tiles = document.get("tiles")
    if isinstance(tiles, list):
        return [dict(tile) for tile in tiles if isinstance(tile, dict)]
    width = document.get("tile_width")
    if not isinstance(width, int):
        return []
    height = document.get("tile_height")
    factors = document.get("scale_factors")
    return [
        {
            "width": width,
            "height": height if isinstance(height, int) else width,
            "scaleFactors": [factor for factor in factors if isinstance(factor, int)]
            if isinstance(factors, list)
            else [],
        }
    ]


def _current(cache: Path, url: str) -> ImageRecord | None:
    rows = [row for row in _read_index(cache) if row.superseded_by is None and row.url == url]
    return rows[-1] if rows else None


def _upsert(cache: Path, record: ImageRecord) -> None:
    """Write a row, retiring with `superseded_by` the row it replaces when the checksum changed."""
    rows: list[ImageRecord] = []
    for row in _read_index(cache):
        if row.url == record.url and row.superseded_by is None:
            if row.sha256 == record.sha256:
                continue  # the same image, with fresh headers
            row = row.model_copy(update={"superseded_by": record.sha256})
        rows.append(row)
    rows.append(record)
    _write_index(cache, rows)


def _read_index(cache: Path) -> list[ImageRecord]:
    path = cache / "index.parquet"
    if not path.exists():
        return []
    return list(tables.read(path, ImageRecord))


def _write_index(cache: Path, rows: list[ImageRecord]) -> int:
    """Write the whole index through the table store, and swap the file into place.

    Parquet is not appendable and the index is small next to the images, so a fetch rewrites it; the
    swap keeps the old file readable if the write is interrupted.
    """
    path = cache / "index.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    scratch = path.with_name(path.name + ".tmp")
    written = tables.write(scratch, rows, ImageRecord)
    os.replace(scratch, path)
    return written


def _file_for(cache: Path, record: ImageRecord) -> Path:
    """The cached file of a row, found by checksum: the extension is not part of the row."""
    folder = cache / record.sha256[:2]
    for candidate in sorted(folder.glob(f"{record.sha256}.*")):
        if candidate.is_file():
            return candidate
    return folder / record.sha256


def _digest(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(CHUNK), b""):
            digest.update(block)
            size += len(block)
    return digest.hexdigest(), size


def _image_size(path: Path) -> tuple[int, int]:
    try:
        with Image.open(path) as image:
            return image.width, image.height
    except Exception as exc:  # Pillow raises a family of errors for a file that is not an image
        raise ImageError(f"{path}: cannot read an image size ({exc})") from exc


def _extension(url: str, content_type: str | None) -> str:
    media = (content_type or "").split(";", 1)[0].strip().lower()
    if media in MEDIA_SUFFIX:
        return MEDIA_SUFFIX[media]
    suffix = Path(urlsplit(url).path).suffix.lower()
    return suffix if suffix in IMAGE_FORMATS else ".bin"
