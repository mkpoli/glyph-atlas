import json
import sqlite3

from glyph_atlas.review.collection import status, worker_active


def test_progress_distinguishes_import_from_extraction(tmp_path, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr("glyph_atlas.review.collection.shutil.disk_usage", lambda path: SimpleNamespace(free=10*1024**3))
    directory = tmp_path / "honkoku-collection"
    directory.mkdir()
    with sqlite3.connect(directory / "queue.sqlite") as db:
        db.executescript("CREATE TABLE books(entry_id,label,state,finished_at,pages,transcriptions);"
                         "CREATE TABLE projects(state); CREATE TABLE collections(state);")
        db.execute("INSERT INTO books VALUES('one','One','done','2026-01-01T00:00:00Z',10,8)")
        db.execute("INSERT INTO books VALUES('two','Two','pending',NULL,NULL,NULL)")
    result = status(tmp_path)
    assert result["total"] == 2
    assert result["completed"] == 1
    assert result["pending"] == 1
    assert result["pages"] == 10
    assert result["text_pages"] == 8
    assert result["status"] == "idle"
    assert result["current"] is None
    assert not worker_active(directory / "worker.lock")
    assert [source["id"] for source in result["sources"]] == ["honkoku", "wikisource"]
    assert result["sources"][0]["discovery_complete"] is True
    assert result["sources"][1]["discovery_complete"] is False


def test_progress_without_queue_keeps_registered_heike(tmp_path):
    book = tmp_path / "heike"
    book.mkdir()
    (book / "MANIFEST.json").write_text(json.dumps({"collection": {"title":"Heike", "volumes":12,"pages":576}}))
    result = status(tmp_path)
    assert result["total"] == 0
    assert result["additions"][0]["pages"] == 576


def test_wikisource_discovery_and_archive_counts_are_separate(tmp_path, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr("glyph_atlas.review.collection.shutil.disk_usage", lambda path: SimpleNamespace(free=10*1024**3))
    collection = tmp_path / "wikisource-collection"
    collection.mkdir()
    (collection / "status.json").write_text(json.dumps({"works": {"pending": 8, "done": 2},
        "page_states": {"pending": 50, "done": 10}, "pages": 10, "text_pages": 9,
        "discovery_complete": False}))
    (collection / "published.json").write_text(json.dumps({"published": 1}))
    index = tmp_path / "corpus-index"
    index.mkdir()
    (index / "archive.json").write_text(json.dumps({"character_crops": 100, "works_with_crops": 3,
        "text_works": 12, "sources": {"honkoku-lines": {"character_crops": 40}}}))
    result = status(tmp_path)
    ws = result["sources"][1]
    assert ws["total"] == 10 and ws["completed"] == 2 and ws["published_books"] == 1
    assert ws["discovered_pages"] == 60 and ws["text_pages"] == 9
    assert ws["character_crops"] == 0 and ws["phase"] == "discovering"
    assert result["archive"]["character_crops"] == 100


def test_disk_floor_checks_external_collection_storage(tmp_path, monkeypatch):
    from types import SimpleNamespace
    collection = tmp_path / "wikisource-collection"
    collection.mkdir()
    def storage(path):
        return SimpleNamespace(free=100*1024**3 if path == collection else 1)
    monkeypatch.setattr("glyph_atlas.review.collection.shutil.disk_usage", storage)
    assert status(tmp_path)["sources"][1]["status"] == "idle"
