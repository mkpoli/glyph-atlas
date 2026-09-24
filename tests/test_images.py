"""Tests for the IIIF helpers and the image cache.

The cache is pointed at `tmp_path` through `GLYPH_ATLAS_CACHE`, and every request goes to the
`http_server` fixture.
"""

from __future__ import annotations

import hashlib
import io
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from conftest import Scripted
from conftest import TestServer as Server
from PIL import Image
from pydantic import ValidationError

from glyph_atlas import images, net, tables
from glyph_atlas.schema import Box, Page, Unit

V1_INFO = {
    "@context": "http://iiif.io/api/image/1/context.json",
    "profile": "http://iiif.io/api/image/1/level2.json",
    "width": 800,
    "height": 600,
    "tile_width": 256,
    "tile_height": 256,
    "scale_factors": [1, 2, 4],
    "formats": ["jpg"],
    "qualities": ["native"],
}
V2_INFO = {
    "@context": "http://iiif.io/api/image/2/context.json",
    "profile": ["http://iiif.io/api/image/2/level2.json"],
    "protocol": "http://iiif.io/api/image",
    "width": 2095,
    "height": 3022,
    "sizes": [{"width": 130, "height": 188}],
    "tiles": [{"width": 512, "height": 512, "scaleFactors": [1, 2]}],
}
V3_INFO = {
    "@context": "http://iiif.io/api/image/3/context.json",
    "id": "https://example.org/iiif/3/abc",
    "type": "ImageService3",
    "profile": "level2",
    "protocol": "http://iiif.io/api/image",
    "width": 1200,
    "height": 900,
    "tiles": [{"width": 512, "height": 512, "scaleFactors": [1, 2, 4]}],
}


class Clock:
    """A monotonic clock that moves only when the code under test sleeps."""

    def __init__(self, now: float = 1_000.0) -> None:
        self.now = now
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


@pytest.fixture(autouse=True)
def cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Clock:
    """Keep the cache in `tmp_path` and the pauses in a fake clock."""
    monkeypatch.setenv(images.ENV_CACHE, str(tmp_path / "cache"))
    clock = Clock()
    monkeypatch.setattr(net, "CLOCK", clock)
    monkeypatch.setattr(net, "SLEEP", clock.sleep)
    net.reset_pauses()
    yield clock
    net.reset_pauses()


def jpeg(width: int = 64, height: int = 48, color: tuple[int, int, int] = (200, 30, 30)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buffer, format="JPEG", quality=95)
    return buffer.getvalue()


def png_quadrants(size: int = 80) -> bytes:
    image = Image.new("RGB", (size, size))
    half = size // 2
    image.paste((255, 0, 0), (0, 0, half, half))
    image.paste((0, 255, 0), (half, 0, size, half))
    image.paste((0, 0, 255), (0, half, half, size))
    image.paste((255, 255, 0), (half, half, size, size))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def info_bytes(document: dict) -> bytes:
    return json.dumps(document).encode("utf-8")


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://example.org/iiif/2/abcd", "https://example.org/iiif/2/abcd"),
        ("https://example.org/iiif/2/abcd/", "https://example.org/iiif/2/abcd"),
        ("https://example.org/iiif/2/abcd/info.json", "https://example.org/iiif/2/abcd"),
        (
            "https://example.org/iiif/2/abcd/full/full/0/default.jpg",
            "https://example.org/iiif/2/abcd",
        ),
        ("https://example.org/iiif/3/abcd/full/max/0/default.jpg", "https://example.org/iiif/3/abcd"),
        (
            "https://example.org/iiif/2/abcd/square/!200,200/90/native.jpg",
            "https://example.org/iiif/2/abcd",
        ),
        (
            "https://example.org/iiif/2/abcd/0,0,100,100/pct:50/0.0/color.png",
            "https://example.org/iiif/2/abcd",
        ),
        (
            "https://example.org/iiif/2/abcd/full/full/0/default.jpg?t=1#frag",
            "https://example.org/iiif/2/abcd",
        ),
        (
            "https://codh.rois.ac.jp/char-shape/iiif/200006663/200006663_00005_2.tif",
            "https://codh.rois.ac.jp/char-shape/iiif/200006663/200006663_00005_2.tif",
        ),
        (
            "https://codh.rois.ac.jp/char-shape/iiif/200006663/200006663_00005_2.tif/info.json",
            "https://codh.rois.ac.jp/char-shape/iiif/200006663/200006663_00005_2.tif",
        ),
        (
            "https://codh.rois.ac.jp/char-shape/iiif/200006663/200006663_00005_2.tif/full/full/0/default.jpg",
            "https://codh.rois.ac.jp/char-shape/iiif/200006663/200006663_00005_2.tif",
        ),
        ("https://example.org/collections/12345/photo.jpg", None),
        ("https://example.org/files/scan.tif", None),
        ("https://example.org/data?id=7", None),
        ("", None),
    ],
)
def test_service_of_strips_the_request_suffix(url: str, expected: str | None) -> None:
    assert images.service_of(url) == expected


