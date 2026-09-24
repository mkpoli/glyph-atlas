"""A slow, resumable, one-book-at-a-time collector for honkoku.org.

The atlas already holds a *snapshot* of honkoku-data: 7,584 entries read from a git
clone. That is a snapshot of one project's listings, not a census of the platform and
not a character corpus. This module walks the live public API instead — every project,
every collection, every entry — and writes one immutable dataset per book, slowly
enough that nobody has to think about it.

What it is careful about, and why:

* **One book at a time, and the database says so.** A partial unique index permits a
  single ``in_progress`` row, so a second worker cannot start even if the file lock is
  lost. A process lock covers the rest.
* **Resumable and duplicate-free.** The queue is SQLite; a book's identity is its entry
  id. Re-running walks the discovery again, sees what is already done, and continues.
  A book is only marked ``done`` once its dataset is complete and its manifest written.
* **Atomic per book.** A book is assembled under ``books/.staging/<id>/`` and moved into
  place with a single rename. An interrupted run leaves staging behind, never a
  half-written book that looks finished.
* **Slow on purpose.** At least three seconds between requests to the host and a minute
  between books, both configurable and both measured on a monotonic clock.
* **Polite to the disk.** The run stops gracefully below a free-space floor rather than
  filling the volume.
* **No pixels and no inference.** Only API JSON is fetched. Canvas *references* are
  recorded; no image is downloaded and no model is run here.
* **Private stays private.** A project marked private, or a collection not displayed,
  is skipped and recorded as skipped — never fetched, never stored.

Rights and revisions travel with each book: the entry's licence and attribution, the
manifest URL, and each transcription's own revision timestamp. Source text is recorded
as unverified transcription. No unit geometry is invented: a page records the canvas
rectangle it was given and nothing more.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import sqlite3
import time
from collections import OrderedDict
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import closing, contextmanager
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

from ..net import USER_AGENT

API_BASE = "https://app.honkoku.org/api"
PROJECTS_URL = f"{API_BASE}/projects"
PROJECT_URL = f"{API_BASE}/projects/{{project}}"
COLLECTION_URL = f"{API_BASE}/collections/{{collection}}"
ENTRY_URL = f"{API_BASE}/entries/{{entry}}"

SCHEMA_VERSION = 1
#: Written next to the queue; a reader compares it before trusting the tables.
QUEUE_FILE = "queue.sqlite"
LOCK_FILE = "worker.lock"
BOOKS_DIR = "books"
STAGING_DIR = ".staging"
STATUS_FILE = "status.json"
INDEX_FILE = "index.json"

#: Seconds between requests to the host. The platform is not a bulk mirror.
MIN_HOST_PAUSE = 3.0
#: Seconds between books: the point of the worker is to be unhurried.
BOOK_PAUSE = 60.0
#: Free space below which the run stops instead of filling the volume.
MIN_FREE_BYTES = 5 * 1024**3
# A completed walk leaves every project and collection `done`, so a later walk
# would queue nothing and entries added under known collections would never be
# seen. Walks are therefore gated by an epoch: when the last walk finished and
# the interval has passed, walked rows reopen and the walk runs again.
# Re-walking is cheap because record_project, record_collection and record_book
# are INSERT OR IGNORE.
DISCOVERY_EPOCH_SECONDS = 7 * 24 * 3600


def _meta_stamp(db, key: str) -> float | None:
    """A wall-clock stamp stored in `meta`, or None when absent or unreadable."""
    row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    try:
        return float(row["value"]) if row else None
    except (TypeError, ValueError):
        return None
#: Attempts per request before the book is left for a later run.
RETRIES = 3
#: Responses kept in memory. An entry body can be large and there are thousands of
#: them, so the cache is a short-lived window rather than a corpus in RAM.
CACHE_MAX = 32
BACKOFF = 2.0

STATES = ("pending", "in_progress", "done", "failed", "skipped", "missing")

#: A book whose entry the live API no longer serves. The row is kept, because the
#: snapshot rows are real catalogue entries and dropping them would silently shrink
#: the atlas to whatever the platform still lists today.
MISSING_STATES = ("skipped", "missing")

#: The eight columns the snapshot's `info.tsv` carries.
INFO_COLUMNS = ("id", "label", "manifestUrl", "projectId", "size", "progress", "attribution", "thumbnail")


class CollectorError(RuntimeError):
    """A refusal that is not worth retrying."""


class Busy(CollectorError):
    """Another worker holds the queue."""


class Refused(CollectorError):
    """The host declined: 401, 403 or 451. Not the same as a book that is gone."""


class AlignmentError(CollectorError):
    """The canvas list and the transcription list disagree about which page is which.

    Publishing this book anyway would pair one page's image with another page's text,
    and a wrong pairing is invisible once it is in a dataset. The book is quarantined
    for a later run instead.
    """


class OutOfSpace(CollectorError):
    """Free space fell below the floor."""


class Retryable(RuntimeError):
    """A request that may succeed later: a 429, a 5xx, a timeout."""


class EntryMissing(CollectorError):
    """The live API no longer serves an entry the catalogue still names."""


class NotFound(EntryMissing):
    """A 404 or 410: the thing asked for is not there. Not a transient failure."""


# --------------------------------------------------------------------- pacing
class Pacer:
    """Spacing between requests and between books, on an injectable clock.

    The clock and the sleeper are separate so a test can measure the intended pauses
    without waiting for them.
    """

    def __init__(
        self,
        *,
        host_pause: float = MIN_HOST_PAUSE,
        book_pause: float = BOOK_PAUSE,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ):
        self.host_pause = max(0.0, host_pause)
        self.book_pause = max(0.0, book_pause)
        self.clock = clock
        self.sleeper = sleeper
        self._last_request: float | None = None
        self._last_book: float | None = None
        self.slept = 0.0

    def before_request(self) -> None:
        self._wait(self._last_request, self.host_pause)
        self._last_request = self.clock()

    def before_book(self, finished_at: float | None) -> None:
        """Wait out the rest of the pause after the previous attempt.

        `finished_at` is a wall-clock stamp from the queue, not from this process, so
        the pause holds across a restart: a worker that died mid-book still waits its
        turn before the next one starts. The first call waits too when a previous
        attempt is on record, which is what makes the first two books in a fresh
        process as far apart as any other pair.
        """
        if finished_at is None or self.book_pause <= 0:
            return
        remaining = self.book_pause - (time.time() - finished_at)
        if remaining > 0:
            self.slept += remaining
            self.sleeper(remaining)

    def after_book(self) -> None:
        """Record that this attempt ended. The wait happens before the next start."""
        self._last_book = self.clock()

    def _wait(self, since: float | None, pause: float) -> None:
        if since is None or pause <= 0:
            return
        remaining = pause - (self.clock() - since)
        if remaining > 0:
            self.slept += remaining
            self.sleeper(remaining)


# --------------------------------------------------------------------- queue
QUEUE_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY, title TEXT, is_private INTEGER NOT NULL DEFAULT 0,
    total_entries INTEGER NOT NULL DEFAULT 0, display INTEGER NOT NULL DEFAULT 1,
    state TEXT NOT NULL DEFAULT 'pending', source TEXT, discovered_at TEXT
);
CREATE TABLE IF NOT EXISTS collections (
    id TEXT PRIMARY KEY, project_id TEXT, title TEXT,
    display INTEGER NOT NULL DEFAULT 1, entry_count INTEGER NOT NULL DEFAULT 0,
    state TEXT NOT NULL DEFAULT 'pending', source TEXT, discovered_at TEXT
);
CREATE TABLE IF NOT EXISTS books (
    entry_id TEXT PRIMARY KEY, project_id TEXT, collection_id TEXT,
    label TEXT, position INTEGER, size INTEGER, progress INTEGER,
    manifest_url TEXT, attribution TEXT, licence TEXT,
    state TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
    pages INTEGER, transcriptions INTEGER, dataset TEXT, manifest_sha256 TEXT,
    discovered_at TEXT, started_at TEXT, finished_at TEXT, last_error TEXT,
    origin TEXT, published INTEGER NOT NULL DEFAULT 0, generation INTEGER
);
CREATE INDEX IF NOT EXISTS books_state ON books(state);
-- One book in progress, enforced by the database rather than by a convention.
CREATE UNIQUE INDEX IF NOT EXISTS books_one_in_progress
    ON books(state) WHERE state = 'in_progress';
"""


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class Queue:
    """The durable work list. Every mutation is a single transaction."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db, db:
            db.executescript(QUEUE_SCHEMA)
            db.execute(
                "INSERT OR IGNORE INTO meta(key, value) VALUES('schema_version', ?)", (str(SCHEMA_VERSION),)
            )

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=30.0)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=NORMAL")
        return db

    @contextmanager
    def transaction(self):
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            yield db

    # ------------------------------------------------------------- discovery
    def record_project(self, project: Mapping[str, Any]) -> bool:
        """Insert a project once. Returns whether it was new.

        A private project keeps its id and its counters and nothing else: the response
        body of something the platform did not publish is not ours to store.
        """
        private = bool(project.get("isPrivate"))
        with self.transaction() as db:
            cursor = db.execute(
                "INSERT OR IGNORE INTO projects(id, title, is_private, total_entries,"
                " display, state, source, discovered_at) VALUES(?,?,?,?,?,?,?,?)",
                (
                    str(project.get("id")),
                    project.get("title"),
                    1 if project.get("isPrivate") else 0,
                    int(project.get("totalEntryCount") or 0),
                    0 if project.get("display") is False else 1,
                    "skipped" if private else "pending",
                    None if private else json.dumps(project, ensure_ascii=False),
                    _now(),
                ),
            )
            return cursor.rowcount > 0

    def record_collection(self, collection: Mapping[str, Any], project_id: str) -> bool:
        with self.transaction() as db:
            cursor = db.execute(
                "INSERT OR IGNORE INTO collections(id, project_id, title, display,"
                " entry_count, state, source, discovered_at) VALUES(?,?,?,?,?,?,?,?)",
                (
                    str(collection.get("id")),
                    project_id,
                    collection.get("title"),
                    0 if collection.get("display") is False else 1,
                    int(collection.get("entryCount") or len(collection.get("entries") or [])),
                    "pending",
                    json.dumps(collection, ensure_ascii=False),
                    _now(),
                ),
            )
            return cursor.rowcount > 0

    def record_book(
        self,
        entry_id: str,
        *,
        project_id: str | None,
        collection_id: str | None,
        position: int | None,
        origin: str,
        fields: Mapping[str, Any] | None = None,
    ) -> bool:
        """Queue a book once, whatever door it arrived through."""
        fields = dict(fields or {})
        with self.transaction() as db:
            cursor = db.execute(
                "INSERT OR IGNORE INTO books(entry_id, project_id, collection_id,"
                " position, label, size, manifest_url, attribution, state,"
                " discovered_at, origin) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    entry_id,
                    project_id,
                    collection_id,
                    position,
                    fields.get("label"),
                    fields.get("size"),
                    fields.get("manifestUrl"),
                    fields.get("attribution"),
                    "pending",
                    _now(),
                    origin,
                ),
            )
            added = cursor.rowcount > 0
            if not added:
                # Seen through both doors: the snapshot knew it and the live platform
                # still lists it. `both` is what makes coverage honest about that.
                db.execute(
                    "UPDATE books SET origin='both' WHERE entry_id=? AND origin<>? AND origin<>'both'",
                    (entry_id, origin),
                )
            return added

    def seed_from_snapshot(self, clone: Path) -> int:
        """Queue every row of every ``v3/<project>/info.tsv`` in a checkout.

        Every row, including the ones whose transcription directory is missing: a
        listing without text still names a book, and the live API can still describe
        it. Reading only the rows with directories is how a snapshot quietly becomes a
        subset.
        """
        clone = Path(clone)
        added = 0
        for info in sorted(clone.glob("v3/*/info.tsv")):
            project = info.parent.name
            for row in _read_info(info):
                entry_id = (row.get("id") or "").strip()
                if not entry_id:
                    continue
                if self.record_book(
                    entry_id,
                    project_id=row.get("projectId") or project,
                    collection_id=None,
                    position=None,
                    origin="snapshot",
                    fields={
                        "label": row.get("label"),
                        "size": _whole(row.get("size")),
                        "manifestUrl": row.get("manifestUrl"),
                        "attribution": row.get("attribution"),
                    },
                ):
                    added += 1
        return added

    # --------------------------------------------------------------- claiming
    def claim_next(self, exclude: Iterable[str] | None = None) -> sqlite3.Row | None:
        """Mark the oldest pending book in progress, or return None.

        The UPDATE is guarded by ``state='pending'`` inside an immediate transaction,
        and the partial unique index is the backstop: if a second process ever raced
        past the guard, the index would reject the write rather than let two books
        run at once.
        """
        with self.transaction() as db:
            running = db.execute("SELECT entry_id FROM books WHERE state='in_progress'").fetchone()
            if running:
                return None
            skip = [e for e in (exclude or ()) if e]
            marks = f" AND entry_id NOT IN ({','.join('?' * len(skip))})" if skip else ""
            row = db.execute(
                "SELECT * FROM books WHERE state='pending'"
                + marks
                + " ORDER BY discovered_at, entry_id LIMIT 1",
                tuple(skip),
            ).fetchone()
            if row is None:
                return None
            db.execute(
                "UPDATE books SET state='in_progress', started_at=?, attempts=attempts+1"
                " WHERE entry_id=? AND state='pending'",
                (_now(), row["entry_id"]),
            )
            return db.execute("SELECT * FROM books WHERE entry_id=?", (row["entry_id"],)).fetchone()

    def finish_book(
        self,
        entry_id: str,
        *,
        dataset: str,
        pages: int,
        transcriptions: int,
        manifest_sha256: str,
        licence: str | None,
        label: str | None = None,
    ) -> None:
        with self.transaction() as db:
            db.execute(
                "UPDATE books SET state='done', finished_at=?, dataset=?, pages=?,"
                " transcriptions=?, manifest_sha256=?, licence=?, label=COALESCE(?, label),"
                " last_error=NULL WHERE entry_id=?",
                (_now(), dataset, pages, transcriptions, manifest_sha256, licence, label, entry_id),
            )

    def fail_book(self, entry_id: str, error: str, *, attempts: int, max_attempts: int) -> str:
        """Leave a failed book retryable until its attempts run out."""
        state = "failed" if attempts >= max_attempts else "pending"
        with self.transaction() as db:
            db.execute(
                "UPDATE books SET state=?, last_error=?, finished_at=CASE WHEN ?='failed'"
                " THEN ? ELSE finished_at END WHERE entry_id=?",
                (state, re.sub(r"/home/[^/\s]+", "~", error)[:500], state, _now(), entry_id),
            )
        return state

    def release_book(self, entry_id: str, reason: str) -> None:
        """Put a claimed book back, for a stop that is not a failure."""
        with self.transaction() as db:
            db.execute(
                "UPDATE books SET state='pending', last_error=? WHERE entry_id=?", (re.sub(r"/home/[^/\s]+", "~", reason)[:500], entry_id)
            )

    def skip_book(self, entry_id: str, reason: str) -> None:
        with self.transaction() as db:
            db.execute(
                "UPDATE books SET state='skipped', last_error=? WHERE entry_id=?", (re.sub(r"/home/[^/\s]+", "~", reason)[:500], entry_id)
            )

    def miss_book(self, entry_id: str, reason: str) -> None:
        """Record that the live platform no longer serves this entry."""
        with self.transaction() as db:
            db.execute(
                "UPDATE books SET state='missing', last_error=?, finished_at=? WHERE entry_id=?",
                (re.sub(r"/home/[^/\s]+", "~", reason)[:500], _now(), entry_id),
            )

    def patch_book(self, entry_id: str, **fields: Any) -> None:
        if not fields:
            return
        columns = ", ".join(f"{k}=?" for k in fields)
        with self.transaction() as db:
            db.execute(f"UPDATE books SET {columns} WHERE entry_id=?", (*fields.values(), entry_id))

    def recover_in_progress(self) -> int:
        """Return an interrupted claim to the queue.

        A worker that dies mid-book leaves its claim behind, and ``claim_next`` would
        then refuse every later book: the run would report the queue empty while a
        book sat in progress forever. Recovery is for the *worker* to call at startup,
        under the process lock — a reader asking for status must not change the queue.

        The cooldown is untouched: the wall stamp of the last finished attempt still
        governs when the next book may start.
        """
        with self.transaction() as db:
            cursor = db.execute(
                "UPDATE books SET state='pending', last_error=? WHERE state='in_progress'",
                ("recovered after an interrupted run",),
            )
            return cursor.rowcount

    # ----------------------------------------------------------- publication
    def unpublished(self, *, limit: int | None = None) -> list[sqlite3.Row]:
        """Books finished since the last checkpoint, oldest first.

        This is the streaming contract with the publisher: it names exactly the books
        that are new or refreshed, so a merge reads those directories and nothing else.
        """
        sql = "SELECT * FROM books WHERE state='done' AND published=0 ORDER BY finished_at, entry_id"
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        with closing(self._connect()) as db:
            return db.execute(sql).fetchall()

    def mark_published(self, entry_ids: Iterable[str], *, generation: int) -> int:
        """Record that a generation now contains these books. Idempotent."""
        ids = list(entry_ids)
        if not ids:
            return 0
        with self.transaction() as db:
            total = 0
            for start in range(0, len(ids), 400):
                chunk = ids[start : start + 400]
                marks = ",".join("?" * len(chunk))
                cursor = db.execute(
                    f"UPDATE books SET published=1, generation=? WHERE entry_id IN ({marks})",
                    (generation, *chunk),
                )
                total += cursor.rowcount
            db.execute(
                "INSERT INTO meta(key, value) VALUES('generation', ?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(generation),),
            )
        return total

    def finished_at(self) -> float | None:
        """When the last attempt ended, as a wall clock stamp."""
        with closing(self._connect()) as db:
            row = db.execute("SELECT value FROM meta WHERE key='last_finished_at'").fetchone()
        try:
            return float(row["value"]) if row else None
        except (TypeError, ValueError):
            return None

    def touch_finished(self) -> None:
        with self.transaction() as db:
            db.execute(
                "INSERT INTO meta(key, value) VALUES('last_finished_at', ?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(time.time()),),
            )

    def block_private(self, entry_ids: Iterable[str], reason: str) -> int:
        """Stop snapshot entries of a private project from ever being fetched."""
        ids = list(entry_ids)
        if not ids:
            return 0
        with self.transaction() as db:
            total = 0
            for start in range(0, len(ids), 400):
                chunk = ids[start : start + 400]
                marks = ",".join("?" * len(chunk))
                cursor = db.execute(
                    f"UPDATE books SET state='skipped', last_error=? WHERE entry_id IN ({marks})"
                    " AND state='pending'",
                    (re.sub(r"/home/[^/\s]+", "~", reason)[:500], *chunk),
                )
                total += cursor.rowcount
        return total

    def generation(self) -> int:
        with closing(self._connect()) as db:
            row = db.execute("SELECT value FROM meta WHERE key='generation'").fetchone()
        return int(row["value"]) if row else 0

    def coverage(self) -> dict[str, int]:
        """The catalogue honestly: live, snapshot, both, and either alone."""
        with closing(self._connect()) as db:

            def count(sql: str) -> int:
                return db.execute(sql).fetchone()[0]

            return {
                "snapshot": count("SELECT COUNT(*) FROM books WHERE origin IN ('snapshot','both')"),
                "live": count("SELECT COUNT(*) FROM books WHERE origin IN ('live','both')"),
                "both": count("SELECT COUNT(*) FROM books WHERE origin='both'"),
                "snapshot_only": count("SELECT COUNT(*) FROM books WHERE origin='snapshot'"),
                "live_only": count("SELECT COUNT(*) FROM books WHERE origin='live'"),
                "missing_live": count("SELECT COUNT(*) FROM books WHERE state='missing'"),
                "collected": count("SELECT COUNT(*) FROM books WHERE state='done'"),
                "pending": count("SELECT COUNT(*) FROM books WHERE state='pending'"),
                "unpublished": count("SELECT COUNT(*) FROM books WHERE state='done' AND published=0"),
            }

    # ------------------------------------------------------------------ reads
    def book(self, entry_id: str) -> sqlite3.Row | None:
        with closing(self._connect()) as db:
            return db.execute("SELECT * FROM books WHERE entry_id=?", (entry_id,)).fetchone()

    def claim_entry(self, entry_id: str, *, origin: str = "manual") -> sqlite3.Row | None:
        """Claim one named book, queueing it first when it is new."""
        self.record_book(entry_id, project_id=None, collection_id=None, position=None, origin=origin)
        with self.transaction() as db:
            running = db.execute("SELECT entry_id FROM books WHERE state='in_progress'").fetchone()
            if running:
                return None
            db.execute(
                "UPDATE books SET state='in_progress', started_at=?,"
                " attempts=attempts+1 WHERE entry_id=?"
                " AND state IN ('pending','failed','missing')",
                (_now(), entry_id),
            )
            return db.execute("SELECT * FROM books WHERE entry_id=?", (entry_id,)).fetchone()

    def books_for_project(self, project_id: str) -> list[sqlite3.Row]:
        with closing(self._connect()) as db:
            return db.execute(
                "SELECT * FROM books WHERE project_id=? ORDER BY entry_id", (project_id,)
            ).fetchall()

    def books(self, *, state: str | None = None) -> list[sqlite3.Row]:
        with closing(self._connect()) as db:
            if state:
                return db.execute("SELECT * FROM books WHERE state=? ORDER BY entry_id", (state,)).fetchall()
            return db.execute("SELECT * FROM books ORDER BY entry_id").fetchall()

    def projects(self, *, state: str | None = None) -> list[sqlite3.Row]:
        with closing(self._connect()) as db:
            if state:
                return db.execute("SELECT * FROM projects WHERE state=? ORDER BY id", (state,)).fetchall()
            return db.execute("SELECT * FROM projects ORDER BY id").fetchall()

    def collections(self, *, state: str | None = None) -> list[sqlite3.Row]:
        with closing(self._connect()) as db:
            if state:
                return db.execute("SELECT * FROM collections WHERE state=? ORDER BY id", (state,)).fetchall()
            return db.execute("SELECT * FROM collections ORDER BY id").fetchall()

    def counts(self) -> dict[str, int]:
        with closing(self._connect()) as db:
            books = {
                row["state"]: row["n"]
                for row in db.execute("SELECT state, COUNT(*) AS n FROM books GROUP BY state")
            }
            return {
                "books": books,
                "projects": db.execute("SELECT COUNT(*) FROM projects").fetchone()[0],
                "collections": db.execute("SELECT COUNT(*) FROM collections").fetchone()[0],
            }


def _read_info(path: Path) -> list[dict[str, str]]:
    """The rows of an ``info.tsv``, tab separated, with the header it declares."""
    import csv

    with open(path, encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        return [{k: (v or "") for k, v in row.items() if k} for row in reader]


def _whole(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------ fetching
@dataclass
class Fetcher:
    """JSON fetches with pacing, backoff and a fresh cache.

    The cache is per run and in memory: a restart re-reads the API. A stale cached
    project list is how a collector silently stops finding new books.
    """

    client: Any
    pacer: Pacer
    retries: int = RETRIES
    backoff: float = BACKOFF
    sleeper: Callable[[float], None] = time.sleep
    cache: OrderedDict[str, Any] = field(default_factory=OrderedDict)
    requests: int = 0
    cache_max: int = CACHE_MAX

    def remember(self, url: str, payload: Any) -> None:
        """Keep a response briefly, evicting the oldest once the window is full."""
        self.cache[url] = payload
        self.cache.move_to_end(url)
        while len(self.cache) > self.cache_max:
            self.cache.popitem(last=False)

    def forget(self, url: str) -> None:
        """Drop a response that will not be asked for again in this run."""
        self.cache.pop(url, None)

    def json(self, url: str) -> Any:
        if url in self.cache:
            self.cache.move_to_end(url)
            return self.cache[url]
        last: Exception | None = None
        for attempt in range(1, self.retries + 1):
            delay = self.backoff * attempt
            self.pacer.before_request()
            self.requests += 1
            try:
                response = self.client.get(
                    url, headers={"Accept": "application/json", "Cache-Control": "no-cache",
                                  "User-Agent": USER_AGENT}, timeout=45.0
                )
            except Exception as error:  # noqa: BLE001 — a transport failure is retryable
                last = error
            else:
                if response.status_code == 200:
                    payload = response.json()
                    self.remember(url, payload)
                    return payload
                if response.status_code in (404, 410):
                    raise NotFound(f"{url} answered {response.status_code}")
                if response.status_code in (401, 403, 451):
                    # Refused, not absent. Kept distinct so it is never recorded as
                    # "the catalogue lost this book".
                    raise Refused(f"{url} answered {response.status_code}")
                last = Retryable(f"{url} answered {response.status_code}")
                retry_after = getattr(response, "headers", {}).get("Retry-After")
                if retry_after:
                    try:
                        delay = max(delay, float(retry_after))
                    except ValueError:
                        try:
                            delay = max(delay, parsedate_to_datetime(retry_after).timestamp() - time.time())
                        except (ValueError, TypeError):
                            pass
            if attempt < self.retries:
                self.sleeper(delay)
        raise Retryable(str(last) if last else f"{url} failed")


# ------------------------------------------------------------------- records
def page_id(entry_id: str, index0: int) -> str:
    """The page id for a transcription index.

    ``transcriptions[].index`` is 0-based on the API; every page id the atlas already
    holds is 1-based. Converting here, in one place, is what keeps a collected book
    addressable by the same ``hk:<entry>:<page>`` key as the snapshot.
    """
    return f"hk:{entry_id}:{int(index0) + 1}"


def _updated_at(value: Any) -> str | None:
    """A transcription's revision timestamp, whatever shape the API used."""
    if isinstance(value, Mapping):
        seconds = value.get("_seconds", value.get("seconds"))
        if isinstance(seconds, (int, float)):
            return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(seconds))
    if isinstance(value, (int, float)):
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(value))
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _text_of(transcription: Mapping[str, Any] | None) -> str:
    text = (transcription or {}).get("text")
    return text if isinstance(text, str) else ""


