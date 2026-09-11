"""List and extract members of a large remote zip through HTTP range requests.

The listing reads the end of the central directory from the tail of the file: one HEAD for the size
and the ETag, one range request for the last 64 KiB and, when the central directory does not lie
inside that tail, one more range request for the central directory. Zip64 archives are read through
the zip64 end-of-central-directory record and its locator, and a zip64 extra field replaces every
saturated 32-bit field of a central directory entry.

Every response to a range request must carry `Content-Range`. A 200 without it means the server
ignores ranges and is reported as `RangeIgnored`.

The listing is cached in `cache/zipindex/<sha256 of url>.parquet` with the ETag and the size in the
Parquet schema metadata. A later listing reuses the cache when the ETag and the size the server
reports still match; a server that reports neither leaves the cache in place.

`extract` asks for the members whose local headers lie within 1 MiB of one another in one range
request. Members are written below `dest`; a name that is absolute, holds a `..` component or leaves
`dest` is refused.
"""

from __future__ import annotations

import hashlib
import os
import re
import struct
import zipfile
import zlib
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Self

import httpx
import pyarrow as pa
import pyarrow.parquet as pq

USER_AGENT = "glyph-atlas (+https://github.com/mkpoli/glyph-atlas)"
CACHE_ROOT = Path("cache") / "zipindex"
TAIL_BYTES = 64 * 1024
BATCH_BYTES = 1024 * 1024
EXTRA_ALLOWANCE = 1024

EOCD = b"PK\x05\x06"
EOCD64 = b"PK\x06\x06"
EOCD64_LOCATOR = b"PK\x06\x07"
CENTRAL_HEADER = b"PK\x01\x02"
LOCAL_HEADER = b"PK\x03\x04"
LOCAL_HEADER_SIZE = 30
CENTRAL_HEADER_SIZE = 46
ZIP64_EXTRA = 0x0001
ZIP64_LIMIT = 0xFFFFFFFF
CRC_MASK = 0xFFFFFFFF
CONTENT_RANGE = re.compile(r"bytes\s+(\d+)-(\d+)/(\d+|\*)")

ENTRY_SCHEMA = pa.schema(
    [
        pa.field("name", pa.string(), nullable=False),
        pa.field("method", pa.int32(), nullable=False),
        pa.field("compress_size", pa.int64(), nullable=False),
        pa.field("file_size", pa.int64(), nullable=False),
        pa.field("crc32", pa.uint32(), nullable=False),
        pa.field("header_offset", pa.int64(), nullable=False),
    ]
)
CACHE_URL = b"glyph-atlas.url"
CACHE_ETAG = b"glyph-atlas.etag"
CACHE_SIZE = b"glyph-atlas.size"
CACHE_DIRECTORY_SIZE = b"glyph-atlas.central-directory-size"


class RemoteZipError(RuntimeError):
    """The remote file could not be read as a zip."""


class RangeIgnored(RemoteZipError):
    """The server answered a range request with 200 and no `Content-Range`."""


class CrcMismatch(RemoteZipError):
    """A member came back with a crc32 that does not match the central directory."""


@dataclass(frozen=True)
class Entry:
    """One central directory record."""

    name: str
    method: int
    compress_size: int
    file_size: int
    crc32: int
    header_offset: int

    @property
    def directory(self) -> bool:
        return self.name.endswith("/")


@dataclass(frozen=True)
class _Cached:
    etag: str | None
    size: int | None
    central_directory_size: int | None
    entries: list[Entry]