@pytest.mark.parametrize(
    ("version", "size"),
    [(1, "full"), (2, "full"), (3, "max"), ("3", "max"), ("v3", "max")],
)
def test_full_url_follows_the_api_version(version: int | str, size: str) -> None:
    url = images.full_url("https://example.org/iiif/2/abcd", version)

    assert url == f"https://example.org/iiif/2/abcd/full/{size}/0/default.jpg"


def test_full_url_accepts_a_request_url() -> None:
    url = images.full_url("https://example.org/iiif/3/abcd/full/max/0/default.jpg", 3)

    assert url == "https://example.org/iiif/3/abcd/full/max/0/default.jpg"


def test_info_reads_a_version_2_document(http_server: Server) -> None:
    http_server.put("iiif/2/abc/info.json", info_bytes(V2_INFO))

    document = images.info(http_server.url("iiif/2/abc"))

    assert (document["width"], document["height"]) == (2095, 3022)
    assert document["version"] == 2
    assert document["tiles"] == [{"width": 512, "height": 512, "scaleFactors": [1, 2]}]
    assert document["sizes"] == [{"width": 130, "height": 188}]
    assert http_server.requests == ["GET /iiif/2/abc/info.json"]


def test_info_reads_a_version_3_document(http_server: Server) -> None:
    http_server.put("iiif/3/abc/info.json", info_bytes(V3_INFO))

    document = images.info(http_server.url("iiif/3/abc"))

    assert (document["width"], document["height"]) == (1200, 900)
    assert document["version"] == 3
    assert document["tiles"] == [{"width": 512, "height": 512, "scaleFactors": [1, 2, 4]}]


def test_info_reads_a_version_1_document(http_server: Server) -> None:
    http_server.put("iiif/1/abc/info.json", info_bytes(V1_INFO))

    document = images.info(http_server.url("iiif/1/abc"))

    assert (document["width"], document["height"]) == (800, 600)
    assert document["version"] == 1
    assert document["sizes"] == []  # version 1 has no sizes
    assert document["tiles"] == [{"width": 256, "height": 256, "scaleFactors": [1, 2, 4]}]


def test_info_names_the_url_when_html_is_served(http_server: Server) -> None:
    http_server.script["/iiif/2/abc/info.json"] = [
        Scripted(200, b"<!DOCTYPE html><html>gone</html>", {"Content-Type": "text/html"})
    ]

    with pytest.raises(net.DownloadError) as failure:
        images.info(http_server.url("iiif/2/abc"))

    assert http_server.url("iiif/2/abc/info.json") in str(failure.value)


def test_info_of_a_plain_file_url_fails() -> None:
    with pytest.raises(images.ImageError):
        images.info("https://example.org/photos/page.jpg")


def test_fetch_stores_the_image_and_indexes_it(http_server: Server, tmp_path: Path) -> None:
    body = jpeg(64, 48)
    http_server.put("iiif/2/page/full/full/0/default.jpg", body)
    url = http_server.url("iiif/2/page/full/full/0/default.jpg")

    record = images.fetch(url, pause=0)

    assert record.url == url
    assert record.service == http_server.url("iiif/2/page")
    assert record.sha256 == hashlib.sha256(body).hexdigest()
    assert (record.width, record.height) == (64, 48)
    assert record.bytes == len(body)
    assert record.fetched_at.tzinfo is not None
    assert record.etag == f'"{len(body)}"'
    assert record.superseded_by is None
    assert images.index() == [record]
    path = tmp_path / "cache" / "images" / record.sha256[:2] / f"{record.sha256}.jpg"
    assert images.path_for(url) == path
    assert path.read_bytes() == body
    assert http_server.requests == ["GET /iiif/2/page/full/full/0/default.jpg"]


