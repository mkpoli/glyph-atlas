"""Sequential, resumable collection of Japanese Wikisource works and proofread pages.

Discovery enumerates both text namespaces through MediaWiki's allpages continuation.
Each acquired work has its own document identity. Text and scan references are kept;
the collector does not claim that a transcription supplies character rectangles.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from urllib.parse import quote

from .. import tables
from ..importers.honkoku_queue import DISCOVERY_EPOCH_SECONDS, MIN_FREE_BYTES, OutOfSpace, write_json
from ..schema import Document, Page, PageText
from .wikisource import DEFAULT_HOST, Wikisource, WikisourcePage, document_of, page_of, page_text_of

NAMESPACES = (250, 0)


def _stamp(db, key: str) -> float | None:
    """A wall-clock stamp stored in `metadata`, or None when absent or unreadable."""
    row = db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
    try:
        return float(row["value"]) if row else None
    except (TypeError, ValueError):
        return None


def work_of(title: str, namespace: int) -> tuple[str, str]:
    if namespace == 250:
        title = title.split(":", 1)[-1].rsplit("/", 1)[0]
        source_title = "Index:" + title
    else:
        title = title.split("/", 1)[0]
        source_title = title
    key = hashlib.sha256(source_title.encode()).hexdigest()[:24]
    return key, source_title


class Collector:
    def __init__(self, root: Path, *, client=None, book_pause=60.0, min_free_bytes=MIN_FREE_BYTES,
                 discovery_epoch_seconds=DISCOVERY_EPOCH_SECONDS):
        self.root = Path(root)
        self.discovery_epoch_seconds = discovery_epoch_seconds
        self.root.mkdir(parents=True, exist_ok=True)
        self.client = client or Wikisource(cache=self.root / "cache", pause=3.0)
        self.book_pause = max(0.0, book_pause)
        self.min_free_bytes = min_free_bytes
        self.path = self.root / "queue.sqlite"
        with closing(self.connect()) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS discovery(namespace INTEGER PRIMARY KEY, cursor TEXT, done INTEGER DEFAULT 0);
                CREATE TABLE IF NOT EXISTS works(id TEXT PRIMARY KEY, title TEXT NOT NULL,
                    state TEXT DEFAULT 'pending', dataset TEXT, pages INTEGER DEFAULT 0,
                    text_pages INTEGER DEFAULT 0, finished_at REAL, error TEXT, attempts INTEGER DEFAULT 0);
                CREATE TABLE IF NOT EXISTS pages(id INTEGER PRIMARY KEY, title TEXT NOT NULL, namespace INTEGER NOT NULL,
                    work_id TEXT NOT NULL, state TEXT DEFAULT 'pending', revision INTEGER, body TEXT);
                CREATE INDEX IF NOT EXISTS pages_work ON pages(work_id,state);
                CREATE UNIQUE INDEX IF NOT EXISTS one_work ON works(state) WHERE state='in_progress';
                CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT);
            """)
            db.executemany("INSERT OR IGNORE INTO discovery(namespace) VALUES(?)", [(ns,) for ns in NAMESPACES])

    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        return db

    def check_space(self):
        if shutil.disk_usage(self.root).free < self.min_free_bytes:
            raise OutOfSpace("Collection paused: less than 5 GiB free on its storage volume")

    def recover(self):
        with closing(self.connect()) as db, db:
            db.execute("UPDATE works SET state='pending' WHERE state='in_progress'")

    def _maybe_open_discovery_epoch(self) -> bool:
        """Reset the walk when the last one finished and the epoch interval passed.

        Same gating as the Honkoku queue: a walk interrupted mid-run resumes from
        its stored cursor, and rows reset only between walks, so a pass that
        outlasts one job is never restarted from the beginning.
        """
        now = time.time()
        with closing(self.connect()) as db:
            started = _stamp(db, "discovery_started_at")
            finished = _stamp(db, "discovery_finished_at")
        if started is not None and (finished is None or finished < started):
            if now - started < self.discovery_epoch_seconds:
                return False
        elif finished is not None and now - finished < self.discovery_epoch_seconds:
            return False
        # The allpages cache would replay the previous walk's first batch and
        # pages added since would never be seen; drop just that bucket. The
        # page-body cache stays: it is keyed by revision and still saves requests.
        cache = getattr(self.client, "cache", None)
        if cache is not None:
            shutil.rmtree(Path(cache) / "discovery", ignore_errors=True)
        with closing(self.connect()) as db, db:
            db.execute("INSERT OR REPLACE INTO metadata VALUES('discovery_started_at',?)", (str(now),))
            db.execute("UPDATE discovery SET cursor=NULL, done=0")
        return True

    def _close_discovery_epoch(self) -> None:
        """Stamp the walk complete, when this run is the one that finished it."""
        with closing(self.connect()) as db:
            started = _stamp(db, "discovery_started_at")
            finished = _stamp(db, "discovery_finished_at")
        if started is None or (finished is not None and finished >= started):
            return
        with closing(self.connect()) as db, db:
            db.execute("INSERT OR REPLACE INTO metadata VALUES('discovery_finished_at',?)", (str(time.time()),))

    def _reopen_works_with_new_pages(self) -> int:
        """Done works whose source gained pages go back to pending, dataset removed.

        `collect_work` leaves an existing dataset alone — that guard is how a
        crash between the rename and the queue commit completes the same book
        safely — so the old dataset is removed first and the work is rebuilt from
        every page the queue holds. A crash between the removal and the state
        update self-heals: the work still matches this query on the next run.
        """
        with closing(self.connect()) as db:
            rows = db.execute(
                "SELECT id FROM works WHERE state='done'"
                " AND EXISTS(SELECT 1 FROM pages p WHERE p.work_id=works.id AND p.state='pending')"
            ).fetchall()
        for row in rows:
            shutil.rmtree(self.root / "books" / row["id"], ignore_errors=True)
        if rows:
            with closing(self.connect()) as db, db:
                marks = ",".join("?" * len(rows))
                db.execute(f"UPDATE works SET state='pending', finished_at=NULL WHERE id IN ({marks})",
                           [row["id"] for row in rows])
        return len(rows)

    def discover_batch(self) -> bool:
        """Commit discovered pages and their continuation together; never lose a boundary page."""
        self.check_space()
        with closing(self.connect()) as db:
            row = db.execute("SELECT * FROM discovery WHERE done=0 ORDER BY namespace DESC LIMIT 1").fetchone()
        if row is None:
            return False
        params = {"action": "query", "list": "allpages", "apnamespace": row["namespace"],
                  "aplimit": 500, "apfilterredir": "nonredirects", "maxlag": 5}
        if row["cursor"]:
            params["apcontinue"] = row["cursor"]
        payload = self.client._api(params, bucket="discovery")
        if "error" in payload or not isinstance((payload.get("query") or {}).get("allpages"), list):
            raise RuntimeError("Wikisource discovery request did not return page data")
        cursor = (payload.get("continue") or {}).get("apcontinue")
        with closing(self.connect()) as db, db:
            for page in payload["query"].get("allpages", []):
                if page.get("ns") not in NAMESPACES:
                    continue
                key, title = work_of(page["title"], page["ns"])
                db.execute("INSERT OR IGNORE INTO works(id,title) VALUES(?,?)", (key, title))
                db.execute("INSERT OR IGNORE INTO pages(id,title,namespace,work_id) VALUES(?,?,?,?)",
                           (page["pageid"], page["title"], page["ns"], key))
            db.execute("UPDATE discovery SET cursor=?,done=? WHERE namespace=?",
                       (cursor, int(cursor is None), row["namespace"]))
        self.write_outputs()
        return True

    def collect_work(self, key: str) -> dict:
        self.check_space()
        with closing(self.connect()) as db, db:
            work = db.execute("SELECT * FROM works WHERE id=?", (key,)).fetchone()
            db.execute("UPDATE works SET state='in_progress',attempts=attempts+1,error=NULL WHERE id=?", (key,))
        self.write_outputs()
        while True:
            self.check_space()
            with closing(self.connect()) as db:
                pending = db.execute("SELECT * FROM pages WHERE work_id=? AND state='pending' ORDER BY id LIMIT 20",
                                     (key,)).fetchall()
            if not pending:
                break
            fetched = self.client.pages([row["title"] for row in pending])
            by_id = {page.pageid: page for page in fetched}
            with closing(self.connect()) as db, db:
                for row in pending:
                    page = by_id.get(row["id"])
                    if page is None:
                        db.execute("UPDATE pages SET state='missing' WHERE id=?", (row["id"],))
                    else:
                        db.execute("UPDATE pages SET state='done',revision=?,body=? WHERE id=?",
                                   (page.revision, json.dumps(page.as_dict(), ensure_ascii=False), page.pageid))
            self.write_outputs()
        with closing(self.connect()) as db:
            rows = db.execute("SELECT body FROM pages WHERE work_id=? AND state='done' ORDER BY title", (key,)).fetchall()
        pages = [WikisourcePage(**json.loads(row["body"])) for row in rows]
        pages.sort(key=lambda page: (page.title.rsplit("/", 1)[0],
                   int(page.title.rsplit("/", 1)[-1]) if page.title.rsplit("/", 1)[-1].isdigit() else -1,
                   page.title))
        base = document_of(DEFAULT_HOST).model_dump()
        base.update(id=f"ws:ja:work:{key}", title=work["title"].removeprefix("Index:"))
        base["source_refs"] = {**base["source_refs"], "wikisource-work":
                               "https://ja.wikisource.org/wiki/" + quote(work["title"].replace(" ", "_"), safe=":/")}
        document = Document(**base)
        output_pages = []
        for sequence, page in enumerate(pages):
            item = page_of(page)
            item.document_id = document.id
            item.seq = sequence
            output_pages.append(item)
        destination = self.root / "books" / key
        staging = self.root / ".staging" / key
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)
        tables.write(staging / "documents.parquet", [document], Document)
        tables.write(staging / "pages.parquet", output_pages, Page)
        tables.write(staging / "page_texts.parquet", [page_text_of(page) for page in pages], PageText)
        counts = {"documents": 1, "pages": len(pages), "page_texts": len(pages)}
        write_json(staging / "MANIFEST.json", {"schema_version": tables.SCHEMA_VERSION, "tables": counts,
                   "command": "collect Japanese Wikisource work", "geometry": "none"})
        destination.parent.mkdir(exist_ok=True)
        if destination.exists():
            # A rename succeeded before the queue commit; complete the same book safely.
            shutil.rmtree(staging)
        else:
            for path in staging.glob("*.parquet"):
                with path.open("rb") as handle:
                    os.fsync(handle.fileno())
            os.replace(staging, destination)
            descriptor = os.open(destination.parent, os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        with closing(self.connect()) as db, db:
            db.execute("UPDATE works SET state='done',dataset=?,pages=?,text_pages=?,finished_at=?,error=NULL WHERE id=?",
                       (f"books/{key}", len(pages), sum(bool(p.wikitext.strip()) for p in pages), time.time(), key))
            db.execute("INSERT OR REPLACE INTO metadata VALUES('last_attempt',?)", (str(time.time()),))
        self.write_outputs()
        return counts

    def run(self, *, seconds=3600, max_books=None, discover_batches=None):
        self.recover()
        started = time.monotonic()
        discovered = completed = 0
        try:
            self._maybe_open_discovery_epoch()
            while time.monotonic() - started < seconds:
                if discover_batches is not None and discovered >= discover_batches:
                    break
                if not self.discover_batch():
                    break
                discovered += 1
            with closing(self.connect()) as db:
                incomplete = db.execute("SELECT count(*) FROM discovery WHERE done=0").fetchone()[0]
            if incomplete:
                return {"discovery_batches": discovered, "collected": 0, "reopened": 0}
            reopened = self._reopen_works_with_new_pages()
            self._close_discovery_epoch()
            attempted = set()
            while time.monotonic() - started < seconds and (max_books is None or completed < max_books):
                with closing(self.connect()) as db:
                    rows = db.execute("SELECT id FROM works WHERE state='pending' ORDER BY title").fetchall()
                    previous = db.execute("SELECT value FROM metadata WHERE key='last_attempt'").fetchone()
                row = next((row for row in rows if row["id"] not in attempted), None)
                if row is None:
                    break
                wait = max(0.0, self.book_pause - (time.time() - float(previous[0]))) if previous else 0
                if time.monotonic() - started + wait >= seconds:
                    break
                if wait:
                    time.sleep(wait)
                attempted.add(row["id"])
                try:
                    self.collect_work(row["id"])
                    completed += 1
                except OutOfSpace:
                    raise
                except Exception as error:  # noqa: BLE001 - checkpoint failed work for a later retry
                    with closing(self.connect()) as db, db:
                        db.execute("UPDATE works SET state=CASE WHEN attempts>=3 THEN 'failed' ELSE 'pending' END,error=? WHERE id=?",
                                   (type(error).__name__, row["id"]))
                        db.execute("INSERT OR REPLACE INTO metadata VALUES('last_attempt',?)", (str(time.time()),))
                    self.write_outputs()
            return {"discovery_batches": discovered, "collected": completed, "reopened": reopened}
        finally:
            self.write_outputs()

    def status(self):
        with closing(self.connect()) as db:
            counts = dict(db.execute("SELECT state,count(*) FROM works GROUP BY state"))
            pages = dict(db.execute("SELECT state,count(*) FROM pages GROUP BY state"))
            discovery = [dict(row) for row in db.execute("SELECT namespace,cursor,done FROM discovery ORDER BY namespace")]
            current = db.execute("SELECT id,title FROM works WHERE state='in_progress'").fetchone()
            total_pages, text_pages = db.execute("SELECT coalesce(sum(pages),0),coalesce(sum(text_pages),0) FROM works WHERE state='done'").fetchone()
        return {"kind": "wikisource-collection-status", "host": DEFAULT_HOST,
                "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "works": counts, "page_states": pages, "pages": total_pages, "text_pages": text_pages,
                "discovery": discovery, "discovery_complete": all(row["done"] for row in discovery),
                "current": dict(current) if current else None, "book_pause": self.book_pause,
                "host_pause": 3, "min_free_bytes": self.min_free_bytes,
                "free_bytes": shutil.disk_usage(self.root).free, "character_crops": 0}

    def write_outputs(self):
        write_json(self.root / "status.json", self.status())
        with closing(self.connect()) as db:
            records = [dict(row) for row in db.execute("SELECT id AS entry_id,title AS label,dataset,pages,text_pages FROM works WHERE state='done' ORDER BY id")]
        write_json(self.root / "index.json", {"kind": "wikisource-collection-index", "count": len(records), "books": records})
