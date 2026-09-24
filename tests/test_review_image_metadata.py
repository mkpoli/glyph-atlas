from PIL import Image

from glyph_atlas.review import atlas


def test_image_checks_survive_a_process_cache_reset(tmp_path, monkeypatch):
    monkeypatch.setattr(atlas.images, "cache_root", lambda: tmp_path / "cache")
    picture = tmp_path / "page.jpg"
    Image.new("RGB", (40, 60), "white").save(picture)
    stamp = picture.stat().st_mtime_ns
    assert atlas.image_size(str(picture), stamp) == (40, 60)
    atlas.image_size.cache_clear()
    atlas.readable_image.cache_clear()
    def reopened(path):
        raise AssertionError("A completed check reopened the full source image")
    monkeypatch.setattr(atlas, "_pixels", reopened)
    assert atlas.image_size(str(picture), stamp) == (40, 60)
    assert atlas.readable_image(str(picture), stamp)


def test_changed_image_is_rechecked(tmp_path, monkeypatch):
    monkeypatch.setattr(atlas.images, "cache_root", lambda: tmp_path / "cache")
    picture = tmp_path / "page.jpg"
    Image.new("RGB", (40, 60), "white").save(picture)
    stamp = picture.stat().st_mtime_ns
    assert atlas.image_size(str(picture), stamp) == (40, 60)
    picture.write_bytes(b"truncated")
    assert atlas.image_size(str(picture), stamp + 1) is None


def test_broken_metadata_cache_is_rebuilt(tmp_path, monkeypatch):
    monkeypatch.setattr(atlas.images, "cache_root", lambda: tmp_path / "cache")
    picture = tmp_path / "page.jpg"
    Image.new("RGB", (40, 60), "white").save(picture)
    stamp = picture.stat().st_mtime_ns
    assert atlas.image_size(str(picture), stamp) == (40, 60)
    next((tmp_path / "cache").rglob("*.json")).write_text("{")
    atlas.image_size.cache_clear()
    assert atlas.image_size(str(picture), stamp) == (40, 60)
