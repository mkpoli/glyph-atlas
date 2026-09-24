"""Display assets persist across restarts without weakening review revision checks."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from glyph_atlas.review import atlas
from glyph_atlas.review.media import MediaCache, router


@pytest.fixture
def media(tmp_path, monkeypatch):
    monkeypatch.setenv("GLYPH_ATLAS_CACHE", str(tmp_path))
    root = tmp_path / "images"
    root.mkdir()
    image = root / "page.png"
    Image.new("RGB", (600, 800), "white").save(image)
    return MediaCache(), image


def key(url):
    return url.rsplit("/", 1)[1].removesuffix(".webp")


def test_cache_survives_restart_and_does_not_reopen_source(media, monkeypatch):
    cache, source = media
    url = cache.local(source, (10, 20, 30, 50))
    first = cache.materialize(key(url)).read_bytes()
    monkeypatch.setattr(atlas, "decoded_image", lambda *_: pytest.fail("cached image decoded again"))
    restarted = MediaCache()
    assert restarted.local(source, (10, 20, 30, 50)) == url
    assert restarted.materialize(key(url)).read_bytes() == first


def test_geometry_and_source_changes_have_distinct_urls(media):
    cache, source = media
    first = cache.local(source, (10, 20, 30, 50))
    assert cache.local(source, (10, 20, 31, 50)) != first
    assert cache.local(source, (10, 20, 30, 50), context=True) != first
    Image.new("RGB", (600, 800), "black").save(source)
    assert cache.local(source, (10, 20, 30, 50)) != first
    with pytest.raises(ValueError, match="changed"):
        cache.materialize(key(first))


def test_concurrent_misses_generate_once(media, monkeypatch):
    cache, source = media
    url = cache.local(source, (10, 20, 30, 50))
    calls = []
    original = atlas.decoded_image
    def decode(*args):
        calls.append(args)
        return original(*args)
    monkeypatch.setattr(atlas, "decoded_image", decode)
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: cache.materialize(key(url)).read_bytes(), range(12)))
    assert len(calls) == 1 and all(r == results[0] for r in results)


def test_http_asset_has_immutable_cache_and_conditional_response(media):
    cache, source = media
    app = FastAPI()
    app.include_router(router(cache))
    client = TestClient(app)
    url = cache.local(source, (10, 20, 30, 50))
    response = client.get(url)
    assert response.status_code == 200 and response.headers["content-type"] == "image/webp"
    assert "immutable" in response.headers["cache-control"]
    assert client.get(url, headers={"If-None-Match": response.headers["etag"]}).status_code == 304
    assert client.get("/atlas/media/unknown.webp").status_code == 404


def test_registered_corpus_row_avoids_unit_lookup_and_honors_rights(media):
    cache, source = media
    crops = SimpleNamespace(page_image_path=lambda *_: source)
    row = {"image": "file:page.png", "image_licence": "CC-BY-SA-4.0", "corpus": "example",
           "box": {"x": 10, "y": 20, "w": 30, "h": 50},
           "thumbnail": {"available": True, "mode": "local_crop"}}
    url = cache.corpus_image(row, crops)
    assert cache.materialize(key(url)).is_file()
    assert cache.corpus_image({**row, "image_licence": None}, crops) is None


def test_remote_cache_is_bounded_to_registered_codh_images(media, monkeypatch):
    cache, _ = media
    assert cache.remote("http://127.0.0.1/private") is None
    assert cache.remote("https://codh.rois.ac.jp.evil.test/char-shape/iiif/x") is None
    assert cache.remote("https://codh.rois.ac.jp/private") is None
    url = cache.remote("https://codh.rois.ac.jp/char-shape/iiif/a/b.tif/1,2,30,40/240,/0/default.jpg")
    calls = []
    def download(remote):
        calls.append(remote)
        return Image.new("RGB", (30, 40), "white")
    monkeypatch.setattr(cache, "_download", download)
    cache.materialize(key(url))
    MediaCache().materialize(key(url))
    assert len(calls) == 1


def test_source_path_must_be_inside_registered_roots(media, tmp_path):
    cache, _ = media
    source = tmp_path / "outside.png"
    Image.new("RGB", (10, 10)).save(source)
    with pytest.raises(ValueError, match="outside"):
        cache.local(source)
