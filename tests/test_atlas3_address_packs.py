import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "atlas3"))
import address_packs


def export(tmp_path):
    db = sqlite3.connect(tmp_path / "corpus.sqlite")
    db.execute("CREATE TABLE media(key TEXT PRIMARY KEY, object TEXT, offset INTEGER, size INTEGER, content_type TEXT)")
    db.execute("CREATE TABLE corpus_units(id TEXT PRIMARY KEY, object TEXT, offset INTEGER, size INTEGER)")
    (tmp_path / "pack-10001.bin").write_bytes(b"aaaabbbb")
    (tmp_path / "corpus-0001.bin").write_bytes(b'{"a":1}{"b":2}')
    db.executemany("INSERT INTO media VALUES(?,?,?,?,'image/webp')", [("k1", "pack-10001.bin", 0, 4), ("k2", "pack-10001.bin", 4, 4)])
    db.executemany("INSERT INTO corpus_units VALUES(?,?,?,?)", [("u1", "corpus-0001.bin", 0, 7), ("u2", "corpus-0001.bin", 7, 7)])
    db.commit()
    db.close()
    return tmp_path / "corpus.sqlite"


def rows(catalogue, table):
    with sqlite3.connect(catalogue) as db:
        return db.execute(f"SELECT object, offset, size FROM {table} ORDER BY 1, 2").fetchall()


def test_packs_take_their_digest_and_rows_follow(tmp_path):
    catalogue = export(tmp_path)
    counts = address_packs.address(catalogue)
    assert counts["moved"] == 2 and counts["objects"] == 2 and counts["bytes"] == 8 + 14
    for table, body in (("media", b"aaaabbbb"), ("corpus_units", b'{"a":1}{"b":2}')):
        objects = {o for o, _, _ in rows(catalogue, table)}
        assert len(objects) == 1
        (name,) = objects
        assert address_packs.ADDRESSED.fullmatch(name)
        assert (tmp_path / name).read_bytes() == body
    assert not (tmp_path / "pack-10001.bin").exists() and not (tmp_path / "corpus-0001.bin").exists()
    # A second run finds nothing to move.
    assert address_packs.address(catalogue)["moved"] == 0


def test_a_dry_run_changes_nothing(tmp_path):
    catalogue = export(tmp_path)
    before = rows(catalogue, "media")
    assert address_packs.address(catalogue, dry_run=True)["to_move"] == 2
    assert rows(catalogue, "media") == before and (tmp_path / "pack-10001.bin").exists()
    assert not (tmp_path / "packs").exists()


def test_a_move_recorded_but_not_made_finishes_next_run(tmp_path, monkeypatch):
    catalogue = export(tmp_path)
    real = Path.rename
    monkeypatch.setattr(Path, "rename", lambda self, target: (_ for _ in ()).throw(OSError("stopped")))
    with pytest.raises(OSError):
        address_packs.address(catalogue)
    monkeypatch.setattr(Path, "rename", real)
    assert address_packs.address(catalogue)["finished"] == 2
    for table in ("media", "corpus_units"):
        for name, _, _ in rows(catalogue, table):
            assert (tmp_path / name).exists()


def test_a_short_pack_stops_the_run(tmp_path):
    catalogue = export(tmp_path)
    (tmp_path / "pack-10001.bin").write_bytes(b"aaaa")
    with pytest.raises(SystemExit, match="shorter"):
        address_packs.address(catalogue)
    assert {o for o, _, _ in rows(catalogue, "media")} == {"pack-10001.bin"}


def test_a_pack_written_twice_stops_the_run(tmp_path):
    catalogue = export(tmp_path)
    with sqlite3.connect(catalogue) as db:
        db.execute("INSERT INTO media VALUES('k3','pack-10001.bin',2,4,'image/webp')")
    with pytest.raises(SystemExit, match="two rows"):
        address_packs.address(catalogue)


def test_a_pack_number_used_again_is_addressed_as_new(tmp_path):
    catalogue = export(tmp_path)
    address_packs.address(catalogue)
    # The exporter numbers its next pack from the bottom again.
    (tmp_path / "pack-10001.bin").write_bytes(b"cccc")
    with sqlite3.connect(catalogue) as db:
        db.execute("INSERT INTO media VALUES('k3','pack-10001.bin',0,4,'image/webp')")
    assert address_packs.address(catalogue)["moved"] == 1
    for name, offset, size in rows(catalogue, "media"):
        assert (tmp_path / name).read_bytes()[offset:offset + size] in (b"aaaa", b"bbbb", b"cccc")
    with sqlite3.connect(catalogue) as db:
        (name,) = db.execute("SELECT object FROM media WHERE key='k3'").fetchone()
    assert (tmp_path / name).read_bytes() == b"cccc"


def test_packs_of_the_same_bytes_share_an_address(tmp_path):
    catalogue = export(tmp_path)
    (tmp_path / "pack-10002.bin").write_bytes(b"aaaabbbb")
    with sqlite3.connect(catalogue) as db:
        db.execute("INSERT INTO media VALUES('k3','pack-10002.bin',0,4,'image/webp')")
    address_packs.address(catalogue)
    assert len({o for o, _, _ in rows(catalogue, "media")}) == 1
    assert address_packs.address(catalogue)["moved"] == 0