class RemoteZip:
    """A zip archive read over HTTP range requests.

    `cache_root` is the directory of the listing cache; it defaults to `cache/zipindex`. The listing
    happens in the constructor, `entries` holds it afterwards.
    """

    def __init__(
        self,
        url: str,
        *,
        client: httpx.Client | None = None,
        cache_root: Path | str | None = None,
        use_cache: bool = True,
    ) -> None:
        self.url = url
        self.cache_root = Path(cache_root) if cache_root is not None else CACHE_ROOT
        self.use_cache = use_cache
        self._owns_client = client is None
        self._client = client if client is not None else httpx.Client(
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
            timeout=httpx.Timeout(30.0, read=120.0),
        )
        self.entries: list[Entry] = []
        self.size: int | None = None
        self.etag: str | None = None
        self.central_directory_size: int | None = None
        self._index: dict[str, Entry] = {}
        self._requests = 0
        self._list()

    # -- listing ---------------------------------------------------------------------------------

    def _list(self) -> None:
        etag, size = self._head()
        cached = self._read_cache() if self.use_cache else None
        if cached is not None and _unchanged(cached.etag, etag) and _unchanged(cached.size, size):
            self.etag = cached.etag
            self.size = cached.size if cached.size is not None else size
            self.central_directory_size = cached.central_directory_size
            self._index = {entry.name: entry for entry in cached.entries}
            self.entries = cached.entries
            return
        self.etag = etag
        self._read_directory()
        self._write_cache()

    def _read_directory(self) -> None:
        start, tail = self._tail()
        size = self.size if self.size is not None else start + len(tail)
        position = tail.rfind(EOCD)
        if position < 0:
            raise RemoteZipError(
                f"{self.url}: no end of central directory record in the last {len(tail)} bytes"
            )
        count, directory_size, directory_offset = struct.unpack_from("<HII", tail, position + 10)
        locator = position - 20
        if tail[locator : locator + 4] == EOCD64_LOCATOR:
            count, directory_size, directory_offset = _zip64_directory(tail, start, locator, self.url)
        elif directory_size == ZIP64_LIMIT or directory_offset == ZIP64_LIMIT:
            raise RemoteZipError(f"{self.url}: the end record is zip64 but holds no zip64 locator")
        self.central_directory_size = directory_size
        directory_end = directory_offset + directory_size
        if directory_end > size:
            raise RemoteZipError(
                f"{self.url}: the central directory ends at {directory_end}, past the {size} byte file"
            )
        if directory_offset >= start:
            data = tail[directory_offset - start : directory_end - start]
        else:
            _, data = self._range(directory_offset, directory_end - 1)
        entries = _parse_central_directory(data)
        if len(entries) != count:
            raise RemoteZipError(
                f"{self.url}: the central directory holds {len(entries)} entries, the end record says {count}"
            )
        self.entries = entries
        self._index = {entry.name: entry for entry in entries}

    # -- http ------------------------------------------------------------------------------------

    def _head(self) -> tuple[str | None, int | None]:
        self._requests += 1
        try:
            response = self._client.head(self.url)
        except httpx.HTTPError as exc:
            raise RemoteZipError(f"{self.url}: HEAD failed: {exc}") from exc
        if response.status_code in (405, 501):
            return None, None
        if response.status_code >= 400:
            raise RemoteZipError(f"{self.url}: HEAD answered {response.status_code}")
        etag = response.headers.get("ETag") or None
        length = response.headers.get("Content-Length")
        size = int(length) if length and length.isdigit() and int(length) > 0 else None
        return etag, size

    def _fetch(self, specification: str) -> tuple[int, bytes]:
        """One range request; returns the first byte it covers and the body."""
        self._requests += 1
        try:
            response = self._client.get(self.url, headers={"Range": specification})
        except httpx.HTTPError as exc:
            raise RemoteZipError(f"{self.url}: {specification} failed: {exc}") from exc
        header = response.headers.get("Content-Range")
        if response.status_code == 200 and header is None:
            raise RangeIgnored(
                f"{self.url} answered 200 to {specification} without Content-Range: the server ignores ranges"
            )
        if response.status_code != 206:
            raise RemoteZipError(f"{self.url}: {specification} answered {response.status_code}")
        match = CONTENT_RANGE.match(header.strip()) if header else None
        if match is None:
            raise RemoteZipError(f"{self.url}: unreadable Content-Range {header!r} for {specification}")
        first, last, total = int(match[1]), int(match[2]), match[3]
        body = response.content
        if last - first + 1 != len(body):
            raise RemoteZipError(
                f"{self.url}: Content-Range {header!r} covers {last - first + 1} bytes, the body holds {len(body)}"
            )
        if total.isdigit():
            self.size = int(total)
        return first, body

    def _range(self, start: int, end: int) -> tuple[int, bytes]:
        if self.size is not None and end > self.size - 1:
            end = self.size - 1
        return self._fetch(f"bytes={start}-{end}")

    def _tail(self) -> tuple[int, bytes]:
        if self.size is not None and self.size > TAIL_BYTES:
            return self._range(self.size - TAIL_BYTES, self.size - 1)
        return self._fetch(f"bytes=-{TAIL_BYTES}")

    # -- entries ---------------------------------------------------------------------------------

    def entry(self, name: str) -> Entry:
        try:
            return self._index[name]
        except KeyError:
            raise KeyError(name) from None

    def iter_prefix(self, prefix: str) -> Iterator[Entry]:
        """The entries whose name starts with `prefix`, in central directory order."""
        return (entry for entry in self.entries if entry.name.startswith(prefix))

    def requests_made(self) -> int:
        return self._requests

    def read(self, name: str) -> bytes:
        """The decompressed bytes of one member, crc32 checked."""
        entry = self.entry(name)
        if entry.directory:
            raise IsADirectoryError(name)
        return next(iter(self._read_members([(0, entry, None)])))[3]

    def extract(self, names: Iterable[str], dest: Path) -> list[Path]:
        """Write `names` below `dest` and return the paths, in the order of `names`."""
        root = Path(dest)
        root.mkdir(parents=True, exist_ok=True)
        root = root.resolve()
        wanted: list[tuple[int, Entry, Path]] = []
        written: list[Path | None] = []
        for name in names:
            entry = self.entry(name)
            relative = _safe_relative(entry.name, root)
            written.append(None)
            if entry.directory:
                (root / relative).mkdir(parents=True, exist_ok=True)
                written[-1] = root / relative
                continue
            wanted.append((len(written) - 1, entry, relative))
        for position, entry, relative, data in self._read_members(wanted):
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            written[position] = path
        return [path for path in written if path is not None]

    def _read_members(
        self, wanted: list[tuple[int, Entry, Path | None]]
    ) -> Iterator[tuple[int, Entry, Path | None, bytes]]:
        """Yield (position, entry, relative, bytes); one request per group of local headers."""
        ordered = sorted(wanted, key=lambda item: item[1].header_offset)
        batch: list[tuple[int, Entry, Path | None]] = []
        for item in ordered:
            if batch and item[1].header_offset - batch[0][1].header_offset > BATCH_BYTES:
                yield from self._read_batch(batch)
                batch = []
            batch.append(item)
        if batch:
            yield from self._read_batch(batch)

    def _read_batch(
        self, batch: list[tuple[int, Entry, Path | None]]
    ) -> Iterator[tuple[int, Entry, Path | None, bytes]]:
        last = batch[-1][1]
        end = last.header_offset + LOCAL_HEADER_SIZE + len(last.name.encode("utf-8")) + EXTRA_ALLOWANCE
        start, chunk = self._range(batch[0][1].header_offset, end + last.compress_size - 1)
        for item in batch:
            entry = item[1]
            position = entry.header_offset - start
            if position < 0 or position + LOCAL_HEADER_SIZE > len(chunk):
                raise RemoteZipError(f"{self.url}: {entry.name!r} is not inside the {len(chunk)} bytes returned")
            if chunk[position : position + 4] != LOCAL_HEADER:
                raise RemoteZipError(f"{self.url}: no local header for {entry.name!r}")
            name_length, extra_length = struct.unpack_from("<HH", chunk, position + 26)
            data_position = position + LOCAL_HEADER_SIZE + name_length + extra_length
            data = chunk[data_position : data_position + entry.compress_size]
            if len(data) < entry.compress_size:
                absolute = start + data_position
                _, data = self._range(absolute, absolute + entry.compress_size - 1)
                data = data[: entry.compress_size]
            yield item[0], entry, item[2], _decompress(entry, data)

    # -- cache -----------------------------------------------------------------------------------

    def cache_path(self) -> Path:
        digest = hashlib.sha256(self.url.encode("utf-8")).hexdigest()
        return self.cache_root / f"{digest}.parquet"

    def _read_cache(self) -> _Cached | None:
        path = self.cache_path()
        if not path.is_file():
            return None
        try:
            table = pq.read_table(path)
            metadata = table.schema.metadata or {}
            if metadata.get(CACHE_URL) != self.url.encode("utf-8"):
                return None
            etag = metadata.get(CACHE_ETAG, b"").decode("utf-8") or None
            size = int(metadata.get(CACHE_SIZE, b"0"))
            directory_size = int(metadata.get(CACHE_DIRECTORY_SIZE, b"0"))
            entries = [
                Entry(
                    name=row["name"],
                    method=int(row["method"]),
                    compress_size=int(row["compress_size"]),
                    file_size=int(row["file_size"]),
                    crc32=int(row["crc32"]),
                    header_offset=int(row["header_offset"]),
                )
                for row in table.to_pylist()
            ]
        except (OSError, ValueError, TypeError, KeyError):
            return None
        return _Cached(etag, size or None, directory_size or None, entries)

    def _write_cache(self) -> None:
        table = pa.Table.from_pylist(
            [
                {
                    "name": entry.name,
                    "method": entry.method,
                    "compress_size": entry.compress_size,
                    "file_size": entry.file_size,
                    "crc32": entry.crc32,
                    "header_offset": entry.header_offset,
                }
                for entry in self.entries
            ],
            schema=ENTRY_SCHEMA,
        )
        table = table.replace_schema_metadata(
            {
                CACHE_URL: self.url.encode("utf-8"),
                CACHE_ETAG: (self.etag or "").encode("utf-8"),
                CACHE_SIZE: str(self.size or 0).encode("utf-8"),
                CACHE_DIRECTORY_SIZE: str(self.central_directory_size or 0).encode("utf-8"),
            }
        )
        path = self.cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        pq.write_table(table, temporary, compression="zstd")
        os.replace(temporary, path)

    # -- lifecycle -------------------------------------------------------------------------------

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exception: object) -> None:
        self.close()


