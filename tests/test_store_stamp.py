from pathlib import Path

from glyph_atlas.review import store


def test_a_file_that_vanishes_between_looking_and_reading_is_absent(tmp_path: Path, monkeypatch):
    wal = tmp_path / "review.sqlite-wal"
    wal.write_bytes(b"x")
    real = Path.stat

    # SQLite removes the -wal file when another process closes the last connection: the file is
    # there when looked for and gone when read.
    def vanished(self, *args, **kwargs):
        if self == wal:
            raise FileNotFoundError(self)
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", vanished)
    assert store._stamp(wal) is None
    assert store._stamp(tmp_path / "absent") is None
    monkeypatch.setattr(Path, "stat", real)
    assert store._stamp(wal) == (wal.stat().st_mtime_ns, 1)
