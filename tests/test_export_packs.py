import importlib
import sqlite3
from pathlib import Path


def test_a_resumed_export_numbers_new_packs_past_those_deleted_after_publication(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path("scripts").resolve()))
    module = importlib.import_module("export_cloudflare")
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE media (key TEXT PRIMARY KEY, object TEXT, offset INTEGER, size INTEGER, content_type TEXT)")
    # pack-0001 and pack-0007 were published and deleted; only pack-0003 is still on disk.
    db.executemany("INSERT INTO media VALUES(?,?,0,1,'image/webp')", [("a", "pack-0001.bin"), ("b", "pack-0007.bin")])
    (tmp_path / "pack-0003.bin").write_bytes(b"x")
    image = tmp_path / "c.webp"
    image.write_bytes(b"new")
    packs = module.Packs(tmp_path, db)
    packs.add("c", image)
    packs.close()
    assert db.execute("SELECT object FROM media WHERE key='c'").fetchone() == ("pack-0008.bin",)
    assert (tmp_path / "pack-0008.bin").read_bytes() == b"new"


def test_a_crop_tone_is_read_from_its_pack_and_cut_again_once_the_pack_is_gone(tmp_path, monkeypatch):
    import io

    from PIL import Image

    monkeypatch.syspath_prepend(str(Path("scripts").resolve()))
    module = importlib.import_module("export_cloudflare")
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE media (key TEXT PRIMARY KEY, object TEXT, offset INTEGER, size INTEGER, content_type TEXT)")
    packs = module.Packs(tmp_path, db)
    for key, colour in (("before", (0, 0, 0)), ("crop", (210, 190, 160))):
        buffer = io.BytesIO()
        Image.new("RGB", (30, 40), colour).save(buffer, "WEBP", lossless=True)
        (tmp_path / f"{key}.webp").write_bytes(buffer.getvalue())
        packs.add(key, tmp_path / f"{key}.webp")

    class Media:
        def __init__(self):
            self.cut = []

        def materialize(self, key):
            self.cut.append(key)
            return tmp_path / f"{key}.webp"

    media = Media()
    # Read from the pack still being written, past the image before it.
    assert module.image_tone(packs, media, "/atlas/media/crop.webp") == {"tone": "#d2bea0", "image_size": [30, 40]}
    assert media.cut == []
    packs.close()
    (tmp_path / packs.name).unlink()
    assert module.image_tone(packs, media, "/atlas/media/crop.webp")["tone"] == "#d2bea0"
    assert media.cut == ["crop"]