def test_fetch_of_a_plain_file_url_asks_nothing_else(http_server: Server) -> None:
    http_server.put("photos/page.jpg", jpeg(20, 10))
    url = http_server.url("photos/page.jpg")

    record = images.fetch(url, pause=0)

    assert record.service is None
    assert (record.width, record.height) == (20, 10)
    assert http_server.requests == ["GET /photos/page.jpg"]


def test_fetch_of_a_service_base_reads_info_json_first(http_server: Server) -> None:
    http_server.put("iiif/2/page/info.json", info_bytes(V2_INFO))
    http_server.put("iiif/2/page/full/full/0/default.jpg", jpeg(64, 48))
    url = http_server.url("iiif/2/page")

    record = images.fetch(url, pause=0)

    assert http_server.requests == [
        "GET /iiif/2/page/info.json",
        "GET /iiif/2/page/full/full/0/default.jpg",
    ]
    assert record.url == url
    assert record.service == url


def test_fetch_of_a_version_3_service_uses_max(http_server: Server) -> None:
    http_server.put("iiif/3/page/info.json", info_bytes(V3_INFO))
    http_server.put("iiif/3/page/full/max/0/default.jpg", jpeg(12, 12))

    images.fetch(http_server.url("iiif/3/page"), pause=0)

    assert http_server.requests[-1] == "GET /iiif/3/page/full/max/0/default.jpg"


def test_fetch_with_a_box_requests_that_region(http_server: Server) -> None:
    http_server.put("iiif/2/page/info.json", info_bytes(V2_INFO))
    http_server.put("iiif/2/page/10,20,30,40/full/0/default.jpg", jpeg(30, 40))

    record = images.fetch(
        http_server.url("iiif/2/page"), box=Box(x=10, y=20, w=30, h=40), pause=0
    )

    assert http_server.requests[-1] == "GET /iiif/2/page/10,20,30,40/full/0/default.jpg"
    assert (record.width, record.height) == (30, 40)
    assert record.url == http_server.url("iiif/2/page/10,20,30,40/full/0/default.jpg")
    assert images.held(http_server.url("iiif/2/page")) is None


def test_held_returns_only_a_whole_page_scan(tmp_path: Path) -> None:
    service = "https://example.org/iiif/page"
    scan = tmp_path / "scan.jpg"
    scan.write_bytes(jpeg(40, 60))
    images.register(scan, service + "/full/max/0/default.jpg", root=tmp_path / "cache")
    root = tmp_path / "cache"
    for whole in (service, service + "/info.json", service + "/full/full/0/default.jpg"):
        assert images.held(whole, root=root) is not None
    for part in (service + "/0,0,10,10/full/0/default.jpg", service + "/full/20,/0/default.jpg"):
        assert images.held(part, root=root) is None
    assert images.held("https://example.org/iiif/other", root=root) is None


def test_a_box_needs_a_service(http_server: Server) -> None:
    http_server.put("photos/page.jpg", jpeg())

    with pytest.raises(images.ImageError):
        images.fetch(
            http_server.url("photos/page.jpg"), box=Box(x=0, y=0, w=4, h=4), pause=0
        )

    assert http_server.requests == []


def test_a_cache_hit_makes_no_request(http_server: Server) -> None:
    http_server.put("photos/page.jpg", jpeg())
    url = http_server.url("photos/page.jpg")
    first = images.fetch(url, pause=0)
    requests = list(http_server.requests)

    second = images.fetch(url, pause=0)

    assert second == first
    assert http_server.requests == requests


def test_a_refetch_of_the_same_bytes_replaces_the_row(http_server: Server) -> None:
    http_server.put("photos/page.jpg", jpeg())
    url = http_server.url("photos/page.jpg")
    first = images.fetch(url, pause=0)

    second = images.fetch(url, refresh=True, pause=0)

    assert second.sha256 == first.sha256
    assert images.index() == [second]