def _same_canvas(named: str, held: str) -> bool:
    """Whether a transcription's `canvasId` and a manifest canvas id are one canvas.

    Some IIIF servers (adeac.jp among them) serve each canvas as a JSON document,
    and the platform records that document's URL, `…/canvas/p1.json`, where the
    manifest's id is `…/canvas/p1`. Any other difference is a real mismatch.
    """
    if not (isinstance(named, str) and isinstance(held, str)):
        return named == held
    return named.removesuffix(".json") == held.removesuffix(".json")


def book_record(entry: Mapping[str, Any], *, entry_id: str) -> dict[str, Any]:
    """One book, normalised: rights, revisions, canvases and pages.

    The canvas list and the transcription list are paired by position, which is how the
    API orders them, and the pairing is checked against ``canvasId`` so a mismatch is
    reported rather than silently mis-numbered.
    """
    canvases = list(entry.get("canvases") or [])
    transcriptions = {}
    for item in entry.get("transcriptions") or []:
        if not isinstance(item, Mapping) or _whole(item.get("index")) is None:
            raise AlignmentError(f"{entry_id}: transcription without a valid page index")
        index = _whole(item["index"])
        if index < 0 or index in transcriptions:
            raise AlignmentError(f"{entry_id}: duplicate or negative transcription index {index}")
        transcriptions[index] = item
    pages = []
    for position, canvas in enumerate(canvases):
        if not isinstance(canvas, Mapping):
            raise AlignmentError(f"{entry_id}: unrecognised canvas at position {position}")
        transcription = transcriptions.get(position)
        canvas_id = canvas.get("id")
        named = (transcription or {}).get("canvasId")
        if named and canvas_id and not _same_canvas(named, canvas_id):
            # Positional pairing is the API's own order, but when a transcription
            # names a different canvas the two lists disagree. Guessing would write a
            # page's text under another page's image.
            raise AlignmentError(
                f"{entry_id}: transcription {position} names canvas {named!r},"
                f" but position {position} holds {canvas_id!r}"
            )
        pages.append(
            {
                "page_id": page_id(entry_id, position),
                "index": position,
                "canvas_id": canvas_id,
                "width": _whole(canvas.get("width")),
                "height": _whole(canvas.get("height")),
                # A reference, never a download.
                "image_url": canvas.get("imageUrl"),
                "info_json_url": canvas.get("infoJsonUrl"),
                "thumbnail_url": canvas.get("thumbnailUrl"),
                "transcription_id": (transcription or {}).get("id"),
                "text": _text_of(transcription),
                "text_state": "unverified-transcription",
                "revision": _updated_at((transcription or {}).get("updatedAt")),
                "status": (transcription or {}).get("status"),
            }
        )
    for index, transcription in sorted(transcriptions.items()):
        if index < len(canvases):
            continue
        # A transcription with no canvas still names a page's text; it is kept so the
        # text is not lost, and flagged because no rectangle can be claimed for it.
        pages.append(
            {
                "page_id": page_id(entry_id, index),
                "index": index,
                "canvas_id": None,
                "width": None,
                "height": None,
                "image_url": None,
                "info_json_url": None,
                "thumbnail_url": None,
                "transcription_id": transcription.get("id"),
                "text": _text_of(transcription),
                "text_state": "unverified-transcription",
                "revision": _updated_at(transcription.get("updatedAt")),
                "status": transcription.get("status"),
                "canvas_missing": True,
            }
        )
    metadata = {
        str(m.get("label")): m.get("value") for m in (entry.get("metadata") or []) if isinstance(m, Mapping)
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "honkoku-book",
        "entry_id": entry_id,
        "label": entry.get("label"),
        "project_id": entry.get("projectId"),
        "collection_id": entry.get("collectionId"),
        "position": _whole(entry.get("index")),
        "size": _whole(entry.get("size")),
        "progress": _whole(entry.get("progress")),
        "licence": entry.get("license") or None,
        "attribution": entry.get("attribution") or None,
        "r18": bool(entry.get("r18")),
        "manifest_url": entry.get("manifestUrl"),
        "manifest_version": entry.get("manifestVersion"),
        "metadata": metadata,
        "source_refs": {
            "honkoku-entry": f"{API_BASE}/entries/{entry_id}",
            "iiif-manifest": entry.get("manifestUrl"),
        },
        "pages": pages,
        "page_count": len(pages),
        "transcription_count": sum(1 for p in pages if p.get("transcription_id")),
        "canvas_mismatches": 0,
        "text_state": "unverified-transcription",
        "geometry": "canvas-references-only",
        "fetched_at": _now(),
    }


