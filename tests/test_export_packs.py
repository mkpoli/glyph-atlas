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
