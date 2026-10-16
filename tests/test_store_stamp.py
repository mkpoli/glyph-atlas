from pathlib import Path

from glyph_atlas import tables
from glyph_atlas.review.store import Store
from glyph_atlas.schema import Document


def test_a_wal_file_removed_while_the_store_looks_for_imports_is_absent(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("GLYPH_ATLAS_CACHE", str(tmp_path / "cache"))
    root = tmp_path / "dataset"
    root.mkdir()
    tables.write(root / "documents.parquet", [Document(id="d", title="資料")], Document)
    store = Store(root)
    assert [document.id for document in store.documents()] == ["d"]
    wal = Path(str(store.path) + "-wal")
    real_exists, real_stat = Path.exists, Path.stat

    # SQLite removes the -wal file when another process closes the last connection: the file is
    # there when looked for and gone when read.
    monkeypatch.setattr(Path, "exists", lambda self, **kw: True if self == wal else real_exists(self, **kw))

    def vanished(self, *args, **kwargs):
        if self == wal:
            raise FileNotFoundError(self)
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", vanished)
    assert [document.id for document in store.documents()] == ["d"]