def test_a_refetch_that_changed_supersedes_the_old_row(
    http_server: Server, tmp_path: Path
) -> None:
    http_server.put("photos/page.jpg", jpeg(color=(1, 2, 3)))
    url = http_server.url("photos/page.jpg")
    first = images.fetch(url, pause=0)
    http_server.put("photos/page.jpg", jpeg(color=(200, 200, 200)))

    second = images.fetch(url, refresh=True, pause=0)

    assert second.sha256 != first.sha256
    rows = images.index()
    old = next(row for row in rows if row.sha256 == first.sha256)
    assert old.superseded_by == second.sha256
    assert len(rows) == 2
    path = images.path_for(url)
    assert path is not None and path.name == f"{second.sha256}.jpg"


def test_register_then_fetch_makes_no_request(tmp_path: Path) -> None:
    source = tmp_path / "200006663_00005_2.jpg"
    source.write_bytes(jpeg(64, 48))
    url = "https://codh.rois.ac.jp/char-shape/iiif/200006663/200006663_00005_2.tif"

    record = images.register(source, url)

    assert record.url == url
    assert record.service == url
    assert (record.width, record.height) == (64, 48)
    assert record.sha256 == hashlib.sha256(source.read_bytes()).hexdigest()
    cached = images.path_for(url)
    assert cached is not None and cached.read_bytes() == source.read_bytes()
    assert images.index() == [record]


def test_register_takes_the_fields_the_file_cannot_say(tmp_path: Path) -> None:
    source = tmp_path / "page.jpg"
    source.write_bytes(jpeg())
    fetched_at = datetime(2024, 5, 6, tzinfo=UTC)

    record = images.register(
        source, "https://example.org/page.jpg", etag='"abc"', last_modified="Mon, 06 May 2024 00:00:00 GMT",
        fetched_at=fetched_at, width=99,
    )

    assert record.etag == '"abc"'
    assert record.fetched_at == fetched_at
    assert record.width == 99
    assert record.height == 48


def test_register_refuses_a_field_the_row_does_not_have(tmp_path: Path) -> None:
    source = tmp_path / "page.jpg"
    source.write_bytes(jpeg())

    with pytest.raises(ValidationError):
        images.register(source, "https://example.org/page.jpg", document_id="codh:1")


def test_register_needs_a_file(tmp_path: Path) -> None:
    with pytest.raises(images.ImageError):
        images.register(tmp_path / "absent.jpg", "https://example.org/absent.jpg")


def test_register_needs_an_image(tmp_path: Path) -> None:
    source = tmp_path / "page.jpg"
    source.write_text("not an image", encoding="utf-8")

    with pytest.raises(images.ImageError):
        images.register(source, "https://example.org/page.jpg")


def test_crop_cuts_the_cached_image(tmp_path: Path) -> None:
    source = tmp_path / "page.png"
    source.write_bytes(png_quadrants())
    url = "https://example.org/page.png"
    images.register(source, url)

    cut = images.crop(url, Box(x=45, y=5, w=10, h=10))

    assert cut.size == (10, 10)
    assert cut.getpixel((5, 5)) == (0, 255, 0)


def test_crop_clamps_a_box_that_runs_over_the_edge(tmp_path: Path) -> None:
    source = tmp_path / "page.png"
    source.write_bytes(png_quadrants())
    url = "https://example.org/page.png"
    images.register(source, url)

    cut = images.crop(url, Box(x=-10, y=-10, w=20, h=20))

    assert cut.size == (10, 10)
    assert cut.getpixel((5, 5)) == (255, 0, 0)


def test_crop_outside_the_image_fails(tmp_path: Path) -> None:
    source = tmp_path / "page.png"
    source.write_bytes(png_quadrants())
    url = "https://example.org/page.png"
    images.register(source, url)

    with pytest.raises(images.ImageError):
        images.crop(url, Box(x=500, y=500, w=10, h=10))


def test_crop_of_an_uncached_url_fails() -> None:
    with pytest.raises(images.ImageError):
        images.crop("https://example.org/absent.jpg", Box(x=0, y=0, w=1, h=1))


def test_path_for_an_unknown_url_is_none() -> None:
    assert images.path_for("https://example.org/absent.jpg") is None


def test_the_cache_is_under_the_test_root(tmp_path: Path) -> None:
    assert images.index() == []
    assert images.index_path() == tmp_path / "cache" / "images" / "index.parquet"