def digest_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            hasher.update(block)
    return hasher.hexdigest()


def write_json(path: Path, payload: Any) -> str:
    """Write JSON through a temporary file, returning its digest."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    _sync_directory(path.parent)
    return digest_file(path)


def _sync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def publish_directory(staging: Path, final: Path) -> None:
    """Rename a finished dataset into place so that a crash leaves all of it or none of it.

    A rename is atomic, but the file contents it points at are not on disk until they are
    flushed: after a power loss or a VM reset, a book renamed from unflushed files comes back
    with every file present and empty. Each file and the staging directory are flushed first,
    and the parent after the rename.
    """
    for path in staging.iterdir():
        if path.is_file():
            with path.open("rb") as handle:
                os.fsync(handle.fileno())
    _sync_directory(staging)
    os.replace(staging, final)
    _sync_directory(final.parent)


def manifest_of(
    directory: Path, *, counts: Mapping[str, int] | None = None, command: str = "atlas collect honkoku"
) -> dict[str, Any]:
    """The manifest of a book directory, in the atlas's own dataset shape.

    A book directory is a dataset, so its manifest carries what every other dataset's
    manifest carries — ``schema_version`` from `tables`, a ``tables`` row count, a
    writer and the command — and adds the per-file digests beside them. Writing only
    our own keys would make the directory unreadable to `corpus.sources.discover`.
    """
    from .. import tables

    files = {}
    for path in sorted(directory.iterdir()):
        # The table writer leaves `.name.lock` sidecars; a published dataset carries
        # tables, not the locks that guarded writing them.
        if path.is_file() and not path.name.startswith(".") and path.name != "MANIFEST.json":
            files[path.name] = digest_file(path)
    return {
        "schema_version": tables.SCHEMA_VERSION,
        "tables": dict(counts or {}),
        "files": files,
        "writer": "glyph-atlas honkoku-collector",
        "command": command,
        "written_at": _now(),
        "kind": "honkoku-book-manifest",
        "book_schema_version": SCHEMA_VERSION,
    }


# ------------------------------------------------------------------ dataset
#: The tables a collected book publishes. The same three `tables.py` names the rest of
#: the atlas reads, so a book directory is a dataset a merge can stream rather than a
#: shape of its own.
DATASET_TABLES = ("documents", "pages", "page_texts")


def dataset_records(entry: Mapping[str, Any], *, entry_id: str) -> tuple[list[Any], ...]:
    """The Document, Page and PageText rows for one book.

    Page ids are 1-based and the text is labelled exactly as the API door labels it in
    the rest of the atlas, so a book collected here and a book read from the snapshot
    are the same rows and a merge can tell they are the same page rather than two.
    """
    from .. import rights
    from ..schema import Document, Page, PageText

    record = book_record(entry, entry_id=entry_id)
    document_id = f"hk:{entry_id}"
    document = Document(
        id=document_id,
        title=record["label"] or entry_id,
        source_refs={
            "honkoku-data": entry_id,
            "honkoku-entry": f"{API_BASE}/entries/{entry_id}",
            "iiif-manifest": record["manifest_url"] or "",
        },
        holder=record["attribution"] or "",
        shelfmark="",
        image_rights=rights.resolve(
            licence=record["licence"], url=record["licence"], holder=record["attribution"]
        ),
        text_rights=rights.resolve(licence="CC-BY-SA-4.0"),
        meta={
            "project_id": record["project_id"],
            "collection_id": record["collection_id"],
            "position": record["position"],
            "size": record["size"],
            "progress": record["progress"],
            "r18": record["r18"],
            "metadata": record["metadata"],
            "origin": "honkoku-api-live",
            "collected_at": record["fetched_at"],
        },
    )
    pages, texts = [], []
    for page in record["pages"]:
        pages.append(
            Page(
                id=page["page_id"],
                document_id=document_id,
                seq=page["index"] + 1,
                canvas=page["canvas_id"],
                image=page["image_url"] or "",
                width=page["width"] or 0,
                height=page["height"] or 0,
                transcription={
                    "source": "honkoku-api",
                    "entry id": entry_id,
                    "revision": page["revision"] or "",
                },
                meta={"canvas_missing": True} if page.get("canvas_missing") else {},
            )
        )
        texts.append(
            PageText(
                page_id=page["page_id"],
                source="honkoku-api",
                revision=page["revision"],
                text_raw=page["text"],
            )
        )
    return [document], pages, texts


def write_dataset(
    directory: Path, entry: Mapping[str, Any], *, entry_id: str, command: str = "atlas collect honkoku"
) -> dict[str, int]:
    """Write one book as a small dataset, from staging, ready to be published."""
    from .. import tables
    from ..schema import Document, Page, PageText

    documents, pages, texts = dataset_records(entry, entry_id=entry_id)
    # Explicit models, not `type(rows[0])`: a book with no canvases yet still has a
    # schema, and an empty table is a result rather than an error.
    tables.write(directory / "documents.parquet", documents, Document, command=command)
    tables.write(directory / "pages.parquet", pages, Page)
    tables.write(directory / "page_texts.parquet", texts, PageText)
    return {"documents": len(documents), "pages": len(pages), "page_texts": len(texts)}


# ----------------------------------------------------------------- collector
@dataclass
class Collector:
    """The worker. All I/O goes through `fetcher`, so tests replace one object."""

    root: Path
    fetcher: Fetcher
    min_free_bytes: int = MIN_FREE_BYTES
    max_attempts: int = 3
    disk_free: Callable[[Path], int] | None = None
    sleeper: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
    now: Callable[[], str] = _now
    on_book: Callable[[str, Path, Mapping[str, Any]], None] | None = None
    command: str = "atlas collect honkoku"
    discovery_epoch_seconds: float = DISCOVERY_EPOCH_SECONDS
    wall: Callable[[], float] = time.time

    def __post_init__(self) -> None:
        self.root = Path(self.root)
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / BOOKS_DIR).mkdir(exist_ok=True)
        (self.root / STAGING_DIR).mkdir(exist_ok=True)
        self.queue = Queue(self.root / QUEUE_FILE)
        self._disk_free = self.disk_free or (lambda p: shutil.disk_usage(p).free)

    # ------------------------------------------------------------ disk guard
    def free_bytes(self) -> int:
        return self._disk_free(self.root)

    def check_space(self) -> None:
        free = self.free_bytes()
        if free < self.min_free_bytes:
            raise OutOfSpace(
                f"{free / 1024**3:.2f} GiB free, floor is {self.min_free_bytes / 1024**3:.2f} GiB"
            )

    # ------------------------------------------------------------- discovery
    def discover_projects(self) -> dict[str, int]:
        """Read the public project list and queue every visible project."""
        projects = self.fetcher.json(PROJECTS_URL)
        if not isinstance(projects, list):
            raise CollectorError("the project list is not a list")
        added = private = 0
        for project in projects:
            if not isinstance(project, Mapping):
                continue
            if project.get("isPrivate"):
                private += 1
                self.queue.block_private(
                    [row["entry_id"] for row in self.queue.books_for_project(project["id"])],
                    "project is private",
                )
            if self.queue.record_project(project):
                added += 1
        return {"projects": len(projects), "added": added, "private": private}

    def discover_collections(self, project_id: str) -> dict[str, int]:
        """Queue the collections of one public project."""
        project = self.fetcher.json(PROJECT_URL.format(project=project_id))
        if not isinstance(project, Mapping):
            raise CollectorError(f"project {project_id} is not an object")
        if project.get("isPrivate"):
            # Not enumerated, and any snapshot rows already queued under it are blocked
            # so a seeded entry id cannot become a fetch of private material.
            blocked = self.queue.block_private(
                [row["entry_id"] for row in self.queue.books_for_project(project_id)], "project is private"
            )
            with self.queue.transaction() as db:
                db.execute("UPDATE projects SET state='skipped', source=NULL WHERE id=?", (project_id,))
            return {"collections": 0, "skipped": "private", "blocked": blocked}
        added = 0
        for collection_id in project.get("collections") or []:
            if self.queue.record_collection({"id": collection_id}, project_id):
                added += 1
        with self.queue.transaction() as db:
            db.execute("UPDATE projects SET state='done' WHERE id=?", (project_id,))
        return {"collections": len(project.get("collections") or []), "added": added}

    def enumerate_books(self, collection_id: str) -> dict[str, int]:
        """Queue the entries of one displayed collection."""
        collection = self.fetcher.json(COLLECTION_URL.format(collection=collection_id))
        if not isinstance(collection, Mapping):
            raise CollectorError(f"collection {collection_id} is not an object")
        project_id = collection.get("projectId")
        if collection.get("isPrivate"):
            with self.queue.transaction() as db:
                db.execute("UPDATE collections SET state='skipped' WHERE id=?", (collection_id,))
            return {"entries": 0, "skipped": "private collection"}
        if collection.get("display") is False:
            # Not displayed is not the same as private; either way it is not enumerated.
            with self.queue.transaction() as db:
                db.execute("UPDATE collections SET state='skipped' WHERE id=?", (collection_id,))
            return {"entries": 0, "skipped": "not displayed"}
        added = 0
        entries = collection.get("entries") or []
        if not isinstance(entries, list):
            raise CollectorError(f"collection {collection_id}: entries is not a list")
        for position, entry_id in enumerate(entries):
            if isinstance(entry_id, Mapping):
                entry_id = entry_id.get("id")
            if not isinstance(entry_id, str) or not entry_id:
                raise CollectorError(f"collection {collection_id}: unrecognised entry at {position}")
            if self.queue.record_book(
                entry_id, project_id=project_id, collection_id=collection_id, position=position, origin="live"
            ):
                added += 1
        with self.queue.transaction() as db:
            db.execute("UPDATE collections SET state='done' WHERE id=?", (collection_id,))
        return {"entries": len(entries), "added": added}

    def _open_discovery_epoch(self) -> bool:
        """Reopen walked rows when the last walk finished and the interval passed.

        An interrupted walk resumes instead: rows reopen only when the previous
        walk completed, or has been open longer than the interval and counts as
        abandoned. `skipped` rows stay skipped — they record deliberate refusals
        of private or hidden material, not walk progress.
        """
        now = self.wall()
        with self.queue.transaction() as db:
            started = _meta_stamp(db, "discovery_started_at")
            finished = _meta_stamp(db, "discovery_finished_at")
            if started is not None and (finished is None or finished < started):
                if now - started < self.discovery_epoch_seconds:
                    return False
            elif finished is not None and now - finished < self.discovery_epoch_seconds:
                return False
            db.execute(
                "INSERT INTO meta(key, value) VALUES('discovery_started_at', ?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(now),),
            )
            db.execute("UPDATE projects SET state='pending' WHERE state='done'")
            db.execute("UPDATE collections SET state='pending' WHERE state='done'")
        return True

    def _close_discovery_epoch(self) -> None:
        """Stamp the walk complete, when this call is the one that finished it."""
        with self.queue.transaction() as db:
            started = _meta_stamp(db, "discovery_started_at")
            finished = _meta_stamp(db, "discovery_finished_at")
            if started is None or (finished is not None and finished >= started):
                return
            db.execute(
                "INSERT INTO meta(key, value) VALUES('discovery_finished_at', ?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(self.wall()),),
            )

    def discover(self) -> dict[str, Any]:
        """Walk projects, then their collections, then their entries."""
        self.check_space()
        self._open_discovery_epoch()
        with self.queue.transaction() as db:
            db.execute("UPDATE projects SET state='pending' WHERE state='failed'")
            db.execute("UPDATE collections SET state='pending' WHERE state='failed'")
        result: dict[str, Any] = {"projects": self.discover_projects(), "collections": 0, "entries": 0}
        self.write_outputs()
        for project in self.queue.projects(state="pending"):
            self.check_space()
            try:
                found = self.discover_collections(project["id"])
            except CollectorError as error:
                with self.queue.transaction() as db:
                    db.execute(
                        "UPDATE projects SET state='failed', source=? WHERE id=?",
                        (str(error)[:200], project["id"]),
                    )
                continue
            result["collections"] += found["collections"]
            self.write_outputs()
        for collection in self.queue.collections(state="pending"):
            self.check_space()
            try:
                found = self.enumerate_books(collection["id"])
            except CollectorError as error:
                with self.queue.transaction() as db:
                    db.execute(
                        "UPDATE collections SET state='failed', source=? WHERE id=?",
                        (str(error)[:200], collection["id"]),
                    )
                continue
            result["entries"] += found["entries"]
            self.write_outputs()
        self._close_discovery_epoch()
        return result

    def recover(self) -> int:
        """Put an interrupted claim back, once, at worker startup."""
        return self.queue.recover_in_progress()

    # --------------------------------------------------------------- collect
    def collect_book(self, entry_id: str) -> dict[str, Any]:
        """Fetch one book and write its immutable dataset.

        The dataset is assembled in staging and moved into place in one step, so an
        interrupted run never leaves a directory that looks finished. An existing
        complete dataset is left alone: the same book is never collected twice.
        """
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", entry_id):
            raise CollectorError("invalid book identifier")
        with closing(self.queue._connect()) as db:
            row = db.execute("SELECT project_id FROM books WHERE entry_id=?", (entry_id,)).fetchone()
        if row and row["project_id"]:
            self._public_project(row["project_id"])
        final = self.root / BOOKS_DIR / entry_id
        if (final / "MANIFEST.json").is_file() and (final / "book.json").is_file():
            # A previous run may have died between the rename and the queue write, so
            # the directory is read and validated rather than assumed: the caller gets
            # the same shape as a fresh collection, and the queue learns the book is
            # done instead of asking for it forever.
            return self._existing(entry_id, final)
        self.check_space()
        staging = self.root / STAGING_DIR / entry_id
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True, exist_ok=True)
        url = ENTRY_URL.format(entry=entry_id)
        try:
            entry = self.fetcher.json(url)
        except NotFound as error:
            # Only a 404/410 means the entry is gone. The row stays, flagged, so a
            # snapshot book that has left the platform is still counted and named.
            shutil.rmtree(staging, ignore_errors=True)
            raise EntryMissing(str(error)) from error
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        finally:
            # Committed or not, this body is done with: keeping thousands of entry
            # responses would be the whole corpus in RAM.
            self.fetcher.forget(url)
        if not isinstance(entry, Mapping):
            raise CollectorError(f"entry {entry_id} is not an object")
        if entry.get("isPrivate"):
            raise Refused("entry is private")
        if entry.get("projectId"):
            self._public_project(entry["projectId"])
        record = book_record(entry, entry_id=entry_id)
        counts = write_dataset(staging, entry, entry_id=entry_id, command=self.command)
        for lock in staging.glob(".*.lock"):
            lock.unlink()
        write_json(staging / "entry.json", entry)
        write_json(staging / "book.json", record)
        write_json(staging / "MANIFEST.json", manifest_of(staging, counts=counts, command=self.command))
        digest = digest_file(staging / "MANIFEST.json")
        if final.exists():
            shutil.rmtree(final)
        publish_directory(staging, final)
        result = {
            "entry_id": entry_id,
            "status": "collected",
            "dataset": f"{BOOKS_DIR}/{entry_id}",
            "pages": record["page_count"],
            "tables": counts,
            "transcriptions": record["transcription_count"],
            "manifest_sha256": digest,
            "licence": record["licence"],
            "label": record["label"],
        }
        if self.on_book is not None:
            # The integration seam: root publishes on its own schedule, so the hook is
            # told what was written and nothing more.
            self.on_book(entry_id, final, result)
        return result

    def _public_project(self, project_id: str) -> None:
        from urllib.parse import quote

        project = self.fetcher.json(PROJECT_URL.format(project=quote(project_id, safe="")))
        if not isinstance(project, Mapping) or project.get("isPrivate"):
            raise Refused("project is not public")

    def _existing(self, entry_id: str, final: Path) -> dict[str, Any]:
        """The result for a dataset that is already on disk."""
        import pyarrow.parquet as pq

        record = json.loads((final / "book.json").read_text(encoding="utf-8"))
        manifest = json.loads((final / "MANIFEST.json").read_text(encoding="utf-8"))
        tables_present = [name for name in DATASET_TABLES if (final / f"{name}.parquet").is_file()]
        if len(tables_present) != len(DATASET_TABLES):
            raise CollectorError(f"{entry_id} has an incomplete dataset")
        rows = {name: pq.ParquetFile(final / f"{name}.parquet").metadata.num_rows for name in DATASET_TABLES}
        return {
            "entry_id": entry_id,
            "status": "already-collected",
            "dataset": f"{BOOKS_DIR}/{entry_id}",
            "pages": record.get("page_count", rows["pages"]),
            "transcriptions": record.get("transcription_count", rows["page_texts"]),
            "tables": rows,
            "manifest_sha256": digest_file(final / "MANIFEST.json"),
            "licence": record.get("licence"),
            "label": record.get("label"),
            "recovered": True,
            "manifest_schema_version": manifest.get("schema_version"),
        }

    def collect_entry(self, entry_id: str) -> dict[str, Any]:
        """Collect one named book, for a smoke test or a targeted re-run."""
        final = self.root / BOOKS_DIR / entry_id
        if (final / "MANIFEST.json").is_file() and (final / "book.json").is_file():
            return self._existing(entry_id, final)
        self.recover()
        row = self.queue.claim_entry(entry_id)
        if row is None:
            raise Busy("another book is in progress")
        self.fetcher.pacer.before_book(self.queue.finished_at())
        try:
            result = self.collect_book(entry_id)
        except EntryMissing as error:
            self.queue.miss_book(entry_id, str(error))
            self.queue.touch_finished()
            return {"entry_id": entry_id, "status": "missing", "reason": str(error)}
        except AlignmentError as error:
            self.queue.fail_book(
                entry_id, str(error), attempts=row["attempts"], max_attempts=self.max_attempts
            )
            self.queue.touch_finished()
            return {"entry_id": entry_id, "status": "quarantined", "reason": str(error)}
        except Exception as error:
            self.queue.fail_book(
                entry_id,
                f"{type(error).__name__}: {error}",
                attempts=row["attempts"],
                max_attempts=self.max_attempts,
            )
            self.queue.touch_finished()
            raise
        self.queue.finish_book(
            entry_id,
            dataset=result["dataset"],
            pages=result["pages"],
            transcriptions=result["transcriptions"],
            manifest_sha256=result["manifest_sha256"],
            licence=result["licence"],
            label=result["label"],
        )
        self.queue.touch_finished()
        self.write_outputs()
        return result

    # ------------------------------------------------------------------- run
    def run(
        self,
        *,
        max_books: int | None = None,
        max_seconds: float | None = None,
    ) -> dict[str, Any]:
        """Collect pending books one at a time, unhurried.

        Returns a status dict either way: a disk floor, a deadline or a run of failures
        ends the run cleanly with everything already written left in place.
        """
        # Startup, not status: a claim left by a dead worker is returned to the queue
        # so the run resumes instead of reporting the queue empty.
        self.recover()
        started = self.clock()
        collected = failures = 0
        deferred: set[str] = set()
        stop: str | None = None
        while max_books is None or collected < max_books:
            if max_seconds is not None and self.clock() - started >= max_seconds:
                stop = "deadline"
                break
            try:
                self.check_space()
            except OutOfSpace as error:
                stop = str(error)
                break
            finished_at = self.queue.finished_at()
            row = self.queue.claim_next(deferred)
            if row is None:
                stop = "retry-later" if deferred else "queue-empty"
                break
            # The pause is measured from the end of the previous attempt, whoever made
            # it and however it ended, and it is read from the queue so a restart
            # resumes mid-pause rather than starting a book immediately.
            self.fetcher.pacer.before_book(finished_at)
            entry_id = row["entry_id"]
            self.write_outputs()
            try:
                result = self.collect_book(entry_id)
            except OutOfSpace as error:
                self.queue.release_book(entry_id, str(error))
                stop = str(error)
                break
            except EntryMissing as error:
                self.queue.miss_book(entry_id, str(error))
                self.queue.touch_finished()
                failures += 1
                continue
            except AlignmentError as error:
                # Nothing was published. The book is quarantined and retried later.
                state = self.queue.fail_book(
                    entry_id, str(error), attempts=row["attempts"], max_attempts=self.max_attempts
                )
                self.queue.touch_finished()
                failures += 1
                if state == "pending":
                    deferred.add(entry_id)
                continue
            except CollectorError as error:
                self.queue.skip_book(entry_id, str(error))
                self.queue.touch_finished()
                failures += 1
                continue
            except Exception as error:  # noqa: BLE001 — a fetch failure is retryable
                state = self.queue.fail_book(
                    entry_id,
                    f"{type(error).__name__}: {error}",
                    attempts=row["attempts"],
                    max_attempts=self.max_attempts,
                )
                self.queue.touch_finished()
                failures += 1
                if state == "pending":
                    # Left for a later run rather than called done, and set aside here
                    # so one unreachable book cannot stall the ones behind it.
                    deferred.add(entry_id)
                continue
            self.queue.finish_book(
                entry_id,
                dataset=result["dataset"],
                pages=result["pages"],
                transcriptions=result["transcriptions"],
                manifest_sha256=result["manifest_sha256"],
                licence=result["licence"],
                label=result["label"],
            )
            collected += 1
            self.queue.touch_finished()
            self.fetcher.pacer.after_book()
            self.write_outputs()
        self.write_outputs()
        return {
            "collected": collected,
            "failures": failures,
            "stop": stop,
            "seconds": round(self.clock() - started, 1),
            "status": self.status(),
        }

    # --------------------------------------------------------------- outputs
    def status(self) -> dict[str, Any]:
        """Counts, the book in progress and the disk headroom. No paths but our own."""
        counts = self.queue.counts()
        running = self.queue.books(state="in_progress")
        return {
            "kind": "honkoku-collection-status",
            "schema_version": SCHEMA_VERSION,
            "updated_at": self.now(),
            "root": self.root.name,
            "books": counts["books"],
            "projects": counts["projects"],
            "collections": counts["collections"],
            "in_progress": running[0]["entry_id"] if running else None,
            "generation": self.queue.generation(),
            "unpublished": len(self.queue.unpublished()),
            "coverage": self.queue.coverage(),
            "free_bytes": self.free_bytes(),
            "min_free_bytes": self.min_free_bytes,
            "host_pause": self.fetcher.pacer.host_pause,
            "book_pause": self.fetcher.pacer.book_pause,
        }

    def index(self) -> dict[str, Any]:
        """The latest dataset path and state of every collected book."""
        books = []
        for row in self.queue.books():
            if row["state"] != "done":
                continue
            books.append(
                {
                    "entry_id": row["entry_id"],
                    "dataset": f"{BOOKS_DIR}/{row['entry_id']}",
                    "label": row["label"],
                    "project_id": row["project_id"],
                    "collection_id": row["collection_id"],
                    "pages": row["pages"],
                    "transcriptions": row["transcriptions"],
                    "licence": row["licence"],
                    "manifest_sha256": row["manifest_sha256"],
                    "generation": row["generation"],
                    "tables": [f"{name}.parquet" for name in DATASET_TABLES],
                    "finished_at": row["finished_at"],
                    "source_url": f"{API_BASE}/entries/{row['entry_id']}",
                }
            )
        return {
            "kind": "honkoku-collection-index",
            "schema_version": SCHEMA_VERSION,
            "updated_at": self.now(),
            "count": len(books),
            "books": books,
        }

    def write_outputs(self) -> None:
        write_json(self.root / STATUS_FILE, self.status())
        write_json(self.root / INDEX_FILE, self.index())


def load_book(root: str | Path, entry_id: str) -> dict[str, Any] | None:
    """A collected book's record, or None when it has not been collected."""
    path = Path(root) / BOOKS_DIR / entry_id / "book.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def load_index(root: str | Path) -> dict[str, Any] | None:
    path = Path(root) / INDEX_FILE
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def load_status(root: str | Path) -> dict[str, Any] | None:
    """The last written status, read from disk.

    Opening the queue to answer ``--status`` would create tables and directories as a
    side effect, and a reader should not change what it reads.
    """
    path = Path(root) / STATUS_FILE
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


