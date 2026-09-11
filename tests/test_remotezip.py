"""Tests for the remote zip reader.

Every zip here is built in `tmp_path` and served by the `http_server` fixture, which honours
`Range`; no test reaches the network.
"""

from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

import httpx
import pytest

from glyph_atlas.remotezip import CrcMismatch, Entry, RangeIgnored, RemoteZip

STORED = b"stored member\n"
CSV = b"".join(b"%d,%d\n" % (index, index * index) for index in range(200))
SPACER = bytes(range(256)) * 6000


class Reported:
    """An httpx client whose HEAD reports the ETag and the size a real server would."""

    def __init__(self, client: httpx.Client, headers: dict[str, str]) -> None:
        self._client = client
        self._headers = headers

    def head(self, url: str) -> httpx.Response:
        response = self._client.head(url)
        return httpx.Response(
            response.status_code,
            headers={**response.headers, **self._headers},
            request=response.request,
        )

    def get(self, url: str, headers: dict[str, str]) -> httpx.Response:
        return self._client.get(url, headers=headers)


def build_zip(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("plain.txt", STORED, compress_type=zipfile.ZIP_STORED)
        archive.writestr("nested/data.csv", CSV, compress_type=zipfile.ZIP_DEFLATED)
        archive.writestr("deep/deeper/keep.txt", b"keep\n", compress_type=zipfile.ZIP_DEFLATED)
    return path


def served(server, path: Path, name: str = "sample.zip") -> str:
    server.put(name, path.read_bytes())
    return server.url(name)


def test_listing_reads_the_tail_and_matches_zipfile(tmp_path, http_server):
    source = build_zip(tmp_path / "sample.zip")
    url = served(http_server, source)
    archive = RemoteZip(url, cache_root=tmp_path / "cache")
    with zipfile.ZipFile(source) as reference:
        expected = reference.infolist()
    assert http_server.requests[0] == "HEAD /sample.zip"
    assert archive.requests_made() == 2, "the central directory lies inside the last 64 KiB"
    assert [entry.name for entry in archive.entries] == [info.filename for info in expected]
    for entry, info in zip(archive.entries, expected, strict=True):
        assert (entry.method, entry.compress_size, entry.file_size, entry.crc32, entry.header_offset) == (
            info.compress_type,
            info.compress_size,
            info.file_size,
            info.CRC,
            info.header_offset,
        )
    assert archive.size == source.stat().st_size
    assert 0 < archive.central_directory_size < archive.size


def test_read_decompresses_stored_and_deflated_members(tmp_path, http_server):
    url = served(http_server, build_zip(tmp_path / "sample.zip"))
    archive = RemoteZip(url, cache_root=tmp_path / "cache")
    assert archive.read("plain.txt") == STORED
    assert archive.read("nested/data.csv") == CSV
    assert archive.read("deep/deeper/keep.txt") == b"keep\n"
    assert archive.requests_made() == 5, "the listing and one range request per member"
    with pytest.raises(KeyError):
        archive.read("absent.txt")


def test_iter_prefix_selects_by_name(tmp_path, http_server):
    source = build_zip(tmp_path / "sample.zip")
    url = served(http_server, source)
    archive = RemoteZip(url, cache_root=tmp_path / "cache")
    assert [entry.name for entry in archive.iter_prefix("nested/")] == ["nested/data.csv"]
    assert [entry.name for entry in archive.iter_prefix("nothing/")] == []
    with zipfile.ZipFile(source) as reference:
        info = reference.getinfo("plain.txt")
    assert archive.entry("plain.txt") == Entry(
        name="plain.txt",
        method=zipfile.ZIP_STORED,
        compress_size=len(STORED),
        file_size=len(STORED),
        crc32=info.CRC,
        header_offset=info.header_offset,
    )


def test_extract_gathers_nearby_members_into_one_request(tmp_path, http_server):
    source = tmp_path / "many-small.zip"
    with zipfile.ZipFile(source, "w") as archive:
        for index in range(40):
            archive.writestr(f"crops/{index:03d}.txt", b"jpeg" * 20, compress_type=zipfile.ZIP_STORED)
    url = served(http_server, source, "many-small.zip")
    archive = RemoteZip(url, cache_root=tmp_path / "cache")
    names = [entry.name for entry in archive.entries]
    listing = archive.requests_made()
    paths = archive.extract(names, tmp_path / "out")
    assert archive.requests_made() - listing == 1, "40 local headers inside 1 MiB take one request"
    assert [path.name for path in paths] == [name.rsplit("/", 1)[-1] for name in names]
    for path in paths:
        assert path.read_bytes() == b"jpeg" * 20
        assert path.parent == (tmp_path / "out" / "crops").resolve()


def test_extract_uses_separate_requests_for_distant_members(tmp_path, http_server):
    source = tmp_path / "spread.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("a.txt", b"first\n", compress_type=zipfile.ZIP_STORED)
        archive.writestr("spacer.bin", SPACER, compress_type=zipfile.ZIP_STORED)
        archive.writestr("z.txt", b"last\n", compress_type=zipfile.ZIP_STORED)
    url = served(http_server, source, "spread.zip")
    archive = RemoteZip(url, cache_root=tmp_path / "cache")
    listing = archive.requests_made()
    paths = archive.extract(["z.txt", "a.txt"], tmp_path / "out")
    assert archive.requests_made() - listing == 2, "the two headers lie more than 1 MiB apart"
    assert [path.name for path in paths] == ["z.txt", "a.txt"]
    assert paths[0].read_bytes() == b"last\n" and paths[1].read_bytes() == b"first\n"


def test_zip64_directory_outside_the_tail(tmp_path, http_server):
    source = tmp_path / "many.zip"
    with zipfile.ZipFile(source, "w") as archive:
        for index in range(65_536):
            archive.writestr(f"f/{index:05d}", b"x")
    payload = source.read_bytes()
    assert payload[-42:-38] == b"PK\x06\x07", "zipfile writes a zip64 locator for this archive"
    url = served(http_server, source, "many.zip")
    archive = RemoteZip(url, cache_root=tmp_path / "cache")
    assert len(archive.entries) == 65_536
    assert archive.requests_made() == 3, "HEAD, the tail and the central directory"
    assert archive.entries[0].name == "f/00000" and archive.entries[-1].name == "f/65535"
    assert archive.read("f/12345") == b"x"


def test_a_server_that_ignores_ranges_is_reported(tmp_path, http_server):
    http_server.ignore_ranges = True
    url = served(http_server, build_zip(tmp_path / "sample.zip"))
    with pytest.raises(RangeIgnored, match="ignores ranges"):
        RemoteZip(url, cache_root=tmp_path / "cache")


def test_a_corrupted_byte_fails_the_crc_check(tmp_path, http_server):
    source = build_zip(tmp_path / "sample.zip")
    payload = bytearray(source.read_bytes())
    payload[payload.index(STORED)] ^= 0x20
    http_server.put("sample.zip", bytes(payload))
    archive = RemoteZip(http_server.url("sample.zip"), cache_root=tmp_path / "cache")
    with pytest.raises(CrcMismatch, match="crc32"):
        archive.read("plain.txt")
    assert archive.read("nested/data.csv") == CSV, "the other members are intact"


def test_the_cache_is_reused_on_a_second_listing(tmp_path, http_server):
    url = served(http_server, build_zip(tmp_path / "sample.zip"))
    cache = tmp_path / "cache"
    first = RemoteZip(url, cache_root=cache)
    assert first.requests_made() == 2
    assert first.cache_path() == cache / f"{hashlib.sha256(url.encode()).hexdigest()}.parquet"
    assert first.cache_path().is_file()
    before = len(http_server.requests)
    second = RemoteZip(url, cache_root=cache)
    assert second.requests_made() == 1, "only the HEAD that checks the ETag and the size"
    assert http_server.requests[before:] == ["HEAD /sample.zip"]
    assert second.entries == first.entries
    assert second.size == first.size
    assert second.central_directory_size == first.central_directory_size


def test_the_cache_is_dropped_when_the_etag_changes(tmp_path, http_server):
    source = build_zip(tmp_path / "sample.zip")
    url = served(http_server, source)
    cache = tmp_path / "cache"
    with httpx.Client() as client:
        size = str(source.stat().st_size)
        same = Reported(client, {"ETag": '"1"', "Content-Length": size})
        assert RemoteZip(url, client=same, cache_root=cache).requests_made() == 2
        assert RemoteZip(url, client=same, cache_root=cache).requests_made() == 1
        for headers in ({"ETag": '"2"', "Content-Length": size},
                        {"ETag": '"1"', "Content-Length": str(source.stat().st_size - 1)}):
            before = len(http_server.requests)
            archive = RemoteZip(url, client=Reported(client, headers), cache_root=cache)
            assert archive.requests_made() == 2, "a changed ETag or size lists the archive again"
            assert http_server.requests[before:] == ["HEAD /sample.zip", "GET /sample.zip"]
            assert len(archive.entries) == 3


def test_extract_refuses_names_that_leave_the_destination(tmp_path, http_server):
    source = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("../escape.txt", b"no\n")
        archive.writestr("/absolute.txt", b"no\n")
        archive.writestr("good.txt", b"yes\n")
    url = served(http_server, source, "unsafe.zip")
    archive = RemoteZip(url, cache_root=tmp_path / "cache")
    dest = tmp_path / "out"
    for name in ("../escape.txt", "/absolute.txt"):
        with pytest.raises(ValueError, match="unsafe"):
            archive.extract([name], dest)
    assert not (tmp_path / "escape.txt").exists()
    assert archive.extract(["good.txt"], dest) == [dest.resolve() / "good.txt"]
    assert (dest / "good.txt").read_bytes() == b"yes\n"
