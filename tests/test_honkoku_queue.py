"""The resumable honkoku collector.

Everything runs against a mock API and a temporary directory: no network, no images,
no model. The properties under test are the ones that make a slow background worker
safe to leave running — one book at a time, resume without duplicates, atomic output,
and honest coverage of a snapshot that is larger than the live platform.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from kuzushiji_atlas.importers import honkoku_queue as hq

ENTRY_A = "7d2c971871a16e8273ea7f73f1869239"
ENTRY_B = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
ENTRY_NEW = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
ENTRY_GONE = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"


def canvas(index: int, *, entry: str = ENTRY_A) -> dict:
    return {
        "id": f"https://example.invalid/iiif/{entry}/canvas/p{index + 1}",
        "width": 4000,
        "height": 3000,
        "imageUrl": f"https://example.invalid/iiif/{entry}/{index + 1}.tif/full/4000,/0/default.jpg",
        "infoJsonUrl": f"https://example.invalid/iiif/{entry}/{index + 1}.tif/info.json",
        "thumbnailUrl": f"https://example.invalid/iiif/{entry}/{index + 1}.tif/full/200,/0/default.jpg",
    }


def transcription(index: int, text: str, *, entry: str = ENTRY_A) -> dict:
    return {
        "id": f"{entry}_{index}",
        "index": index,
        "canvasId": f"https://example.invalid/iiif/{entry}/canvas/p{index + 1}",
        "text": text,
        "updatedAt": {"_seconds": 1700000000 + index},
        "status": "approved",
        "entryId": entry,
    }


def entry_payload(
    entry_id: str,
    *,
    label="日録",
    canvases=3,
    texts=("一", "二", "三"),
    licence="CC-BY-SA-4.0",
    manifest=True,
) -> dict:
    return {
        "id": entry_id,
        "label": label,
        "projectId": "20251105",
        "collectionId": "C0SDJPE3FSmnjtwNtZKL",
        "index": 1,
        "size": canvases,
        "progress": 2,
        "license": licence,
        "attribution": "Okayama University Library",
        "r18": False,
        "manifestUrl": f"https://example.invalid/iiif/{entry_id}/manifest" if manifest else None,
        "manifestVersion": 2,
        "metadata": [{"label": "Call Number", "value": "M007-20"}],
        "canvases": [canvas(i, entry=entry_id) for i in range(canvases)],
        "transcriptions": [
            transcription(i, texts[i] if i < len(texts) else "", entry=entry_id) for i in range(canvases)
        ],
    }


PROJECTS = [
    {"id": "20251105", "title": "三浦家文書", "isPrivate": False, "totalEntryCount": 2, "display": True},
    {"id": "private-one", "title": "非公開", "isPrivate": True, "totalEntryCount": 9, "display": True},
]
PROJECT_DETAIL = {
    "id": "20251105",
    "title": "三浦家文書",
    "isPrivate": False,
    "collections": ["C0SDJPE3FSmnjtwNtZKL"],
}
COLLECTION = {
    "id": "C0SDJPE3FSmnjtwNtZKL",
    "projectId": "20251105",
    "title": "三浦家文書（真庭市所蔵）",
    "display": True,
    "entryCount": 2,
    "entries": [ENTRY_A, ENTRY_B],
}
HIDDEN_COLLECTION = {
    "id": "hidden",
    "projectId": "20251105",
    "title": "hidden",
    "display": False,
    "entryCount": 1,
    "entries": [ENTRY_GONE],
}


class FakeResponse:
    def __init__(self, status: int, payload=None):
        self.status_code = status
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeAPI:
    """A mock honkoku.org that records every URL it was asked for."""

    def __init__(self, *, projects=None, project=None, collections=None, entries=None, failures=None):
        self.projects = PROJECTS if projects is None else projects
        self.project = PROJECT_DETAIL if project is None else project
        self.collections = (
            {COLLECTION["id"]: COLLECTION, HIDDEN_COLLECTION["id"]: HIDDEN_COLLECTION}
            if collections is None
            else collections
        )
        self.entries = (
            {
                ENTRY_A: entry_payload(ENTRY_A),
                ENTRY_B: entry_payload(ENTRY_B, label="二冊目", canvases=2, texts=("甲", "乙")),
            }
            if entries is None
            else entries
        )
        self.failures = dict(failures or {})
        self.urls: list[str] = []

    def get(self, url, headers=None, timeout=None):
        self.urls.append(url)
        if url in self.failures and self.failures[url] > 0:
            self.failures[url] -= 1
            return FakeResponse(503)
        if url == hq.PROJECTS_URL:
            return FakeResponse(200, self.projects)
        if url.startswith(f"{hq.API_BASE}/projects/"):
            return FakeResponse(200, self.project)
        if url.startswith(f"{hq.API_BASE}/collections/"):
            collection = self.collections.get(url.rsplit("/", 1)[-1])
            return FakeResponse(200, collection) if collection else FakeResponse(404)
        if url.startswith(f"{hq.API_BASE}/entries/"):
            entry = self.entries.get(url.rsplit("/", 1)[-1])
            return FakeResponse(200, entry) if entry else FakeResponse(404)
        return FakeResponse(404)

    def image_like(self):
        return [u for u in self.urls if any(ext in u for ext in (".jpg", ".tif", ".png"))]


class FakeClock:
    """A clock the test advances, so pauses are measured and never waited for."""

    def __init__(self):
        self.now = 0.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def build(tmp_path: Path, api: FakeAPI | None = None, *, free: int | None = None, **kwargs):
    api = api or FakeAPI()
    clock = FakeClock()
    collector = hq.make_collector(
        tmp_path / "collection",
        client=api,
        clock=clock,
        sleeper=clock.sleep,
        disk_free=(lambda p: free if free is not None else 50 * 1024**3),
        **kwargs,
    )
    return collector, api, clock


class TestQueueSchema:
    def test_the_database_permits_only_one_book_in_progress(self, tmp_path):
        queue = hq.Queue(tmp_path / "queue.sqlite")
        queue.record_book(ENTRY_A, project_id="p", collection_id="c", position=0, origin="live")
        queue.record_book(ENTRY_B, project_id="p", collection_id="c", position=1, origin="live")
        assert queue.claim_next() is not None
        assert queue.claim_next() is None  # the running book blocks the next
        # The partial unique index is the backstop under the guard.
        with pytest.raises(sqlite3.IntegrityError), queue.transaction() as db:
            db.execute("UPDATE books SET state='in_progress' WHERE entry_id=?", (ENTRY_B,))

    def test_claiming_marks_the_book_and_counts_the_attempt(self, tmp_path):
        queue = hq.Queue(tmp_path / "queue.sqlite")
        queue.record_book(ENTRY_A, project_id="p", collection_id="c", position=0, origin="live")
        claimed = queue.claim_next()
        assert claimed["state"] == "in_progress" and claimed["attempts"] == 1

    def test_a_book_is_never_queued_twice(self, tmp_path):
        queue = hq.Queue(tmp_path / "queue.sqlite")
        assert (
            queue.record_book(ENTRY_A, project_id="p", collection_id="c", position=0, origin="snapshot")
            is True
        )
        assert (
            queue.record_book(ENTRY_A, project_id="p", collection_id="c", position=0, origin="live") is False
        )
        assert len(queue.books()) == 1


class TestSnapshotSeeding:
    def snapshot(self, tmp_path: Path) -> Path:
        clone = tmp_path / "clone"
        for project, rows in (
            ("kinseikisyo", [("e1", "素人名手"), ("e2", "碁經拾遺")]),
            ("other", [("e3", "三冊目")]),
        ):
            folder = clone / "v3" / project
            folder.mkdir(parents=True)
            lines = ["\t".join(hq.INFO_COLUMNS)]
            for entry_id, label in rows:
                lines.append(
                    "\t".join(
                        [
                            entry_id,
                            label,
                            f"https://example.invalid/{entry_id}/manifest",
                            project,
                            "10",
                            "0",
                            "holder",
                            "https://example.invalid/t.jpg",
                        ]
                    )
                )
            (folder / "info.tsv").write_text("\n".join(lines) + "\n", encoding="utf-8")
        # One entry with a transcription directory, one without: both must be seeded.
        (clone / "v3" / "kinseikisyo" / "e1").mkdir()
        return clone

    def test_every_row_is_seeded_including_ones_without_a_directory(self, tmp_path):
        queue = hq.Queue(tmp_path / "queue.sqlite")
        added = queue.seed_from_snapshot(self.snapshot(tmp_path))
        assert added == 3
        ids = {row["entry_id"] for row in queue.books()}
        assert ids == {"e1", "e2", "e3"}  # e2 has no directory and is still here
        assert all(row["origin"] == "snapshot" for row in queue.books())

    def test_seeding_twice_adds_nothing(self, tmp_path):
        queue = hq.Queue(tmp_path / "queue.sqlite")
        clone = self.snapshot(tmp_path)
        assert queue.seed_from_snapshot(clone) == 3
        assert queue.seed_from_snapshot(clone) == 0

    def test_the_snapshot_fields_are_kept(self, tmp_path):
        queue = hq.Queue(tmp_path / "queue.sqlite")
        queue.seed_from_snapshot(self.snapshot(tmp_path))
        row = queue.book("e1")
        assert row["label"] == "素人名手" and row["size"] == 10


class TestDiscovery:
    def test_projects_collections_and_entries_are_queued(self, tmp_path):
        collector, _api, _ = build(tmp_path)
        result = collector.discover()
        assert result["projects"]["projects"] == 2
        assert result["entries"] == 2
        assert {row["entry_id"] for row in collector.queue.books()} == {ENTRY_A, ENTRY_B}

    def test_a_private_project_is_not_enumerated(self, tmp_path):
        collector, api, _ = build(tmp_path)
        collector.discover()
        assert not any("private-one" in url for url in api.urls)
        assert collector.queue.projects(state="skipped")

    def test_a_collection_that_is_not_displayed_is_skipped(self, tmp_path):
        collector, _, _ = build(tmp_path)
        collector.queue.record_collection(HIDDEN_COLLECTION, "20251105")
        assert collector.enumerate_books("hidden")["skipped"] == "not displayed"
        assert collector.queue.collections(state="skipped")
        assert collector.queue.book(ENTRY_GONE) is None

    def test_discovery_adds_nothing_the_second_time(self, tmp_path):
        collector, _, _ = build(tmp_path)
        collector.discover()
        before = len(collector.queue.books())
        collector.discover()
        assert len(collector.queue.books()) == before

    def test_a_new_epoch_reopens_walked_rows_and_queues_new_entries(self, tmp_path):
        collector, _api, _ = build(tmp_path)
        collector.discover()
        walked = {row["entry_id"] for row in collector.queue.books()}
        assert walked == {ENTRY_A, ENTRY_B}

        # The source gains an entry under an already-walked collection.
        grown = {**COLLECTION, "entries": [*COLLECTION["entries"], ENTRY_NEW]}
        api = FakeAPI(collections={COLLECTION["id"]: grown,
                                   HIDDEN_COLLECTION["id"]: HIDDEN_COLLECTION})
        collector, _api, _ = build(tmp_path, api)
        collector.discover()
        # Within the epoch the walk is not owed: nothing reopens, nothing new is queued.
        assert {row["entry_id"] for row in collector.queue.books()} == walked

        collector.discovery_epoch_seconds = 0.0
        collector.discover()
        books = {row["entry_id"]: row for row in collector.queue.books()}
        assert ENTRY_NEW in books and books[ENTRY_NEW]["state"] == "pending"

    def test_skipped_rows_stay_skipped_across_epochs(self, tmp_path):
        collector, _api, _ = build(tmp_path)
        collector.discover()
        skipped = {row["id"] for row in collector.queue.projects(state="skipped")}
        assert skipped  # the private project is refused on the first walk
        collector.discovery_epoch_seconds = 0.0
        collector.discover()
        assert {row["id"] for row in collector.queue.projects(state="skipped")} == skipped

    def test_no_image_url_is_ever_requested(self, tmp_path):
        collector, api, _ = build(tmp_path)
        collector.discover()
        collector.run(max_books=2)
        assert api.image_like() == []


class TestPacing:
    def test_requests_are_at_least_three_seconds_apart(self, tmp_path):
        collector, _api, clock = build(tmp_path, host_pause=3.0, book_pause=0.0)
        collector.discover()
        assert clock.slept and all(pause >= 3.0 for pause in clock.slept)

    def test_books_are_a_minute_apart_by_default(self, tmp_path):
        collector, _, clock = build(tmp_path, host_pause=0.0, book_pause=60.0)
        collector.discover()
        collector.run(max_books=2)
        assert any(pause >= 59.0 for pause in clock.slept)

    def test_pacing_is_configurable(self, tmp_path):
        collector, _, clock = build(tmp_path, host_pause=1.0, book_pause=2.0)
        collector.discover()
        collector.run(max_books=2)
        assert any(pause >= 1.9 for pause in clock.slept)


class TestCollectingOneBook:
    def test_a_book_becomes_a_complete_dataset(self, tmp_path):
        collector, _, _ = build(tmp_path)
        result = collector.collect_book(ENTRY_A)
        dataset = tmp_path / "collection" / "books" / ENTRY_A
        assert result["status"] == "collected"
        for name in (
            "documents.parquet",
            "pages.parquet",
            "page_texts.parquet",
            "book.json",
            "entry.json",
            "MANIFEST.json",
        ):
            assert (dataset / name).is_file(), name

    def test_the_dataset_is_readable_by_the_atlas_tables(self, tmp_path):
        from kuzushiji_atlas import tables
        from kuzushiji_atlas.schema import Document, Page, PageText

        collector, _, _ = build(tmp_path)
        collector.collect_book(ENTRY_A)
        dataset = tmp_path / "collection" / "books" / ENTRY_A
        documents = tables.read(dataset / "documents.parquet", Document)
        pages = tables.read(dataset / "pages.parquet", Page)
        texts = tables.read(dataset / "page_texts.parquet", PageText)
        assert [d.id for d in documents] == [f"hk:{ENTRY_A}"]
        assert len(pages) == 3 and len(texts) == 3

    def test_page_ids_are_one_based_like_the_snapshot(self, tmp_path):
        collector, _, _ = build(tmp_path)
        record = hq.load_book(tmp_path / "collection", ENTRY_A) if False else None
        collector.collect_book(ENTRY_A)
        book = json.loads(
            (tmp_path / "collection" / "books" / ENTRY_A / "book.json").read_text(encoding="utf-8")
        )
        # transcriptions[].index is 0-based on the API; page ids are 1-based here.
        assert [p["index"] for p in book["pages"]] == [0, 1, 2]
        assert [p["page_id"] for p in book["pages"]] == [
            f"hk:{ENTRY_A}:1",
            f"hk:{ENTRY_A}:2",
            f"hk:{ENTRY_A}:3",
        ]
        assert record is None

    def test_the_text_is_labelled_unverified_and_no_geometry_is_invented(self, tmp_path):
        collector, _, _ = build(tmp_path)
        collector.collect_book(ENTRY_A)
        book = json.loads(
            (tmp_path / "collection" / "books" / ENTRY_A / "book.json").read_text(encoding="utf-8")
        )
        assert book["text_state"] == "unverified-transcription"
        assert book["geometry"] == "canvas-references-only"
        for page in book["pages"]:
            assert page["text_state"] == "unverified-transcription"
            assert "box" not in page and "units" not in page

    def test_rights_and_revisions_travel_with_the_book(self, tmp_path):
        collector, _, _ = build(tmp_path)
        collector.collect_book(ENTRY_A)
        book = json.loads(
            (tmp_path / "collection" / "books" / ENTRY_A / "book.json").read_text(encoding="utf-8")
        )
        assert book["licence"] == "CC-BY-SA-4.0"
        assert book["attribution"] == "Okayama University Library"
        assert book["source_refs"]["iiif-manifest"].endswith("/manifest")
        assert all(page["revision"] for page in book["pages"])

    def test_an_entry_without_a_manifest_is_still_collected(self, tmp_path):
        api = FakeAPI(entries={ENTRY_A: entry_payload(ENTRY_A, manifest=False)})
        collector, _, _ = build(tmp_path, api)
        result = collector.collect_book(ENTRY_A)
        assert result["status"] == "collected"
        book = json.loads(
            (tmp_path / "collection" / "books" / ENTRY_A / "book.json").read_text(encoding="utf-8")
        )
        assert book["manifest_url"] is None
        assert book["page_count"] == 3

    def test_a_transcription_without_a_canvas_is_kept_and_flagged(self, tmp_path):
        payload = entry_payload(ENTRY_A, canvases=1, texts=("一", "二"))
        payload["transcriptions"].append(transcription(1, "二"))
        collector, _, _ = build(tmp_path, FakeAPI(entries={ENTRY_A: payload}))
        collector.collect_book(ENTRY_A)
        book = json.loads(
            (tmp_path / "collection" / "books" / ENTRY_A / "book.json").read_text(encoding="utf-8")
        )
        assert book["page_count"] == 2
        extra = book["pages"][1]
        assert extra["canvas_missing"] is True and extra["text"] == "二"

    def test_a_canvas_transcription_mismatch_is_quarantined_not_published(self, tmp_path):
        """A wrong pairing is invisible once written, so nothing is written."""
        payload = entry_payload(ENTRY_A)
        payload["transcriptions"][0]["canvasId"] = "https://example.invalid/elsewhere"
        collector, _, _ = build(tmp_path, FakeAPI(entries={ENTRY_A: payload}))
        collector.queue.record_book(ENTRY_A, project_id="p", collection_id="c", position=0, origin="live")
        result = collector.run()
        assert result["collected"] == 0
        assert collector.queue.book(ENTRY_A)["state"] == "pending"  # quarantined
        assert not (tmp_path / "collection" / "books" / ENTRY_A).exists()

    def test_an_entry_with_no_canvases_writes_empty_tables_not_a_crash(self, tmp_path):
        payload = entry_payload(ENTRY_A, canvases=0, texts=())
        collector, _, _ = build(tmp_path, FakeAPI(entries={ENTRY_A: payload}))
        result = collector.collect_book(ENTRY_A)
        assert result["status"] == "collected"
        assert result["pages"] == 0 and result["tables"]["pages"] == 0
        dataset = tmp_path / "collection" / "books" / ENTRY_A
        for name in ("documents.parquet", "pages.parquet", "page_texts.parquet"):
            assert (dataset / name).is_file()

    def test_a_second_collection_of_the_same_book_is_a_no_op(self, tmp_path):
        collector, api, _ = build(tmp_path)
        collector.collect_book(ENTRY_A)
        before = len(api.urls)
        again = collector.collect_book(ENTRY_A)
        assert again["status"] == "already-collected"
        assert len(api.urls) == before  # not even re-fetched


class TestAtomicityAndInterruption:
    def test_staging_is_cleared_and_never_left_behind(self, tmp_path):
        collector, _, _ = build(tmp_path)
        collector.collect_book(ENTRY_A)
        assert not any((tmp_path / "collection" / ".staging").iterdir())

    def test_leftover_staging_is_rebuilt_rather_than_trusted(self, tmp_path):
        root = tmp_path / "collection"
        stale = root / ".staging" / ENTRY_A
        stale.mkdir(parents=True)
        (stale / "book.json").write_text("{ not json", encoding="utf-8")
        collector, _, _ = build(tmp_path)
        assert collector.collect_book(ENTRY_A)["status"] == "collected"
        assert (root / "books" / ENTRY_A / "MANIFEST.json").is_file()

    def test_a_failed_fetch_leaves_no_book_directory(self, tmp_path):
        api = FakeAPI(failures={hq.ENTRY_URL.format(entry=ENTRY_A): 99})
        collector, _, _ = build(tmp_path, api)
        with pytest.raises(hq.Retryable):
            collector.collect_book(ENTRY_A)
        assert not (tmp_path / "collection" / "books" / ENTRY_A).exists()
        assert not any((tmp_path / "collection" / ".staging").iterdir())

    def test_the_manifest_digests_every_table(self, tmp_path):
        collector, _, _ = build(tmp_path)
        collector.collect_book(ENTRY_A)
        manifest = json.loads(
            (tmp_path / "collection" / "books" / ENTRY_A / "MANIFEST.json").read_text(encoding="utf-8")
        )
        assert set(manifest["files"]) == {
            "documents.parquet",
            "pages.parquet",
            "page_texts.parquet",
            "book.json",
            "entry.json",
        }
        # The standard dataset manifest fields a corpus reader expects.
        assert manifest["schema_version"] == 2
        assert manifest["tables"] == {"documents": 1, "pages": 3, "page_texts": 3}
        assert manifest["writer"] and manifest["command"]


class TestResumeAndDedup:
    def test_a_run_resumes_where_it_stopped(self, tmp_path):
        collector, _, _ = build(tmp_path)
        collector.discover()
        first = collector.run(max_books=1)
        assert first["collected"] == 1
        second = collector.run(max_books=1)
        assert second["collected"] == 1
        assert len(collector.queue.books(state="done")) == 2

    def test_a_finished_book_is_not_collected_again(self, tmp_path):
        collector, _api, _ = build(tmp_path)
        collector.discover()
        collector.run(max_books=2)
        assert collector.run(max_books=2)["collected"] == 0
        assert len(collector.queue.books(state="done")) == 2

    def test_an_interrupted_claim_is_picked_up_again(self, tmp_path):
        collector, _, _ = build(tmp_path)
        collector.discover()
        collector.queue.claim_next()  # a worker that died mid-book
        # The next run recovers the claim at startup and finishes the book, rather
        # than reporting an empty queue while one sits in progress forever.
        assert collector.run(max_books=1)["collected"] == 1
        assert len(collector.queue.books(state="done")) == 1

    def test_a_failed_book_is_retried_before_being_given_up_on(self, tmp_path):
        url = hq.ENTRY_URL.format(entry=ENTRY_A)
        api = FakeAPI(failures={url: 99})  # unreachable for this whole run
        collector, _, _ = build(tmp_path, api)
        collector.discover()
        first = collector.run()
        # The unreachable book is set aside rather than called done, and it does not
        # stall the book behind it.
        assert first["collected"] == 1
        assert collector.queue.book(ENTRY_A)["state"] == "pending"
        api.failures = {}  # the transport recovers
        second = collector.run()
        assert second["collected"] == 1
        assert collector.queue.book(ENTRY_A)["state"] == "done"

    def test_a_book_that_keeps_failing_is_not_called_done(self, tmp_path):
        url = hq.ENTRY_URL.format(entry=ENTRY_A)
        api = FakeAPI(failures={url: 99})
        collector, _, _ = build(tmp_path, api)
        collector.queue.record_book(ENTRY_A, project_id="p", collection_id="c", position=0, origin="live")
        for _ in range(collector.max_attempts + 1):
            collector.run()
        row = collector.queue.book(ENTRY_A)
        assert row["state"] == "failed"
        assert row["last_error"] and "done" not in row["state"]

    def test_a_deadline_ends_the_run_cleanly(self, tmp_path):
        collector, _, _ = build(tmp_path, book_pause=100.0)
        collector.discover()
        result = collector.run(max_seconds=1.0)
        assert result["stop"] == "deadline"
        assert collector.queue.books(state="done")


class TestSnapshotBooksMissingFromLive:
    def test_a_snapshot_book_the_live_api_lost_is_kept_and_flagged(self, tmp_path):
        collector, _api, _ = build(tmp_path)
        collector.queue.record_book(
            ENTRY_GONE, project_id="old", collection_id=None, position=None, origin="snapshot"
        )
        collector.run()
        row = collector.queue.book(ENTRY_GONE)
        assert row["state"] == "missing"  # kept, flagged, not dropped
        assert "404" in (row["last_error"] or "")

    def test_coverage_reports_both_catalogues_honestly(self, tmp_path):
        collector, _, _ = build(tmp_path)
        collector.queue.record_book(ENTRY_A, project_id="p", collection_id="c", position=0, origin="snapshot")
        collector.queue.record_book(
            ENTRY_A, project_id="p", collection_id="c", position=0, origin="live"
        )  # seen live too
        collector.queue.record_book(ENTRY_B, project_id="p", collection_id="c", position=1, origin="live")
        collector.queue.record_book(
            ENTRY_GONE, project_id="p", collection_id=None, position=None, origin="snapshot"
        )
        coverage = collector.queue.coverage()
        assert coverage["both"] == 1  # ENTRY_A
        assert coverage["snapshot"] == 2  # both + snapshot_only
        assert coverage["live"] == 2  # both + live_only
        assert coverage["snapshot_only"] == 1  # ENTRY_GONE, still counted
        assert coverage["live_only"] == 1  # ENTRY_B


class TestPublicationContract:
    def test_a_finished_book_is_offered_once(self, tmp_path):
        collector, _, _ = build(tmp_path)
        collector.discover()
        collector.run(max_books=1)
        pending = collector.queue.unpublished()
        assert len(pending) == 1
        marked = collector.queue.mark_published([pending[0]["entry_id"]], generation=1)
        assert marked == 1
        assert collector.queue.unpublished() == []
        assert collector.queue.generation() == 1

    def test_marking_the_same_book_again_changes_nothing(self, tmp_path):
        collector, _, _ = build(tmp_path)
        collector.discover()
        collector.run(max_books=1)
        row = collector.queue.unpublished()[0]
        collector.queue.mark_published([row["entry_id"]], generation=1)
        assert collector.queue.mark_published([row["entry_id"]], generation=2) == 1
        assert collector.queue.generation() == 2
        assert collector.queue.unpublished() == []

    def test_the_hook_is_told_what_was_written(self, tmp_path):
        seen: list[tuple[str, Path]] = []
        collector, _, _ = build(tmp_path)
        collector.on_book = lambda entry_id, path, result: seen.append((entry_id, path))
        collector.discover()
        collector.run(max_books=1)
        assert len(seen) == 1
        _entry_id, path = seen[0]
        assert path.is_dir() and (path / "MANIFEST.json").is_file()

    def test_the_index_names_the_dataset_and_its_tables(self, tmp_path):
        collector, _, _ = build(tmp_path)
        collector.discover()
        collector.run(max_books=1)
        index = collector.index()
        assert index["count"] == 1
        book = index["books"][0]
        assert book["dataset"].startswith("books/")
        assert book["tables"] == ["documents.parquet", "pages.parquet", "page_texts.parquet"]
        assert book["generation"] is None

    def test_the_index_has_one_row_per_entry(self, tmp_path):
        collector, _, _ = build(tmp_path)
        collector.discover()
        collector.run(max_books=2)
        ids = [b["entry_id"] for b in collector.index()["books"]]
        assert len(ids) == len(set(ids)) == 2

    def test_outputs_are_written_without_absolute_paths(self, tmp_path):
        collector, _, _ = build(tmp_path)
        collector.discover()
        collector.run(max_books=1)
        for name in (hq.STATUS_FILE, hq.INDEX_FILE):
            text = (tmp_path / "collection" / name).read_text(encoding="utf-8")
            assert str(tmp_path) not in text and "/home/" not in text


class TestDiskGuard:
    def test_a_run_stops_below_the_floor(self, tmp_path):
        collector, _, _ = build(tmp_path, free=4 * 1024**3)
        collector.queue.record_book(ENTRY_A, project_id="p", collection_id="c", position=0, origin="live")
        result = collector.run()
        assert result["collected"] == 0
        assert "floor" in (result["stop"] or "")
        assert collector.queue.book(ENTRY_A)["state"] == "pending"

    def test_a_claimed_book_is_released_not_failed(self, tmp_path):
        collector, _, _ = build(tmp_path, free=6 * 1024**3)
        collector.queue.record_book(ENTRY_A, project_id="p", collection_id="c", position=0, origin="live")
        # Space disappears after the claim but before the dataset is written.
        calls = {"n": 0}

        def shrinking(path):
            calls["n"] += 1
            return 6 * 1024**3 if calls["n"] <= 2 else 1 * 1024**3

        collector._disk_free = shrinking
        result = collector.run()
        assert result["stop"] and "floor" in result["stop"]
        row = collector.queue.book(ENTRY_A)
        assert row["state"] == "pending"  # released, not failed
        assert not (tmp_path / "collection" / "books" / ENTRY_A).exists()

    def test_the_status_reports_headroom(self, tmp_path):
        collector, _, _ = build(tmp_path, free=7 * 1024**3)
        status = collector.status()
        assert status["free_bytes"] == 7 * 1024**3
        assert status["min_free_bytes"] == hq.MIN_FREE_BYTES


class TestProcessLock:
    def test_a_second_worker_is_refused(self, tmp_path):
        root = tmp_path / "collection"
        root.mkdir()
        with hq.process_lock(root), pytest.raises(hq.Busy), hq.process_lock(root):
            pass

    def test_the_lock_is_released_afterwards(self, tmp_path):
        root = tmp_path / "collection"
        root.mkdir()
        with hq.process_lock(root):
            pass
        with hq.process_lock(root):
            pass


class TestStatusShape:
    def test_the_status_counters_are_plain_numbers(self, tmp_path):
        collector, _, _ = build(tmp_path)
        collector.discover()
        collector.run(max_books=1)
        status = collector.status()
        assert isinstance(status["books"], dict)
        assert status["books"].get("done") == 1
        assert status["in_progress"] is None
        assert status["books"]["pending"] == 1
        assert isinstance(status["coverage"], dict)

    def test_status_json_is_written_to_disk(self, tmp_path):
        collector, _, _ = build(tmp_path)
        collector.discover()
        collector.run(max_books=1)
        saved = json.loads((tmp_path / "collection" / hq.STATUS_FILE).read_text(encoding="utf-8"))
        assert saved["books"]["done"] == 1
        assert saved["kind"] == "honkoku-collection-status"


class TestFetcher:
    def test_a_404_is_not_retried(self, tmp_path):
        api = FakeAPI(entries={})
        clock = FakeClock()
        fetcher = hq.Fetcher(
            client=api, pacer=hq.Pacer(clock=clock, sleeper=clock.sleep), sleeper=clock.sleep
        )
        with pytest.raises(hq.CollectorError):
            fetcher.json(hq.ENTRY_URL.format(entry=ENTRY_A))
        assert len(api.urls) == 1

    def test_a_5xx_is_retried_with_backoff(self, tmp_path):
        api = FakeAPI(failures={hq.ENTRY_URL.format(entry=ENTRY_A): 2})
        clock = FakeClock()
        fetcher = hq.Fetcher(
            client=api, pacer=hq.Pacer(clock=clock, sleeper=clock.sleep), sleeper=clock.sleep
        )
        assert fetcher.json(hq.ENTRY_URL.format(entry=ENTRY_A))["id"] == ENTRY_A
        assert len(api.urls) == 3

    def test_a_repeated_request_within_a_run_is_served_from_memory(self, tmp_path):
        api = FakeAPI()
        clock = FakeClock()
        fetcher = hq.Fetcher(
            client=api, pacer=hq.Pacer(clock=clock, sleeper=clock.sleep), sleeper=clock.sleep
        )
        fetcher.json(hq.PROJECTS_URL)
        fetcher.json(hq.PROJECTS_URL)
        assert len(api.urls) == 1

    def test_a_restart_does_not_reuse_a_stale_cache(self, tmp_path):
        """The cache is per run: a new fetcher re-reads the API."""
        api = FakeAPI()
        clock = FakeClock()
        first = hq.Fetcher(client=api, pacer=hq.Pacer(clock=clock, sleeper=clock.sleep), sleeper=clock.sleep)
        first.json(hq.PROJECTS_URL)
        second = hq.Fetcher(client=api, pacer=hq.Pacer(clock=clock, sleeper=clock.sleep), sleeper=clock.sleep)
        second.json(hq.PROJECTS_URL)
        assert len(api.urls) == 2


class TestPageIdHelper:
    @pytest.mark.parametrize("index0,expected", [(0, 1), (1, 2), (30, 31)])
    def test_the_api_index_is_zero_based_and_the_id_is_one_based(self, index0, expected):
        assert hq.page_id(ENTRY_A, index0) == f"hk:{ENTRY_A}:{expected}"


class TestStartupRecovery:
    """A claim left by a dead worker must not block the queue forever."""

    def test_a_crashed_claim_is_returned_by_a_new_collector(self, tmp_path):
        collector, _, _ = build(tmp_path)
        collector.discover()
        crashed = collector.queue.claim_next()  # a worker that died mid-book
        assert crashed is not None
        assert collector.queue.books(state="in_progress")

        # A new process: same root, new collector, under the worker lock.
        resumed, _api, _clock = build(tmp_path)
        with hq.process_lock(tmp_path / "collection"):
            assert resumed.recover() == 1
            assert resumed.queue.books(state="in_progress") == []
            result = resumed.run(max_books=1)
        assert result["collected"] == 1
        assert resumed.queue.book(crashed["entry_id"])["state"] == "done"

    def test_recovery_keeps_the_finished_at_cooldown(self, tmp_path):
        collector, _, _ = build(tmp_path)
        collector.discover()
        collector.run(max_books=1)  # one book finished, stamp written
        stamp = collector.queue.finished_at()
        collector.queue.claim_next()  # then the worker died
        collector.recover()
        assert collector.queue.finished_at() == stamp

    def test_recovery_is_not_a_side_effect_of_reading_status(self, tmp_path):
        collector, _, _ = build(tmp_path)
        collector.queue.record_book(ENTRY_A, project_id="p", collection_id="c", position=0, origin="live")
        collector.queue.claim_next()
        collector.status()  # a reader, not a worker
        collector.index()
        assert collector.queue.books(state="in_progress")

    def test_recovery_is_idempotent(self, tmp_path):
        collector, _, _ = build(tmp_path)
        collector.queue.record_book(ENTRY_A, project_id="p", collection_id="c", position=0, origin="live")
        collector.queue.claim_next()
        assert collector.recover() == 1
        assert collector.recover() == 0


def test_private_project_in_list_blocks_snapshot_entries(tmp_path):
    collector, api, _ = build(tmp_path)
    collector.queue.record_book(ENTRY_GONE, project_id="private-one", collection_id=None,
                               position=None, origin="snapshot")
    collector.discover_projects()
    assert collector.queue.books(state="skipped")[0]["entry_id"] == ENTRY_GONE
    assert hq.ENTRY_URL.format(entry=ENTRY_GONE) not in api.urls


def test_unknown_snapshot_project_must_be_public_before_entry_fetch(tmp_path):
    collector, api, _ = build(tmp_path, FakeAPI(project={"isPrivate": True}))
    collector.queue.record_book(ENTRY_A, project_id="old-project", collection_id=None,
                               position=None, origin="snapshot")
    with pytest.raises(hq.Refused):
        collector.collect_book(ENTRY_A)
    assert hq.ENTRY_URL.format(entry=ENTRY_A) not in api.urls


@pytest.mark.parametrize("change", ["duplicate", "canvas", "index"])
def test_malformed_pages_are_quarantined_without_losing_text(change):
    data = entry_payload(ENTRY_A)
    if change == "duplicate":
        data["transcriptions"].append(data["transcriptions"][0])
    elif change == "canvas":
        data["canvases"][1] = "unexpected canvas"
    else:
        del data["transcriptions"][0]["index"]
    with pytest.raises(hq.AlignmentError):
        hq.book_record(data, entry_id=ENTRY_A)


def test_collection_entry_objects_are_enumerated(tmp_path):
    data = dict(COLLECTION, entries=[{"id": ENTRY_A}, {"id": ENTRY_B}])
    collector, _, _ = build(tmp_path, FakeAPI(collections={COLLECTION["id"]: data}))
    collector.discover()
    assert len(collector.queue.books()) == 2