def _unchanged(cached: str | int | None, reported: str | int | None) -> bool:
    """A field the server does not report cannot be compared and leaves the cache valid."""
    return reported is None or cached == reported


def _zip64_directory(tail: bytes, start: int, locator: int, url: str) -> tuple[int, int, int]:
    """Read the zip64 end of central directory record the locator points at."""
    record = struct.unpack_from("<Q", tail, locator + 8)[0]
    position = record - start
    if position < 0 or position + 56 > len(tail):
        raise RemoteZipError(f"{url}: the zip64 end of central directory at {record} is not in the last {len(tail)} bytes")
    if tail[position : position + 4] != EOCD64:
        raise RemoteZipError(f"{url}: no zip64 end of central directory record at {record}")
    count = struct.unpack_from("<Q", tail, position + 32)[0]
    directory_size = struct.unpack_from("<Q", tail, position + 40)[0]
    directory_offset = struct.unpack_from("<Q", tail, position + 48)[0]
    return count, directory_size, directory_offset


def _parse_central_directory(data: bytes) -> list[Entry]:
    entries: list[Entry] = []
    position = 0
    while position + CENTRAL_HEADER_SIZE <= len(data) and data[position : position + 4] == CENTRAL_HEADER:
        (
            _made,
            _needed,
            flags,
            method,
            _time,
            _date,
            crc32,
            compress_size,
            file_size,
            name_length,
            extra_length,
            comment_length,
            _disk,
            _internal,
            _external,
            header_offset,
        ) = struct.unpack_from("<HHHHHHIIIHHHHHII", data, position + 4)
        name = data[position + CENTRAL_HEADER_SIZE : position + CENTRAL_HEADER_SIZE + name_length]
        extra = data[
            position + CENTRAL_HEADER_SIZE + name_length : position + CENTRAL_HEADER_SIZE
            + name_length
            + extra_length
        ]
        if ZIP64_LIMIT in (compress_size, file_size, header_offset):
            file_size, compress_size, header_offset = _zip64_entry(file_size, compress_size, header_offset, extra)
        entries.append(
            Entry(
                name=name.decode("utf-8" if flags & 0x800 else "cp437", "replace"),
                method=method,
                compress_size=compress_size,
                file_size=file_size,
                crc32=crc32,
                header_offset=header_offset,
            )
        )
        position += CENTRAL_HEADER_SIZE + name_length + extra_length + comment_length
    return entries


