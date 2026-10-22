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


def test_an_image_that_fails_to_copy_leaves_no_row_and_no_bytes(tmp_path, monkeypatch):
    import pytest

    monkeypatch.syspath_prepend(str(Path("scripts").resolve()))
    module = importlib.import_module("export_cloudflare")
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE media (key TEXT PRIMARY KEY, object TEXT, offset INTEGER, size INTEGER, content_type TEXT)")
    packs = module.Packs(tmp_path, db)
    (tmp_path / "a.webp").write_bytes(b"first")
    (tmp_path / "b.webp").write_bytes(b"broken")
    (tmp_path / "c.webp").write_bytes(b"third")
    packs.add("a", tmp_path / "a.webp")
    real = module.shutil.copyfileobj

    def fail(source, target, length):
        target.write(b"bro")
        raise OSError(12, "Cannot allocate memory")

    monkeypatch.setattr(module.shutil, "copyfileobj", fail)
    with pytest.raises(OSError):
        packs.add("b", tmp_path / "b.webp")
    monkeypatch.setattr(module.shutil, "copyfileobj", real)
    packs.add("c", tmp_path / "c.webp")
    packs.add("b", tmp_path / "b.webp")
    packs.close()
    pack = (tmp_path / packs.name).read_bytes()
    rows = {k: (o, s) for k, o, s in db.execute("SELECT key, offset, size FROM media")}
    assert {k: pack[o:o + s] for k, (o, s) in rows.items()} == {"a": b"first", "c": b"third", "b": b"broken"}


def test_rows_past_a_packs_end_are_dropped_and_packed_again(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path("scripts").resolve()))
    module = importlib.import_module("export_cloudflare")
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE media (key TEXT PRIMARY KEY, object TEXT, offset INTEGER, size INTEGER, content_type TEXT)")
    # A run killed after committing b's row and before its bytes reached pack-0002; pack-0001 was
    # published and deleted.
    (tmp_path / "pack-0002.bin").write_bytes(b"aaaa")
    db.executemany("INSERT INTO media VALUES(?,?,?,?,'image/webp')",
                   [("old", "pack-0001.bin", 0, 9), ("a", "pack-0002.bin", 0, 4), ("b", "pack-0002.bin", 4, 4)])
    db.commit()
    packs = module.Packs(tmp_path, db)
    assert {k for k, in db.execute("SELECT key FROM media")} == {"old", "a"}
    (tmp_path / "b.webp").write_bytes(b"bbbb")
    packs.add("b", tmp_path / "b.webp")
    packs.close()
    obj, offset, size = db.execute("SELECT object, offset, size FROM media WHERE key='b'").fetchone()
    assert obj == "pack-0003.bin" and (tmp_path / obj).read_bytes()[offset:offset + size] == b"bbbb"


def test_sync_puts_the_open_packs_bytes_on_disk_before_a_commit(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path("scripts").resolve()))
    module = importlib.import_module("export_cloudflare")
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE media (key TEXT PRIMARY KEY, object TEXT, offset INTEGER, size INTEGER, content_type TEXT)")
    packs = module.Packs(tmp_path, db)
    (tmp_path / "a.webp").write_bytes(b"aaaa")
    packs.add("a", tmp_path / "a.webp")
    synced = []
    monkeypatch.setattr(module.os, "fsync", lambda fd: synced.append(fd))
    packs.sync()
    assert synced == [packs.file.fileno()]
    assert (tmp_path / packs.name).read_bytes() == b"aaaa"