def test_fill_sizes_reads_the_services_of_a_pages_table(
    http_server: Server, tmp_path: Path
) -> None:
    table = tmp_path / "pages.parquet"
    tables.write(
        table,
        [
            Page(
                id="codh:1:1", document_id="codh:1", seq=0,
                image=http_server.url("iiif/2/a"), width=10, height=20,
            ),
            Page(
                id="codh:1:2", document_id="codh:1", seq=1,
                image=http_server.url("iiif/2/b"), width=0, height=0,
            ),
            Page(
                id="codh:1:3", document_id="codh:1", seq=2,
                image=http_server.url("photos/c.jpg"), width=0, height=0,
            ),
        ],
        Page,
    )
    http_server.put("iiif/2/b/info.json", info_bytes(V2_INFO))

    filled, failed = images.fill_sizes(table)

    assert (filled, failed) == (1, 1)
    rows = {row.id: row for row in tables.read(table, Page)}
    assert (rows["codh:1:1"].width, rows["codh:1:1"].height) == (10, 20)  # already sized, kept
    filled_page = rows["codh:1:2"]
    assert (filled_page.width, filled_page.height) == (V2_INFO["width"], V2_INFO["height"])
    assert (rows["codh:1:3"].width, rows["codh:1:3"].height) == (0, 0)  # a plain file URL has no service
    assert http_server.requests == ["GET /iiif/2/b/info.json"]


def test_fetch_pages_fetches_then_skips(http_server: Server, tmp_path: Path) -> None:
    table = tmp_path / "pages.parquet"
    tables.write(
        table,
        [
            Page(
                id=f"codh:1:{n}", document_id="codh:1", seq=n,
                image=http_server.url(f"iiif/2/p{n}"), width=0, height=0,
            )
            for n in (1, 2)
        ],
        Page,
    )
    for n in (1, 2):
        http_server.put(f"iiif/2/p{n}/info.json", info_bytes(V2_INFO))
        http_server.put(f"iiif/2/p{n}/full/full/0/default.jpg", jpeg(64, 48))

    assert images.fetch_pages(table) == (2, 0, 0)
    rows = {row.id: row for row in tables.read(table, Page)}
    sizes = [(rows[f"codh:1:{n}"].width, rows[f"codh:1:{n}"].height) for n in (1, 2)]
    assert sizes == [(64, 48), (64, 48)]

    requests = list(http_server.requests)
    assert images.fetch_pages(table) == (0, 2, 0)
    assert http_server.requests == requests  # a page in the cache is not asked for again

    assert images.fetch_pages(table, document="codh:1", limit=1) == (0, 1, 0)
    assert images.fetch_pages(table, document="codh:9") == (0, 0, 0)
    assert images.fetch_pages(table, pages_filter=["codh:1:2"]) == (0, 1, 0)


def test_write_crops_writes_one_jpeg_per_boxed_unit(tmp_path: Path) -> None:
    page = tmp_path / "page.png"
    page.write_bytes(png_quadrants())
    url = "https://example.org/page.png"
    images.register(page, url)
    tables.write(
        tmp_path / "pages.parquet",
        [Page(id="codh:1:2", document_id="codh:1", seq=0, image=url, width=80, height=80)],
        Page,
    )
    units = [
        Unit(id="codh:1:2:0", document_id="codh:1", page_id="codh:1:2", box=Box(x=45, y=5, w=10, h=10)),
        Unit(id="codh:1:2:1", document_id="codh:1", page_id="codh:1:2"),
    ]
    table = tmp_path / "units.parquet"
    tables.write(table, units, Unit)
    out = tmp_path / "crops"

    written, skipped = images.write_crops(table, out)  # the pages table beside the units table

    assert (written, skipped) == (1, 1)
    crop_path = out / "codh_1_2_0.jpg"
    with Image.open(crop_path) as cut:
        assert cut.size == (10, 10)
    assert images.write_crops(table, out, images={"codh:1:2": url}) == (0, 2)  # both skipped now


def test_write_crops_of_a_units_table_alone_skips_everything(tmp_path: Path) -> None:
    alone = tmp_path / "alone" / "units.parquet"
    alone.parent.mkdir()
    tables.write(
        alone,
        [Unit(id="codh:1:2:0", document_id="codh:1", page_id="codh:1:2", box=Box(x=0, y=0, w=4, h=4))],
        Unit,
    )

    assert images.write_crops(alone, tmp_path / "crops") == (0, 1)