def _zip64_entry(
    file_size: int, compress_size: int, header_offset: int, extra: bytes
) -> tuple[int, int, int]:
    """Replace the saturated fields of a central directory entry from its zip64 extra field."""
    values = _extra_values(extra, ZIP64_EXTRA)
    if values is None:
        raise RemoteZipError("a central directory entry has a zip64 size without a zip64 extra field")
    numbers = struct.unpack(f"<{len(values) // 8}Q", values[: len(values) // 8 * 8])
    needed = sum(1 for value in (file_size, compress_size, header_offset) if value == ZIP64_LIMIT)
    if len(numbers) < needed:
        raise RemoteZipError("a zip64 extra field is shorter than the sizes it must hold")
    index = 0
    if file_size == ZIP64_LIMIT:
        file_size = numbers[index]
        index += 1
    if compress_size == ZIP64_LIMIT:
        compress_size = numbers[index]
        index += 1
    if header_offset == ZIP64_LIMIT:
        header_offset = numbers[index]
    return file_size, compress_size, header_offset


def _extra_values(extra: bytes, tag: int) -> bytes | None:
    position = 0
    while position + 4 <= len(extra):
        header, size = struct.unpack_from("<HH", extra, position)
        position += 4
        if header == tag:
            return extra[position : position + size]
        position += size
    return None


def _decompress(entry: Entry, data: bytes) -> bytes:
    if entry.method == zipfile.ZIP_STORED:
        raw = data
    elif entry.method == zipfile.ZIP_DEFLATED:
        try:
            raw = zlib.decompress(data, -15)
        except zlib.error as exc:
            raise RemoteZipError(f"{entry.name}: {exc}") from exc
    else:
        raise RemoteZipError(f"{entry.name}: compression method {entry.method} is not supported")
    if len(raw) != entry.file_size:
        raise RemoteZipError(f"{entry.name}: {entry.file_size} bytes expected, {len(raw)} decompressed")
    if zlib.crc32(raw) != entry.crc32 & CRC_MASK:
        raise CrcMismatch(
            f"{entry.name}: crc32 {zlib.crc32(raw):08x} does not match the central directory {entry.crc32:08x}"
        )
    return raw


def _safe_relative(name: str, root: Path) -> Path:
    """The member name as a path below `root`, or ValueError when it could leave `root`."""
    if not name or "\0" in name or name.startswith("/") or "\\" in name or name[1:2] == ":":
        raise ValueError(f"unsafe zip member name {name!r}")
    parts = [part for part in name.split("/") if part not in ("", ".")]
    if not parts or ".." in parts:
        raise ValueError(f"unsafe zip member name {name!r}")
    relative = Path(*parts)
    if not (root / relative).resolve().is_relative_to(root):
        raise ValueError(f"unsafe zip member name {name!r}: it leaves {root}")
    return relative
