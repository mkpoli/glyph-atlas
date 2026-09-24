import json
from contextlib import closing

import pytest

from glyph_atlas import tables
from glyph_atlas.corpus.collection import publish
from glyph_atlas.corpus.sources import discover
from glyph_atlas.corpus.wikisource import WikisourcePage
from glyph_atlas.corpus.wikisource_queue import Collector, work_of
from glyph_atlas.schema import Document, Page, PageText


class Client:
    def __init__(self):
        self.requests = []
        self.fetches = []

    def _api(self, params, *, bucket):
        self.requests.append(params.copy())
        if params["apnamespace"] == 0:
            return {"query": {"allpages": [{"pageid": 3, "ns": 0, "title": "作品/上"}]}}
        if not params.get("apcontinue"):
            return {"query": {"allpages": []}, "continue": {"apcontinue": "Next"}}
        return {"query": {"allpages": [
            {"pageid": 1, "ns": 250, "title": "Page:書.pdf/10"},
            {"pageid": 2, "ns": 250, "title": "Page:書.pdf/2"},
        ]}}

    def pages(self, titles):
        self.fetches.append(titles)
        ids = {"Page:書.pdf/10": 1, "Page:書.pdf/2": 2, "作品/上": 3}
        return [WikisourcePage(title=t, pageid=ids[t], revision=100+ids[t],
                              timestamp="2026-01-01T00:00:00Z", url="https://ja.wikisource.org/wiki/"+t,
                              namespace=250 if t.startswith("Page:") else 0, wikitext="トモ") for t in titles]


def test_discovery_resume_empty_continuation_and_work_identity(tmp_path):
    client = Client()
    collector = Collector(tmp_path, client=client, book_pause=0, min_free_bytes=0)
    collector.run(discover_batches=1)
    assert not collector.status()["discovery_complete"]
    assert collector.status()["works"] == {}
    collector = Collector(tmp_path, client=client, book_pause=0, min_free_bytes=0)
    result = collector.run()
    assert result["collected"] == 2
    assert client.requests[1]["apcontinue"] == "Next"
    assert collector.status()["discovery_complete"]
    key, _ = work_of("Page:書.pdf/2", 250)
    book = tmp_path / "books" / key
    docs = list(tables.read(book / "documents.parquet", Document))
    pages = list(tables.read(book / "pages.parquet", Page))
    assert docs[0].id == f"ws:ja:work:{key}"
    assert [page.id for page in pages] == ["ws:2", "ws:1"]
    assert all(page.document_id == docs[0].id for page in pages)
    assert not (book / "units.parquet").exists()
    assert collector.status()["character_crops"] == 0
    assert collector.status()["pages"] == 3


def test_resume_does_not_refetch_committed_pages(tmp_path):
    client = Client()
    collector = Collector(tmp_path, client=client, book_pause=0, min_free_bytes=0)
    while collector.discover_batch():
        pass
    key, _ = work_of("Page:書.pdf/2", 250)
    page = client.pages(["Page:書.pdf/2"])[0]
    with closing(collector.connect()) as db, db:
        db.execute("UPDATE works SET state='in_progress' WHERE id=?", (key,))
        db.execute("UPDATE pages SET state='done',body=?,revision=? WHERE id=2",
                   (json.dumps(page.as_dict()), page.revision))
    client.fetches.clear()
    collector.run()
    assert all("Page:書.pdf/2" not in batch for batch in client.fetches)
    assert collector.status()["works"] == {"done": 2}


def test_failure_keeps_pending_and_does_not_publish_missing_text(tmp_path):
    client = Client()
    collector = Collector(tmp_path, client=client, book_pause=0, min_free_bytes=0)
    while collector.discover_batch():
        pass
    def fail(titles):
        raise RuntimeError("maxlag")
    client.pages = fail
    collector.run()
    assert collector.status()["page_states"] == {"pending": 3}
    assert collector.status()["works"] == {"pending": 2}
    assert json.loads((tmp_path / "index.json").read_text())["books"] == []


def test_wikisource_publication_replaces_legacy_page_keeps_other_sources(tmp_path):
    collector = Collector(tmp_path / "wikisource-collection", client=Client(), book_pause=0, min_free_bytes=0)
    collector.run()
    base = tmp_path / "wikisource"
    base.mkdir()
    tables.write(base / "documents.parquet", [Document(id="ws:ja", title="Legacy search")], Document)
    tables.write(base / "pages.parquet", [Page(id="ws:1", document_id="ws:ja", seq=0, image="", width=0, height=0)], Page)
    tables.write(base / "page_texts.parquet", [PageText(page_id="ws:1", source="wikisource", text_raw="古")], PageText)
    result = publish(tmp_path, source="wikisource", min_free_bytes=0)
    assert result["published"] == 2
    corpus = next(item for item in discover(tmp_path) if item.name == "wikisource")
    assert result["tables"]["pages"] == 3
    assert [row.text_raw for row in tables.read(corpus.table("page_texts"), PageText)] == ["トモ"] * 3
    assert (base / "page_texts.parquet").exists()
    from glyph_atlas.corpus.index import CorpusIndex
    assert CorpusIndex(tmp_path / "corpus-index", tmp_path).summary("ト")["n_occurrences"] == 3
    from glyph_atlas.corpus.collection import archive_statistics
    assert archive_statistics(tmp_path)["text_works"] == 2


def test_storage_floor_uses_collection_volume(tmp_path, monkeypatch):
    from glyph_atlas.importers.honkoku_queue import OutOfSpace
    collector = Collector(tmp_path, client=Client(), min_free_bytes=5)
    class Space:
        free = 4
    monkeypatch.setattr("glyph_atlas.corpus.wikisource_queue.shutil.disk_usage", lambda path: Space())
    with pytest.raises(OutOfSpace):
        collector.discover_batch()


def test_invalid_discovery_does_not_finish_namespace(tmp_path):
    client = Client()
    client._api = lambda *args, **kwargs: {"query": {}}
    collector = Collector(tmp_path, client=client, min_free_bytes=0)
    with pytest.raises(RuntimeError):
        collector.discover_batch()
    assert not collector.status()["discovery_complete"]
    assert collector.status()["works"] == {}
