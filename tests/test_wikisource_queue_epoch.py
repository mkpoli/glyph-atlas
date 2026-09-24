"""Rediscovery: a later walk finds source material the earlier one could not.

The collection queues mark a completed walk `done` and never revisit it, so a
scheduled run after the first walk queues nothing. These cover the epoch that
reopens walked rows — and the two things that make reopening correct rather than
merely frequent: the cached discovery response is dropped so the walk sees the
current first batch, and a work whose book is already written is rebuilt rather
than left stale.
"""

from kuzushiji_atlas import tables
from kuzushiji_atlas.corpus.wikisource import WikisourcePage
from kuzushiji_atlas.corpus.wikisource_queue import Collector, work_of
from kuzushiji_atlas.schema import Page


class Client:
    """A Wikisource whose 書.pdf has two pages and 作品/上 has one."""

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
        return [WikisourcePage(title=t, pageid=ids[t], revision=100 + ids[t],
                               timestamp="2026-01-01T00:00:00Z",
                               url="https://ja.wikisource.org/wiki/" + t,
                               namespace=250 if t.startswith("Page:") else 0,
                               wikitext="トモ") for t in titles]


class GrownClient(Client):
    """The same source one epoch later: 書.pdf has gained a page."""

    def _api(self, params, *, bucket):
        self.requests.append(params.copy())
        if params["apnamespace"] == 0:
            return {"query": {"allpages": [{"pageid": 3, "ns": 0, "title": "作品/上"}]}}
        if not params.get("apcontinue"):
            return {"query": {"allpages": []}, "continue": {"apcontinue": "Next"}}
        return {"query": {"allpages": [
            {"pageid": 1, "ns": 250, "title": "Page:書.pdf/10"},
            {"pageid": 2, "ns": 250, "title": "Page:書.pdf/2"},
            {"pageid": 4, "ns": 250, "title": "Page:書.pdf/11"},
        ]}}

    def pages(self, titles):
        self.fetches.append(titles)
        ids = {"Page:書.pdf/10": 1, "Page:書.pdf/2": 2, "Page:書.pdf/11": 4, "作品/上": 3}
        return [WikisourcePage(title=t, pageid=ids[t], revision=100 + ids[t],
                               timestamp="2026-01-01T00:00:00Z",
                               url="https://ja.wikisource.org/wiki/" + t,
                               namespace=250 if t.startswith("Page:") else 0,
                               wikitext="トモ") for t in titles]


def test_within_the_epoch_a_finished_walk_is_not_restarted(tmp_path):
    Collector(tmp_path, client=Client(), book_pause=0, min_free_bytes=0).run()
    fresh = Client()
    result = Collector(tmp_path, client=fresh, book_pause=0, min_free_bytes=0).run()
    assert fresh.requests == []  # discovery did not walk again
    assert result["collected"] == 0 and result["reopened"] == 0


def test_a_new_epoch_rediscovers_new_pages_and_rebuilds_their_book(tmp_path):
    Collector(tmp_path, client=Client(), book_pause=0, min_free_bytes=0).run()
    key, _ = work_of("Page:書.pdf/10", 250)
    book = tmp_path / "books" / key
    assert len(list(tables.read(book / "pages.parquet", Page))) == 2

    # A cached discovery response would replay the old first batch; the epoch must drop it.
    stale = tmp_path / "cache" / "discovery"
    stale.mkdir(parents=True)
    (stale / "first-batch.json").write_text("{}")

    grown = GrownClient()
    grown.cache = tmp_path / "cache"
    result = Collector(tmp_path, client=grown, book_pause=0, min_free_bytes=0,
                       discovery_epoch_seconds=0).run()

    assert not (stale / "first-batch.json").exists()
    assert result["reopened"] == 1 and result["collected"] == 1
    pages = list(tables.read(book / "pages.parquet", Page))
    assert len(pages) == 3  # rebuilt from every page the queue holds, new one included
    assert Collector(tmp_path, client=Client(), book_pause=0,
                     min_free_bytes=0).status()["works"]["done"] == 2


def test_an_interrupted_walk_resumes_rather_than_restarting(tmp_path):
    """A walk still open is resumed from its cursor; rows are not reopened mid-walk."""
    client = Client()
    collector = Collector(tmp_path, client=client, book_pause=0, min_free_bytes=0,
                          discovery_epoch_seconds=0)
    collector.run(discover_batches=1)  # one namespace batch, then stop
    assert not collector.status()["discovery_complete"]

    # Epoch interval elapsed, but the walk never finished: still no reset.
    resumed = Collector(tmp_path, client=Client(), book_pause=0, min_free_bytes=0,
                        discovery_epoch_seconds=0)
    resumed._maybe_open_discovery_epoch()
    assert resumed.status()["discovery"] == collector.status()["discovery"]
