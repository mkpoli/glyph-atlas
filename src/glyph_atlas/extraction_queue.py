"""Bounded, restartable extraction from transcribed page rectangles.

Each page is committed as an immutable dataset. The review store imports completed
pages separately; this worker never rewrites its baseline or review history. `Queue.claim`
takes pending work coverage-first: a page holding characters the atlas still lacks is claimed
ahead of a page of characters it already has plenty of, as `Queue.prioritize` scores it; see
`Queue`'s docstring for the full claim order.

A unit is published through one of two gates, recorded as `meta.extraction.gate`. `consensus`: the
alignment accepted it and both visual models read its character (`quality_reason`): the classifier at
.80, and NDLkotenOCR at .10. NDLkotenOCR reads text lines, and on a lone character its reading is right
far more often than its score says: on 21 sampled pages it read 264 crops as the transcribed character
below .70, and of 40 drawn at random and the 40 lowest, all framed their character but one at .09.
`unconfirmed`: its letter has no class in the classifier under any of its readings, so alignment scores it at the
probability floor and leaves it rejected, and the classifier cannot vote for it; it is published on its
shape, unless the classifier confidently reads a kana or a neighbour there, and on NDLkotenOCR's
reading when that model's alphabet holds the character (`unconfirmed_reason`). Without
this gate no character outside the classifier's classes could reach review, and those are the rare
ones. Of the 30 units it published from 7 pages on 2026-09-27, 25 framed their character; the others
held part of it (a detector trained on common classes splits tall compounds such as 飍) or a sliver,
so reviewers and consumers should read the gate.

A page completed under an earlier policy is not extracted again; a supplement adds what the current
policy would publish there and the earlier one could not. `Queue.seed_supplements` lists the complete
pages whose output predates `POLICY`; `supplement` extracts such a page under `POLICY` and keeps the
units, of either gate, that overlap no crop the page's earlier output or an earlier supplement
published. `run` takes a supplement every `supplement_every` pages, so the backlog of new pages keeps
moving.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import sqlite3
import time
import unicodedata
from collections import Counter, defaultdict
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from . import align, images, tables, withdrawn
from .schema import PAGE_SCOPE, Classification, ReviewState, UnitKind

POLICY = "single-character-consensus-v3"
CONSENSUS = "consensus"
UNCONFIRMED = "unconfirmed"


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    default=str).encode()).hexdigest()


def atomic_json(path, value):
    """Write `value` to `path` through a scratch file of this call's own, so writers never share one."""
    temporary = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
    with temporary.open("w") as handle:
        json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)
    descriptor = os.open(path.parent, os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


#: How often a page is extracted, or published, before it is left failed for a person to look at.
MAX_ATTEMPTS = 3
#: How long a claim holds without a heartbeat. A worker renews it every eighth of this while it
#: extracts, so a page is taken back only from a worker that stopped.
LEASE_SECONDS = 600
#: How many leases a page may run before its heartbeat stops renewing it, so a worker whose
#: extraction hangs gives the page back.
PAGE_DEADLINE_LEASES = 6


def scorable_chars(text: str) -> set[str]:
    """The distinct characters of `text` worth prioritizing: no whitespace, punctuation or marks.

    Whitespace (including the common full-width U+3000) and every Unicode punctuation category
    (P*) carry no glyph to extract. Combining marks and variation selectors (Unicode category
    M*, which includes both) never stand alone as their own crop, so they are skipped too; see
    also `single_character` in `.review.atlas` for a related, string-level check.
    """
    return {c for c in text if not c.isspace() and not unicodedata.category(c).startswith(("P", "M"))}


def located_pages(source: Path) -> set[str]:
    """The pages of a dataset with at least one located line that is not page-scoped.

    Read with pyarrow, two columns at a time, because Honkoku-Lines holds more than a million lines.
    """
    import pyarrow.compute as pc
    import pyarrow.dataset as ds

    lines_path = source / "lines"
    if not lines_path.exists():
        lines_path = source / "lines.parquet"
    if not lines_path.exists():
        return set()
    table = ds.dataset(lines_path, format="parquet").to_table(
        columns=["page_id", "meta"], filter=pc.is_valid(pc.field("box")))
    found = set()
    for page_id, meta in zip(table.column("page_id").to_pylist(), table.column("meta").to_pylist(), strict=True):
        # `Line.page_scope` reads `meta["scope"]`; most lines carry no scope, so the JSON is parsed
        # only when the key appears.
        if meta and '"scope"' in meta and json.loads(meta).get("scope") == PAGE_SCOPE:
            continue
        found.add(page_id)
    return found


def load_char_counts(path: Path) -> dict[str, int]:
    """Crops the atlas already holds per character, from a corpus-index characters table.

    Reads only the `char` and `n_units` columns of the parquet table at `path` (by default
    `work/corpus-index/chars.parquet`); the caller passes the result to `Queue.prioritize`.
    """
    import pyarrow.parquet as pq

    table = pq.read_table(path, columns=["char", "n_units"])
    return dict(zip(table.column("char").to_pylist(), table.column("n_units").to_pylist(), strict=True))


class Queue:
    """A durable, restartable queue of pages to extract, claimed one at a time.

    `claim` orders pending work by focus first — the pages of documents `focus` names, such as a
    book chosen as a source of variant forms — then by coverage priority — a page scores higher the
    more its characters are still scarce in the atlas, as `prioritize` computes it — then by whether
    its image is already cached, then by the page's original rank within its book, then by document
    and page id as a stable tie-break for equal scores. `claim_supplement` takes focus first too.

    Any number of workers share a queue. A claim marks its row running under the worker's name and a
    lease the worker renews (`heartbeat`); a row whose lease lapsed is claimed again like a pending one.
    Only the worker holding a lease finishes or fails its row. An uncached page on a host another
    worker is downloading from goes after every other claimable page, so workers spread over hosts.
    """

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        # The wall clock leases are kept on, which every process reads alike; tests replace it.
        self.wall = time.time
        self._alive = None
        self._settle_until = 0.0
        self.db = sqlite3.connect(self.root / "queue.sqlite", timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        # One write transaction for the whole schema check, so workers starting together on an older
        # queue do not both add the same column.
        self.db.execute("BEGIN IMMEDIATE")
        self.db.execute("""CREATE TABLE IF NOT EXISTS pages (
            id TEXT PRIMARY KEY, document_id TEXT NOT NULL, title TEXT NOT NULL,
            source TEXT NOT NULL, cached INTEGER NOT NULL, rank INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
            output TEXT, accepted INTEGER NOT NULL DEFAULT 0, examined INTEGER NOT NULL DEFAULT 0,
            error TEXT, updated_at TEXT)""")
        columns = {r[1] for r in self.db.execute("PRAGMA table_info(pages)")}
        for name in ("published_at", "publish_error", "retry_after"):
            if name not in columns:
                self.db.execute(f"ALTER TABLE pages ADD COLUMN {name} TEXT")
        if "publish_attempts" not in columns:
            self.db.execute("ALTER TABLE pages ADD COLUMN publish_attempts INTEGER NOT NULL DEFAULT 0")
        if "priority" not in columns:
            self.db.execute("ALTER TABLE pages ADD COLUMN priority REAL NOT NULL DEFAULT 0")
        if "focus" not in columns:
            self.db.execute("ALTER TABLE pages ADD COLUMN focus INTEGER NOT NULL DEFAULT 0")
        # The policy a complete page's output was written under, so listing supplements is a query.
        if "policy" not in columns:
            self.db.execute("ALTER TABLE pages ADD COLUMN policy TEXT")
        # Who holds a running page, and until when: a worker renews its lease while it works, and a
        # page is taken back only once its lease has lapsed, so several workers can share the queue.
        if "worker" not in columns:
            self.db.execute("ALTER TABLE pages ADD COLUMN worker TEXT")
        if "lease_until" not in columns:
            self.db.execute("ALTER TABLE pages ADD COLUMN lease_until REAL")
        # The image host of the page, filled by `seed`, so a claim can keep off a host in use.
        if "host" not in columns:
            self.db.execute("ALTER TABLE pages ADD COLUMN host TEXT")
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_pages_lease ON pages(status, lease_until)")
        self.db.execute("DROP INDEX IF EXISTS idx_pages_claim_order")
        self.db.execute("""CREATE INDEX IF NOT EXISTS idx_pages_focus_claim_order
            ON pages(focus DESC, priority DESC, cached DESC, rank, document_id, id)""")
        self.db.execute("""CREATE TABLE IF NOT EXISTS supplements (
            page_id TEXT NOT NULL, policy TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0, output TEXT, added INTEGER, error TEXT,
            published_at TEXT, publish_error TEXT, publish_attempts INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT, retry_after REAL, PRIMARY KEY (page_id, policy))""")
        supplement_columns = {r[1] for r in self.db.execute("PRAGMA table_info(supplements)")}
        if "retry_after" not in supplement_columns:
            self.db.execute("ALTER TABLE supplements ADD COLUMN retry_after REAL")
        if "worker" not in supplement_columns:
            self.db.execute("ALTER TABLE supplements ADD COLUMN worker TEXT")
        if "lease_until" not in supplement_columns:
            self.db.execute("ALTER TABLE supplements ADD COLUMN lease_until REAL")
        # Settings every worker on the queue must share, such as where NDL's model runs (`pin`).
        self.db.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        self.db.commit()

    def pin(self, key, value, *, replace=False):
        """Record `value` as the queue's `key` on first use, and refuse a worker that brings another.

        NDL's provider is pinned so: the CPU and CUDA differ in the last digits of a score, which moves a
        few crops across a threshold, and a queue extracted by both would hold pages of either. `replace`
        re-pins it, for a switch made once every worker has stopped.
        """
        with self.db:
            if replace:
                self.db.execute("INSERT OR REPLACE INTO settings (key,value) VALUES(?,?)", (key, value))
            else:
                self.db.execute("INSERT OR IGNORE INTO settings (key,value) VALUES(?,?)", (key, value))
            pinned = self.db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()[0]
        if pinned != value:
            raise RuntimeError(f"this queue's {key} is {pinned}, not {value}")

    def seed(self, source: Path, *, include_ainu=False):
        """Queue the pages of `source` that hold a located transcription line.

        `extract` works only inside located lines, so a page with none, such as a page whose line
        boxes have not been derived yet, would only fail its attempts; it is left for a later seed.
        A withdrawn document's pages are not queued, and those already queued are withdrawn unless
        they have finished.
        """
        dataset = tables.Dataset(source)
        documents = {d.id: d for d in dataset.read("documents")}
        located = located_pages(Path(source))
        # Read the cache index once, rather than rescanning it for 79,000 pages.
        import pyarrow.parquet as pq
        index = images.index_path()
        records = pq.read_table(index).to_pylist() if index.exists() else []
        cached = {r[k] for r in records for k in ("url", "service") if r.get(k)}
        groups = defaultdict(list)
        for page in dataset.read("pages"):
            if page.id not in located:
                continue
            document = documents[page.document_id]
            if document.id in withdrawn.documents():
                continue
            if not include_ainu and any(s in document.title for s in ("蝦夷", "北海随筆", "アイヌ", "藻汐")):
                continue
            groups[page.document_id].append(page)
        added = 0
        with self.db:
            for document_id, pages in groups.items():
                for rank, page in enumerate(sorted(pages, key=lambda p: (p.seq, p.id))):
                    host = (urlsplit(page.image).hostname or "").lower() or None
                    added += self.db.execute("""INSERT OR IGNORE INTO pages
                        (id,document_id,title,source,cached,rank,host) VALUES(?,?,?,?,?,?,?)""",
                        (page.id, document_id, documents[document_id].title,
                         str(source), int(page.image in cached), rank, host)).rowcount
                    self.db.execute("UPDATE pages SET host=? WHERE id=? AND host IS NULL", (host, page.id))
            gone = sorted(withdrawn.documents())
            self.db.execute(f"""UPDATE pages SET status='withdrawn',worker=NULL,lease_until=NULL,updated_at=?
                WHERE status NOT IN ('complete','withdrawn') AND document_id IN ({','.join('?' * len(gone))})""",
                            (datetime.now(UTC).isoformat(), *gone))
        return added

    def focus(self, documents) -> int:
        """Claim the pages of `documents` first, and no others; an empty set clears the focus.

        Returns how many focused pages are still pending or due for a retry.
        """
        documents = sorted(set(documents))
        with self.db:
            self.db.execute("UPDATE pages SET focus=0 WHERE focus!=0")
            for start in range(0, len(documents), 500):
                batch = documents[start:start + 500]
                self.db.execute(f"UPDATE pages SET focus=1 WHERE document_id IN ({','.join('?' * len(batch))})", batch)
        return self.db.execute("SELECT count(*) FROM pages WHERE focus=1 AND status IN ('pending','retry')").fetchone()[0]

    def claim(self, worker, *, lease=LEASE_SECONDS):
        """Take the next page to extract, in the order this class's docstring describes.

        One statement picks the page and marks it running under `worker`'s lease, so two workers
        claiming at once never take the same page. A page whose lease lapsed is claimed like a pending
        one, and its attempt counts: a page that kills its worker never reaches `fail`, so after
        `MAX_ATTEMPTS` such stops it is left failed, or it would be claimed first after every restart.
        `lapse` says when a lease counts as lapsed.
        """
        now, lapsed, stopped = self.lapse(lease)
        stamp = datetime.now(UTC).isoformat()
        with self.db:
            self.db.execute("""UPDATE pages SET status='failed',updated_at=?,worker=NULL,lease_until=NULL,
                error='the worker stopped while extracting this page ' || attempts || ' times'
                WHERE status='running' AND coalesce(lease_until,0)<? AND attempts>=?""",
                            (stamp, stopped, MAX_ATTEMPTS))
            row = self.db.execute("""UPDATE pages SET status='running',attempts=attempts+1,updated_at=?,
                worker=?,lease_until=? WHERE id=(SELECT id FROM pages WHERE status='pending'
                OR (status='retry' AND CAST(retry_after AS REAL)<=?)
                OR (status='running' AND coalesce(lease_until,0)<? AND attempts<?)
                ORDER BY (cached=0 AND host IS NOT NULL AND host IN (SELECT host FROM pages
                    WHERE status='running' AND cached=0 AND lease_until>=? AND worker!=? AND host IS NOT NULL)),
                focus DESC, priority DESC, cached DESC, rank, document_id, id LIMIT 1) RETURNING *""",
                (stamp, worker, now + lease, now, lapsed, MAX_ATTEMPTS, now, worker)).fetchone()
        return dict(row) if row else None

    def renew(self, kind, ident, worker, *, lease=LEASE_SECONDS):
        """Extend `worker`'s lease on a running page or supplement; False once another worker holds it.

        Opens its own connection, so a heartbeat thread can call it while the worker's thread extracts.
        """
        table, key = ("supplements", "page_id") if kind == "supplement" else ("pages", "id")
        now = self.wall()
        self.alive(lease, now)
        with sqlite3.connect(self.root / "queue.sqlite", timeout=30) as db:
            changed = db.execute(f"UPDATE {table} SET lease_until=? WHERE {key}=? AND status='running' AND worker=?",
                                 (now + lease, ident, worker)).rowcount
        return changed > 0

    def alive(self, lease=LEASE_SECONDS, now=None):
        """Note a sign of life of this process: a claim or a renewal.

        Half a lease without one is most likely a machine that slept, and its other workers wake with
        every lease lapsed. For a quarter lease this process then claims no lapsed page, while their
        heartbeats renew them, which they do within an eighth.
        """
        now = self.wall() if now is None else now
        if self._alive is not None and now - self._alive > lease / 2:
            self._settle_until = now + lease / 4
        self._alive = now

    def lapse(self, lease=LEASE_SECONDS):
        """Now, the time a running lease must end before to count as lapsed, and the time before which a
        lapsed page counts as having stopped its worker; this notes a sign of life (`alive`).

        A page is left failed for stopping its workers only once its lease is a whole lease past, so a
        heartbeat late after a machine woke still finds it running.
        """
        now = self.wall()
        self.alive(lease, now)
        lapsed = now if now >= self._settle_until else -math.inf
        return now, lapsed, min(lapsed, now - lease)

    def prioritize(self, counts: dict[str, int]) -> int:
        """Score every pending or retry page by how much the atlas still lacks its characters.

        A page's score is the sum, over the distinct scorable characters in its transcription
        lines, of `1 / (1 + counts.get(char, 0))`: a character with no crops yet in `counts`
        contributes 1, one the atlas already has plenty of contributes close to 0. Whitespace,
        punctuation and combining marks are not scored. `counts` maps a character to the crops
        the atlas already holds for it, typically loaded from a corpus-index characters table
        with `load_char_counts`. Each page is scored from the lines of the dataset it was seeded
        from, its `source`, so pages of every seeded dataset compete on the same terms. Complete,
        running and failed pages are left alone, and only `page_id` and `text` are read from each
        lines table to keep this cheap at tens of thousands of pages. A source without a lines table
        is an error. Returns how many pages were scored.
        """
        import pyarrow.dataset as ds

        pending = self.db.execute("SELECT id, source FROM pages WHERE status IN ('pending','retry')").fetchall()
        if not pending:
            return 0
        chars_by_page: dict[str, set[str]] = {row["id"]: set() for row in pending}
        for source in sorted({row["source"] for row in pending}):
            lines_path = Path(source) / "lines"
            if not lines_path.exists():
                lines_path = Path(source) / "lines.parquet"
            if not lines_path.exists():
                # A moved or misnamed dataset would otherwise leave all its pages at 0 unnoticed.
                raise FileNotFoundError(f"{source}: queued pages name a dataset with no lines table")
            scanner = ds.dataset(lines_path, format="parquet").scanner(columns=["page_id", "text"])
            for batch in scanner.to_batches():
                for page_id, text in zip(batch.column("page_id").to_pylist(),
                                         batch.column("text").to_pylist(), strict=True):
                    found = chars_by_page.get(page_id)
                    if found is not None and text:
                        found.update(scorable_chars(text))
        scores = [(sum(1 / (1 + counts.get(ch, 0)) for ch in chars), page_id)
                  for page_id, chars in chars_by_page.items()]
        with self.db:
            self.db.executemany("UPDATE pages SET priority=? WHERE id=?", scores)
        return len(scores)

    def seed_supplements(self) -> int:
        """List the complete pages whose output was written under another policy than `POLICY`.

        A supplement of an earlier policy that has not run is superseded: `claim_supplement` takes
        only the current policy's, and this one covers what it would have added. Returns how many
        rows were added; a page already listed for `POLICY` is left as it is. A withdrawn document's
        pages get none, and those not yet run are superseded.
        """
        now = datetime.now(UTC).isoformat()
        # A page completed before the queue recorded policies has it read from its report, once.
        unknown = list(self.db.execute("SELECT id,output FROM pages WHERE status='complete' AND policy IS NULL"))
        policies = []
        for page_id, output in unknown:
            report = self.root/output/"report.json"
            if report.exists():
                policies.append((json.loads(report.read_text()).get("policy"), page_id))
        with self.db:
            self.db.executemany("UPDATE pages SET policy=? WHERE id=?", policies)
            self.db.execute("""UPDATE supplements SET status='superseded',updated_at=?
                WHERE policy!=? AND status IN ('pending','running')""", (now, POLICY))
            gone = sorted(withdrawn.documents())
            marks = ",".join("?" * len(gone))
            self.db.execute(f"""UPDATE supplements SET status='superseded',updated_at=?
                WHERE status IN ('pending','running')
                AND page_id IN (SELECT id FROM pages WHERE document_id IN ({marks}))""", (now, *gone))
            changed = self.db.total_changes
            self.db.execute(f"""INSERT OR IGNORE INTO supplements (page_id,policy)
                SELECT id,? FROM pages WHERE status='complete' AND policy IS NOT NULL AND policy!=?
                AND document_id NOT IN ({marks})""", (POLICY, POLICY, *gone))
        return self.db.total_changes - changed

    def claim_supplement(self, worker, *, lease=LEASE_SECONDS):
        """Take the supplement whose page scores highest, as `claim` orders pages, atomically.

        A supplement whose lease lapsed is claimed again, and left failed after `MAX_ATTEMPTS`, as
        `claim` treats a page.
        """
        now, lapsed, stopped = self.lapse(lease)
        stamp = datetime.now(UTC).isoformat()
        with self.db:
            self.db.execute("""UPDATE supplements SET status='failed',updated_at=?,worker=NULL,lease_until=NULL,
                error='the worker stopped while supplementing this page ' || attempts || ' times'
                WHERE status='running' AND coalesce(lease_until,0)<? AND attempts>=?""",
                            (stamp, stopped, MAX_ATTEMPTS))
            claimed = self.db.execute("""UPDATE supplements SET status='running',attempts=attempts+1,updated_at=?,
                worker=?,lease_until=? WHERE policy=? AND page_id=(SELECT s.page_id FROM supplements s
                JOIN pages p ON p.id = s.page_id WHERE s.policy=? AND ((s.status='pending'
                AND (s.retry_after IS NULL OR s.retry_after<=?))
                OR (s.status='running' AND coalesce(s.lease_until,0)<? AND s.attempts<?))
                ORDER BY p.focus DESC, p.priority DESC, p.cached DESC, p.rank, p.document_id, p.id LIMIT 1)
                RETURNING page_id""", (stamp, worker, now + lease, POLICY, POLICY, now, lapsed, MAX_ATTEMPTS)).fetchone()
            if not claimed:
                return None
            row = self.db.execute("""SELECT s.page_id, s.policy, p.* FROM supplements s JOIN pages p ON p.id = s.page_id
                WHERE s.page_id=? AND s.policy=?""", (claimed[0], POLICY)).fetchone()
            earlier = [r[0] for r in self.db.execute("""SELECT output FROM supplements
                WHERE page_id=? AND policy!=? AND status='complete'""", (row["page_id"], POLICY))]
        return {**dict(row), "earlier_supplements": earlier}

    def finish_supplement(self, page_id, worker, report, output):
        """Record a committed supplement; False when `worker` no longer holds it, and nothing is written."""
        with self.db:
            return self.db.execute("""UPDATE supplements SET status='complete',output=?,added=?,error=NULL,
                updated_at=?,worker=NULL,lease_until=NULL WHERE page_id=? AND policy=? AND status='running'
                AND worker=?""", (str(output.relative_to(self.root)), report["added"],
                                  datetime.now(UTC).isoformat(), page_id, POLICY, worker)).rowcount > 0

    def fail_supplement(self, page_id, worker, reason):
        """Leave a supplement to try again after a growing pause, or failed after `MAX_ATTEMPTS`.

        False when `worker` no longer holds it, and nothing is written.
        """
        with self.db:
            held = self.db.execute("""SELECT attempts FROM supplements WHERE page_id=? AND policy=?
                AND status='running' AND worker=?""", (page_id, POLICY, worker)).fetchone()
            if held is None:
                return False
            status = "pending" if held[0] < MAX_ATTEMPTS else "failed"
            retry_after = self.wall() + 30 * 2**max(0, held[0]-1) if status == "pending" else None
            return self.db.execute("""UPDATE supplements SET status=?,error=?,updated_at=?,retry_after=?,
                worker=NULL,lease_until=NULL WHERE page_id=? AND policy=? AND status='running' AND worker=?""",
                (status, reason, datetime.now(UTC).isoformat(), retry_after, page_id, POLICY, worker)).rowcount > 0

    def finish(self, ident, worker, report, output):
        """Record a committed page; False when `worker` no longer holds it, and nothing is written."""
        with self.db:
            return self.db.execute("""UPDATE pages SET status='complete',output=?,accepted=?,examined=?,
                error=NULL,updated_at=?,policy=?,worker=NULL,lease_until=NULL
                WHERE id=? AND status='running' AND worker=?""",
                (str(output.relative_to(self.root)), report["accepted"], report["examined"],
                 datetime.now(UTC).isoformat(), report["policy"], ident, worker)).rowcount > 0

    def fail(self, ident, worker, reason, *, retryable=False):
        """Leave a page to retry or failed; False when `worker` no longer holds it, and nothing is written."""
        with self.db:
            held = self.db.execute("SELECT attempts FROM pages WHERE id=? AND status='running' AND worker=?",
                                   (ident, worker)).fetchone()
            if held is None:
                return False
            status = "retry" if retryable and held[0] < MAX_ATTEMPTS else "failed"
            retry_after = self.wall() + 30 * 2**max(0, held[0]-1) if status == "retry" else None
            return self.db.execute("""UPDATE pages SET status=?,error=?,updated_at=?,retry_after=?,worker=NULL,
                lease_until=NULL WHERE id=? AND status='running' AND worker=?""",
                (status,reason, datetime.now(UTC).isoformat(),retry_after,ident,worker)).rowcount > 0

    def sweep(self, *, age=LEASE_SECONDS):
        """Remove the staging directories and scratch files that workers killed mid-write left, once
        untouched for `age` seconds; a worker writing one touches it within seconds."""
        cutoff = time.time() - age
        staging = self.root / ".staging"
        for path in [*(staging.iterdir() if staging.is_dir() else ()), *self.root.glob("*.tmp")]:
            with suppress(FileNotFoundError):
                if path.stat().st_mtime >= cutoff:
                    continue
                if path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    path.unlink()

    def status(self, *, state="idle", error=None):
        counts = dict(self.db.execute("SELECT status,count(*) FROM pages GROUP BY status"))
        result = {"policy": POLICY, "state": state, "worker_error":error, "counts": counts,
                  "character_crops": self.db.execute("SELECT coalesce(sum(accepted),0) FROM pages").fetchone()[0],
                  "published_pages": self.db.execute("SELECT count(*) FROM pages WHERE published_at IS NOT NULL").fetchone()[0],
                  "published_crops": self.db.execute("SELECT coalesce(sum(accepted),0) FROM pages WHERE published_at IS NOT NULL").fetchone()[0],
                  "publication_failures": self.db.execute("SELECT count(*) FROM pages WHERE publish_error IS NOT NULL").fetchone()[0],
                  "books_with_crops": self.db.execute("SELECT count(DISTINCT document_id) FROM pages WHERE accepted>0").fetchone()[0],
                  # Supplements of the current policy by status; crops added and published count every
                  # policy's, since those crops are on the site.
                  "supplements": {**dict(self.db.execute(
                                      "SELECT status,count(*) FROM supplements WHERE policy=? GROUP BY status", (POLICY,))),
                                  "added": self.db.execute("SELECT coalesce(sum(added),0) FROM supplements").fetchone()[0],
                                  "published": self.db.execute(
                                      "SELECT coalesce(sum(added),0) FROM supplements WHERE published_at IS NOT NULL").fetchone()[0],
                                  "publication_failures": self.db.execute(
                                      "SELECT count(*) FROM supplements WHERE publish_error IS NOT NULL").fetchone()[0]},
                  # `state` is the last writer's; this lists every worker holding a live lease.
                  "workers": dict(self.db.execute("""SELECT worker,count(*) FROM (
                      SELECT worker FROM pages WHERE status='running' AND lease_until>=:now UNION ALL
                      SELECT worker FROM supplements WHERE status='running' AND lease_until>=:now)
                      GROUP BY worker""", {"now": self.wall()})),
                  "updated_at": datetime.now(UTC).isoformat(),
                  "recent": [dict(r) for r in self.db.execute("""SELECT id,title,status,accepted,examined,output,error,published_at,publish_error,retry_after
                      FROM pages WHERE status!='pending' ORDER BY updated_at DESC LIMIT 12""")]}
        atomic_json(self.root / "status.json", result)
        return result


def shape_reason(unit, size):
    """Why a unit cannot be one character's crop on a page of `size`, or None."""
    expected = unicodedata.normalize("NFC", unit.text_source or "")
    if unit.kind != UnitKind.CHAR or unit.granularity != "char" or len(expected) != 1:
        return "not-one-character"
    b = unit.box
    if b is None or min(b.w, b.h) < 8 or b.x < 0 or b.y < 0 or b.x+b.w > size[0] or b.y+b.h > size[1]:
        return "invalid-geometry"
    if not .25 <= b.w / b.h <= 2.2:
        return "elongated-crop"
    return None


def vote_reason(votes, expected, name, threshold):
    """Why the vote of engine `name` does not confirm `expected`, or None."""
    vote = next((v for v in votes if v["engine"] == name), None)
    if (not vote or vote.get("identity_scope", "character") != "character"
            or unicodedata.normalize("NFC", vote.get("text") or "").strip() != expected):
        return "visual-disagreement"
    score = vote.get("score")
    if (not isinstance(score, (float, int)) or isinstance(score, bool)
            or not math.isfinite(score) or not 0 <= score <= 1 or score < threshold):
        return "visual-uncertain"
    return None


def quality_reason(unit, votes, size):
    """Conservative publication gate. Both independent visual models must agree."""
    if unit.review != ReviewState.MACHINE:
        return "alignment-uncertain"
    reason = shape_reason(unit, size)
    if reason:
        return reason
    expected = unicodedata.normalize("NFC", unit.text_source)
    for name, threshold in (("Atlas classifier", .80), ("NDLkotenOCR", .10)):
        reason = vote_reason(votes, expected, name, threshold)
        if reason:
            return reason
    return None


def letter_out_of_vocabulary(char, candidates, classes) -> bool:
    """Whether `char` is a letter (category Lo) with no class under itself or any of `candidates`."""
    char = unicodedata.normalize("NFC", char)
    if len(char) != 1 or unicodedata.category(char) != "Lo":
        return False
    return {f"U+{ord(char):04X}", *candidates}.isdisjoint(classes)


def out_of_vocabulary(unit, classes) -> bool:
    """Whether the unit is a letter the classifier has no class for, under any of its readings.

    Alignment scores a token through its candidate code points, so a katakana unit read through its
    hiragana class is in vocabulary. Marks, punctuation and symbols (not category Lo) never are
    out of vocabulary here: many are editorial notation with no ink of their own.
    """
    return letter_out_of_vocabulary(unit.text_source or "", (c.unicode for c in unit.candidates), classes)


def kana(text) -> bool:
    return len(text) == 1 and unicodedata.name(text, "").startswith(("HIRAGANA", "KATAKANA", "HENTAIGANA"))


def unconfirmed_reason(unit, votes, size, *, alphabet, neighbours=()):
    """Publication gate for a character the classifier has no class for.

    Alignment gives such a token whichever detection is left between its neighbours, and when the
    detector missed the character that can be a ruby kana or part of the next character. The
    classifier vetoes the box when it reads, at .80 or more, a kana or the text of a neighbouring
    unit; it cannot veto every confident reading, since it reads 髙 as 高. NDLkotenOCR vetoes it when
    it reads two or more characters there at .90 or more (tall compounds such as 孼 read as two, but
    less surely). The shape checks of any unit apply, and NDLkotenOCR must read the character when
    its alphabet holds it, at .70 rather than the .10 the consensus gate asks: there the classifier
    has already read the character, and NDLkotenOCR only has to agree; here its reading is the only
    evidence of which character the box holds.
    """
    reason = shape_reason(unit, size)
    if reason:
        return reason
    sequence = next((v for v in votes if v["engine"] == "NDLkotenOCR"), None)
    if sequence and len(sequence.get("text") or "") > 1 and not vote_reason(
            [sequence], sequence["text"], "NDLkotenOCR", .90):
        return "not-one-character"
    vote = next((v for v in votes if v["engine"] == "Atlas classifier"), None)
    if vote and not vote_reason([vote], vote.get("text") or "", "Atlas classifier", .80):
        read = unicodedata.normalize("NFC", vote.get("text") or "")
        if kana(read) or read in neighbours:
            return "visual-disagreement"
    expected = unicodedata.normalize("NFC", unit.text_source)
    if expected in alphabet:
        return vote_reason(votes, expected, "NDLkotenOCR", .70)
    return None


def overlaps(a, b):
    area = max(0, min(a.x+a.w,b.x+b.w)-max(a.x,b.x))*max(0,min(a.y+a.h,b.y+b.h)-max(a.y,b.y))
    return area / min(a.w*a.h,b.w*b.h) >= .5


def unique_units(units):
    """Reject all overlapping candidates, including conflicts from adjacent line boxes."""
    bad = set()
    for i, a in enumerate(units):
        for j in range(i):
            if overlaps(a.box, units[j].box):
                bad.update((i,j))
    return [u for i,u in enumerate(units) if i not in bad], len(bad)


def source_dimensions(page, *, info_loader=None):
    """Line rectangles are in the original image coordinate space, never inferred from a cache."""
    if page.width > 0 and page.height > 0:
        dimensions = (page.width, page.height)
    else:
        if "?IIIF=" in page.image:
            import tempfile

            from . import net
            base = page.image.rsplit("/", 4)[0]
            with tempfile.TemporaryDirectory(prefix="atlas-source-info-") as scratch:
                path = Path(scratch)/"info.json"
                net.download(base+"/info.json",path,expected="json")
                info = json.loads(path.read_text())
            dimensions = (int(info["width"]),int(info["height"]))
        elif images.service_of(page.image) is None:
            raise ValueError("original page dimensions unavailable; extraction withheld")
        else:
            info = (info_loader or images.info)(page.image)
            dimensions = (int(info["width"]), int(info["height"]))
    if min(dimensions) <= 0 or dimensions[0]*dimensions[1] > 40_000_000:
        raise ValueError("original page exceeds 40 megapixel resource cap")
    return dimensions


def check_coordinate_space(original, cached):
    if tuple(original) != tuple(cached):
        raise ValueError("cached image dimensions differ from source coordinates; extraction withheld")


def engine_models(detector, reader):
    """What names the models of an extraction: the detector's checksum, and each recognizer's model
    with the provider it ran on, since providers differ in the last digits of a score."""
    return {"detector": hashlib.sha256(Path(detector).read_bytes()).hexdigest(),
            "recognizers": reader.engines,
            "classifier_classes": digest(reader.classifier.classes),
            "sequence_alphabet": digest(reader.alphabet)}


def page_identity(models, run, page, document, lines):
    """The digest that names a page's extraction output: policy, models, run and the page's inputs."""
    return digest({"policy":POLICY,"models":models,"run":run.model_dump(),
                   "page":page.model_dump(),"document":document.model_dump(),
                   "lines":[l.model_dump() for l in lines]})


class Engine:
    def __init__(self, *, ndl_cpu: bool = False, ndl_threads: int = 2):
        import onnxruntime as ort

        from .detect import Detector
        from .review.suggestions import Recognizer
        self.run = align.load_run(Path("models/align/runs/collection-v2.yaml"))
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        options.log_severity_level = 3
        if "CUDAExecutionProvider" not in ort.get_available_providers():
            raise RuntimeError("CUDA provider required for this bounded worker")
        ort.preload_dlls()
        session = ort.InferenceSession(self.run.detector, sess_options=options, providers=[
            ("CUDAExecutionProvider", {"gpu_mem_limit":1024*1024*1024,
                                      "arena_extend_strategy":"kSameAsRequested"}), "CPUExecutionProvider"])
        if session.get_providers()[0] != "CUDAExecutionProvider":
            raise RuntimeError("detector CUDA initialization failed")
        self.detector = Detector(self.run.detector, score=self.run.score, nms=self.run.nms, session=session)
        # The classifier, shared with alignment, runs on CUDA; NDL's sequence model may run on the CPU.
        self.reader = Recognizer(sequence_on_cpu=ndl_cpu, sequence_threads=ndl_threads)
        if self.reader.sequence is None or self.reader.classifier is None:
            raise RuntimeError("both sequence and single-character models are required")
        wanted = {"NDLkotenOCR": "CPUExecutionProvider" if ndl_cpu else "CUDAExecutionProvider"}
        if any(e["provider"] != wanted.get(e["name"], "CUDAExecutionProvider") for e in self.reader.engines):
            raise RuntimeError("recognizer initialization did not reach the requested providers")
        self.classifier = self.reader.classifier
        self.ndl_provider = next(e["provider"] for e in self.reader.engines if e["name"] == "NDLkotenOCR")
        self.models = engine_models(Path(self.run.detector), self.reader)

    def inputs(self, job, *, max_lines=64):
        """What one page's extraction reads, with the `identity` that names its output; no model runs."""
        from PIL import Image
        dataset = tables.Dataset(Path(job["source"]))
        # One row each, filtered by Arrow: building all 79,000 pages to keep one cost 2 s a page.
        page = next(p for batch in dataset.scan("pages", keep=tables.In("id", {job["id"]})) for p in batch)
        document = next(d for batch in dataset.scan("documents", keep=tables.In("id", {page.document_id}))
                        for d in batch)
        lines = [line for batch in dataset.scan("lines", keep=tables.In("page_id", {page.id}))
                 for line in batch if line.box is not None and not line.page_scope]
        lines = sorted(lines, key=lambda x:(x.seq,x.id))
        if len(lines) > max_lines:
            raise ValueError("page exceeds transcription-line resource cap")
        if not lines:
            raise ValueError("page has no located transcription lines")
        original_dimensions = source_dimensions(page)
        path = images.path_for(page.image)
        if path is None:
            images.fetch(page.image)
            path = images.path_for(page.image)
        if path is None:
            raise ValueError("page image unavailable")
        with Image.open(path) as handle:
            check_coordinate_space(original_dimensions, handle.size)
            image = handle.convert("RGB")
        page = page.model_copy(update={"width":image.width,"height":image.height,
                                       "sha256":hashlib.sha256(path.read_bytes()).hexdigest(),
                                       "meta":{**page.meta,"extraction_source_dimensions":list(original_dimensions)}})
        located_line_count = len(lines)
        lines = [line for line in lines if line.box.x >= 0 and line.box.y >= 0
                 and line.box.x+line.box.w <= image.width and line.box.y+line.box.h <= image.height]
        identity = page_identity(self.models, self.run, page, document, lines)
        return {"page": page, "document": document, "lines": lines, "image": image, "identity": identity,
                "located_line_count": located_line_count, "original_dimensions": original_dimensions}

    def extract(self, job, root, *, max_lines=64):
        """Extract one page and commit it under `pages/`, or return the output already committed."""
        work = self.inputs(job, max_lines=max_lines)
        output = root/"pages"/work["identity"]
        if output.exists():
            return committed_report(output, work["identity"], work["page"]), output
        report, records = self.extract_page(work)
        return report, commit(output, work["identity"], records, report)

    def extract_page(self, work):
        """Run the models over one page's `inputs`: its report and the records to commit."""
        page, document, lines, image = work["page"], work["document"], work["lines"], work["image"]
        identity, located_line_count = work["identity"], work["located_line_count"]
        original_dimensions = work["original_dimensions"]

        def crop_of(page_id, box):
            return image.crop((box.x,box.y,box.x+box.w,box.y+box.h))
        candidates = []
        reasons = Counter()
        examined = 0
        detections = [align.Detection(box=box, score=score) for box,score in self.detector.boxes(image)]
        classes = set(self.classifier.classes)
        for line in lines:
            found, _ = align.align_line(line, detections, run=self.run, classifier=self.classifier,
                                        crop_of=crop_of)
            for position, unit in enumerate(found):
                examined += 1
                gate = CONSENSUS
                reason = quality_reason(unit, [], image.size)
                if reason in (None, "visual-disagreement"):
                    result = self.reader.read(crop_of(page.id, unit.box))
                    reason = quality_reason(unit, result["votes"], image.size)
                elif (reason == "alignment-uncertain" and out_of_vocabulary(unit, classes)
                        and shape_reason(unit, image.size) is None):
                    result = self.reader.read(crop_of(page.id, unit.box))
                    neighbours = {other.text_source for other in found[max(0, position - 1):position + 2]
                                  if other is not unit and other.text_source}
                    reason = unconfirmed_reason(unit, result["votes"], image.size, alphabet=self.reader.alphabet,
                                                neighbours=neighbours)
                    gate = UNCONFIRMED
                if reason:
                    reasons[reason] += 1
                    continue
                label = unicodedata.normalize("NFC", unit.text_source)
                unit = unit.model_copy(update={
                    "id":"ex:"+identity[:16]+":"+digest(unit.id)[:20],
                    "document_id":document.id,"unicode":f"U+{ord(label):04X}",
                    "classification":Classification.IDENTIFIED,"group_id":None,
                    "review":ReviewState.MACHINE,
                    "meta":{"extraction":{"policy":POLICY,"gate":gate,"source_unit_id":unit.id,
                        "source_page_id":page.id,"source_line_id":line.id,"page_sha256":page.sha256,
                        "generation":identity,"visual_votes":result["votes"],
                        "verified":False,"quiz":True}},
                    "upstream":{**unit.upstream,"source":line.meta.get("source", "honkoku-lines"),
                                "extraction_policy":POLICY}})
                candidates.append(unit)
        align.clear_crop_cache()
        accepted, duplicates = unique_units(candidates)
        reasons["overlapping-crops"] += duplicates
        report = {"policy":POLICY,"generation":identity,"page_id":page.id,"document_id":document.id,
                  "title":document.title,"page_sha256":page.sha256,"models":self.models,
                  "examined":examined,"accepted":len(accepted),"withheld":dict(reasons),
                  "unconfirmed":sum(1 for u in accepted if u.meta["extraction"]["gate"] == UNCONFIRMED),
                  "source_dimensions":list(original_dimensions),"lines":len(lines),"detected":len(detections),"complete_page":len(lines)==located_line_count,
                  "withheld_lines":{"invalid_geometry":located_line_count-len(lines)},
                  "created_at":datetime.now(UTC).isoformat()}
        return report, {"documents":[document],"pages":[page],"lines":lines,"units":accepted}


def supplement(engine, job, root, *, max_lines=64):
    """Extract a page completed under an earlier policy again and keep what that policy could not add.

    The kept units, of either gate, are those that overlap no crop of the page's earlier output or of
    a supplement an earlier policy made for it (`overlaps`), since those are already published and a
    crop there may already be reviewed. They are committed with the page's document, page and line
    rows under `supplements/`; the page's full extraction under this policy is not kept. A supplement
    committed before its worker stopped is found by its identity and returned as it is.
    """
    work = engine.inputs(job, max_lines=max_lines)
    earlier = sorted(job.get("earlier_supplements", ()))
    identity = digest({"supplement": work["identity"], "earlier": job["output"], "earlier_supplements": earlier})
    output = root/"supplements"/identity
    if output.exists():
        report = json.loads((output/"report.json").read_text())
        if report.get("generation") != identity or report.get("page_id") != job["id"]:
            raise ValueError("committed supplement identity mismatch")
        return report, output
    extraction, records = engine.extract_page(work)
    published = [unit.box for committed in (job["output"], *earlier)
                 for unit in tables.Dataset(root/committed).read("units") if unit.box]
    added = [unit for unit in records["units"] if not any(overlaps(unit.box, box) for box in published)]
    result = {"policy": POLICY, "generation": identity, "page_id": job["id"], "extraction": extraction["generation"],
              "earlier_output": job["output"], "added": len(added), "created_at": datetime.now(UTC).isoformat()}
    return result, commit(output, identity, {**records, "units": added}, result)


def commit(output, identity, records, report):
    """Write an immutable dataset and its report to `output` through a staging directory."""
    if output.exists():
        return output
    root = output.parent.parent
    # A staging directory of this call's own: two workers committing one identity never share one.
    stage = root/".staging"/f"{identity}.{uuid4().hex}"
    stage.mkdir(parents=True)
    try:
        for name, rows in records.items():
            tables.write(stage/f"{name}.parquet", rows, tables.TABLES[name])
        issues = tables.Dataset(stage).validate()
        if issues:
            raise ValueError("invalid extracted dataset: "+"; ".join(issues[:3]))
        atomic_json(stage/"report.json",report)
        output.parent.mkdir(parents=True,exist_ok=True)
        for file in stage.glob("*.parquet"):
            with file.open("rb") as handle:
                os.fsync(handle.fileno())
        try:
            stage.replace(output)
        except OSError:
            if not output.exists():
                raise
            # Another worker committed the same identity first; its output is the same.
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    descriptor = os.open(output.parent, os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return output


def committed_report(output, identity, page):
    report = json.loads((output/"report.json").read_text())
    if (report.get("generation") != identity or report.get("page_id") != page.id
            or report.get("page_sha256") != page.sha256):
        raise ValueError("committed extraction identity mismatch")
    dataset = tables.Dataset(output)
    if dataset.validate() or len(dataset.read("units")) != report.get("accepted"):
        raise ValueError("committed extraction tables mismatch")
    return report


def require_storage(root, *, minimum_gib=5):
    for path in {Path(root),images.images_root()}:
        if shutil.disk_usage(path).free < minimum_gib*2**30:
            raise OSError("extraction paused: less than 5 GiB free on data or image-cache volume")


def publish_completed(queue, store):
    """Retry insert-only imports after a crash, without repeating OCR or changing reviews.

    A page is tried at most `MAX_ATTEMPTS` times, and once only when the store refuses it outright,
    so a page that can never be imported does not have its crops rendered again on every pass.
    """
    from .review.media import prepare_dataset
    from .review.store import BadRequest, Conflict

    published = 0
    rows = list(queue.db.execute("""SELECT id,output FROM pages WHERE status='complete'
        AND published_at IS NULL AND publish_attempts<?""", (MAX_ATTEMPTS,)))
    for row in rows:
        try:
            prepare_dataset(queue.root / row["output"])
            store.import_dataset(queue.root / row["output"])
        except (ValueError, RuntimeError, OSError, sqlite3.Error) as exc:
            attempts = MAX_ATTEMPTS if isinstance(exc, BadRequest | Conflict) else None
            with queue.db:
                queue.db.execute("""UPDATE pages SET publish_error=?,
                    publish_attempts=COALESCE(?, publish_attempts + 1) WHERE id=?""",
                                 (type(exc).__name__+": "+str(exc).replace(str(Path.home()),"~")[:500],
                                  attempts, row["id"]))
            continue
        with queue.db:
            queue.db.execute("UPDATE pages SET published_at=?,publish_error=NULL WHERE id=?",
                             (datetime.now(UTC).isoformat(),row["id"]))
        published += 1
    return published


def publish_supplements(queue, store):
    """Import completed supplements, as `publish_completed` imports pages."""
    from .review.media import prepare_dataset
    from .review.store import BadRequest, Conflict

    published = 0
    rows = list(queue.db.execute("""SELECT page_id,policy,output FROM supplements WHERE status='complete'
        AND published_at IS NULL AND publish_attempts<?""", (MAX_ATTEMPTS,)))
    for row in rows:
        try:
            prepare_dataset(queue.root / row["output"])
            store.import_dataset(queue.root / row["output"])
        except (ValueError, RuntimeError, OSError, sqlite3.Error) as exc:
            attempts = MAX_ATTEMPTS if isinstance(exc, BadRequest | Conflict) else None
            with queue.db:
                queue.db.execute("""UPDATE supplements SET publish_error=?,
                    publish_attempts=COALESCE(?, publish_attempts + 1) WHERE page_id=? AND policy=?""",
                                 (type(exc).__name__+": "+str(exc).replace(str(Path.home()),"~")[:500],
                                  attempts, row["page_id"], row["policy"]))
            continue
        with queue.db:
            queue.db.execute("""UPDATE supplements SET published_at=?,publish_error=NULL
                WHERE page_id=? AND policy=?""", (datetime.now(UTC).isoformat(), row["page_id"], row["policy"]))
        published += 1
    return published


class heartbeat:
    """Renew a claim's lease in the background while the worker extracts it.

    A renewal the database refuses for a moment (busy, locked) is tried again at the next beat, well
    inside the lease. Once another worker holds the claim, `lost` is set and the beats stop: the
    worker then writes nothing for it. A page still running `deadline` seconds after its claim
    (`PAGE_DEADLINE_LEASES` leases by default) is given up the same way, so a hung extraction lets its
    lease lapse and another worker takes the page.
    """

    def __init__(self, queue, kind, ident, worker, *, lease=LEASE_SECONDS, deadline=None):
        import threading
        self.stop = threading.Event()
        self.lost = threading.Event()
        deadline = lease * PAGE_DEADLINE_LEASES if deadline is None else deadline
        self.thread = threading.Thread(target=self._beat, args=(queue, kind, ident, worker, lease, deadline),
                                       daemon=True)

    def _beat(self, queue, kind, ident, worker, lease, deadline):
        started = time.monotonic()
        while not self.stop.wait(lease / 8):
            if time.monotonic() - started > deadline:
                self.lost.set()
                return
            try:
                held = queue.renew(kind, ident, worker, lease=lease)
            except sqlite3.Error:
                continue
            if not held:
                self.lost.set()
                return

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.stop.set()
        self.thread.join()


def publish(queue, store, *, wait=False):
    """Import completed pages and supplements into the store, one worker at a time.

    Another worker already publishing takes this worker's pages too, so a busy lock is skipped, unless
    `wait` asks to wait for it: a worker's last publish does, or its final pages would wait for the
    next worker's run.
    """
    try:
        with tables.locked(queue.root / "publish", timeout=None if wait else 0):
            publish_completed(queue, store)
            publish_supplements(queue, store)
    except TimeoutError:
        pass


def run(queue, engine, *, pages=3, seconds=600, max_lines=64, store=None, supplement_every=2, worker=None,
        lease=LEASE_SECONDS):
    """Extract up to `pages` pages within `seconds`, as one of any number of workers sharing `queue`.

    Requests to image hosts are paced by `net`, across every worker, so pages follow one another at once.
    """
    import socket
    worker = worker or f"{socket.gethostname()}:{os.getpid()}"
    started = time.monotonic()
    queue.sweep(age=lease)
    if store is not None:
        publish(queue, store)
    queue.status(state="running")
    done = 0
    while done < pages and time.monotonic()-started < seconds:
        try:
            require_storage(queue.root)
            if store is not None:
                require_storage(store.directory)
        except OSError:
            return queue.status(state="paused-low-storage")
        job = (queue.claim_supplement(worker, lease=lease)
               if supplement_every and done % supplement_every == supplement_every - 1 else None)
        kind = "supplement" if job else "page"
        job = job or queue.claim(worker, lease=lease)
        if job is None:
            job, kind = queue.claim_supplement(worker, lease=lease), "supplement"
        if job is None:
            break
        beat = heartbeat(queue, kind, job["id"], worker, lease=lease)
        try:
            with beat:
                if kind == "supplement":
                    report, output = supplement(engine, job, queue.root, max_lines=max_lines)
                else:
                    report, output = engine.extract(job, queue.root, max_lines=max_lines)
            # A claim another worker took over is its to record; the output committed here is kept,
            # named by its identity, and that worker finds it.
            if not beat.lost.is_set():
                if kind == "supplement":
                    queue.finish_supplement(job["id"], worker, report, output)
                else:
                    queue.finish(job["id"], worker, report, output)
        except Exception as exc:  # noqa: BLE001 — persist a failed page and keep the bounded queue moving
            # Avoid leaking local paths from exception text into durable status.
            import httpx

            from . import net
            reason = type(exc).__name__+": "+str(exc).replace(str(Path.home()),"~")[:500]
            if kind == "supplement" and not beat.lost.is_set():
                queue.fail_supplement(job["id"], worker, reason)
            elif not beat.lost.is_set():
                queue.fail(job["id"], worker, reason,
                           retryable=isinstance(exc,(net.DownloadError,httpx.HTTPError,OSError)))
        if store is not None:
            publish(queue, store)
        done += 1
        queue.status(state="running")
    if store is not None:
        publish(queue, store, wait=True)
    return queue.status()