@contextmanager
def process_lock(root: str | Path, *, name: str = LOCK_FILE) -> Iterator[Path]:
    """Hold the worker lock, or raise :class:`Busy`.

    ``flock`` is released by the kernel when the process dies, so a killed worker does
    not leave the queue locked.
    """
    path = Path(root) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise Busy(f"another collector holds {path.name}") from error
        handle.write(f"{os.getpid()}\n")
        handle.flush()
        try:
            yield path
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def make_collector(
    root: str | Path,
    *,
    client: Any = None,
    host_pause: float = MIN_HOST_PAUSE,
    book_pause: float = BOOK_PAUSE,
    min_free_bytes: int = MIN_FREE_BYTES,
    sleeper: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    disk_free: Callable[[Path], int] | None = None,
    discovery_epoch_seconds: float = DISCOVERY_EPOCH_SECONDS,
) -> Collector:
    """A collector with an httpx client, for the CLI and for root's own runs."""
    if client is None:
        import httpx

        client = httpx.Client(follow_redirects=True)
    pacer = Pacer(host_pause=host_pause, book_pause=book_pause, clock=clock, sleeper=sleeper)
    fetcher = Fetcher(client=client, pacer=pacer, sleeper=sleeper)
    return Collector(
        Path(root), fetcher, min_free_bytes=min_free_bytes, disk_free=disk_free, sleeper=sleeper, clock=clock,
        discovery_epoch_seconds=discovery_epoch_seconds,
    )
