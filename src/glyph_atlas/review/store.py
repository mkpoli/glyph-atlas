"""The review store: an append-only event log over one dataset directory, kept in SQLite.

The store lives beside the tables it reviews, at `<directory>/review.sqlite`. It holds the review
events, the current state of the lines and units those events produced, and the revision of every
target. The event log is the source of truth: a review is appended before the state it implies is
written, so a crash between the two leaves an event whose effect the state does not show yet.

`apply` writes the state back to `lines.parquet` and `units.parquet` and the events to
`reviews.jsonl`. `replay` rebuilds the state from the tables and the log, compares it with what the
store holds and repairs the difference. Rebuilding leaves an event alone when its effect is already
present, so replaying a log over tables that `apply` wrote changes nothing.

An event carries one editorial decision: a field of a line or a unit, a whole unit drawn by a
reviewer, or a segmentation. Segmentation is one event with `field = "segmentation"` and
`new = {"split": [{"box": …, "reading": …}, …]}` or `new = {"merge": [ids]}`; the inputs are retired
with `active` false and `split_into` or `merged_into` set, and the outputs take the reviewer ids
`{line_id}:m{n}`.

A client that sends `base_revision` older than the target's current revision gets `Conflict`; a
client that repeats an `idempotency_key` gets the earlier result instead of a second event.
"""

from __future__ import annotations

import json
import random
import re
import sqlite3
import threading
from collections import Counter
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from dataclasses import field as dc_field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .. import tables
from ..schema import (
    Box,
    Classification,
    Document,
    Line,
    LineRole,
    Page,
    Review,
    ReviewState,
    Script,
    Unit,
    UnitKind,
)

STORE_NAME = "review.sqlite"
LOG_NAME = "reviews.jsonl"
SCHEMA_VERSION = 1
#: The field that carries a split or a merge.
SEGMENTATION = "segmentation"
#: The field that carries a record a reviewer drew.
CREATE = "create"
#: Events that are recorded and change no state: how long a line was open, and free notes.
STATELESS = frozenset({"timing", "note"})
#: The keys a split entry may carry. The identity and lifecycle fields belong to the server.
SPLIT_KEYS = frozenset(
    {
        "box",
        "reading",
        "text_source",
        "unicode",
        "kind",
        "granularity",
        "script",
        "classification",
        "voicing",
        "variants",
        "candidates",
        "confidence",
        "crop",
        "crop_sha256",
        "group_id",
        "antecedent_ids",
    }
)
MANUAL = "manual"

_EVENT_COLUMNS = (
    "id",
    "target_type",
    "target_id",
    "field",
    "old",
    "new",
    "role",
    "actor",
    "evidence",
    "at",
    "client_id",
    "idempotency_key",
    "result",
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    field TEXT NOT NULL,
    old TEXT,
    new TEXT,
    role TEXT NOT NULL,
    actor TEXT,
    evidence TEXT,
    at TEXT NOT NULL,
    client_id TEXT NOT NULL DEFAULT '',
    idempotency_key TEXT,
    result TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS events_idempotency ON events (client_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS events_target ON events (target_id);
CREATE TABLE IF NOT EXISTS revisions (target_id TEXT PRIMARY KEY, revision INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS lines (
    id TEXT PRIMARY KEY,
    document_id TEXT,
    page_id TEXT,
    seq INTEGER,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS lines_page ON lines (page_id);
CREATE TABLE IF NOT EXISTS units (
    id TEXT PRIMARY KEY,
    document_id TEXT,
    page_id TEXT,
    line_id TEXT,
    seq INTEGER,
    active INTEGER NOT NULL,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS units_line ON units (line_id);
CREATE INDEX IF NOT EXISTS units_page ON units (page_id);
"""


class StoreError(RuntimeError):
    """A review the store will not record."""


class NotFound(StoreError):
    """The target of a review is not in the dataset."""


class Conflict(StoreError):
    """The target moved under the client, or a retired record was reviewed.

    `detail` is the body the service answers with: the reason, the current revision and the current
    state of the target.
    """

    def __init__(self, reason: str, **detail: Any) -> None:
        self.reason = reason
        self.detail: dict[str, Any] = {"error": reason, **detail}
        super().__init__(f"{reason}: {detail.get('target_id', '')}".strip(": "))


class StaleTables(StoreError):
    """The store holds events but the tables under it were rewritten by another writer.

    Serving the store's copy would hide whatever the other writer did, and reloading would throw the
    events away, so the store refuses and names the way out: export the events to `reviews.jsonl`,
    delete the database, and let the next open build the state from the tables plus the log.
    """


class BadRequest(StoreError):
    """A review whose field or value does not fit the target."""


class ReviewRequest(BaseModel):
    """One decision a client asks the server to record. The server assigns the rest."""

    model_config = ConfigDict(extra="forbid")

    target_type: Literal["unit", "line", "page", "document", "group"] = "unit"
    target_id: str
    field: str
    new: Any = None
    base_revision: int | None = Field(default=None, description="the revision the client edited from")
    client_id: str | None = Field(default=None, description="who is reviewing; also the actor of the event")
    idempotency_key: str | None = Field(default=None, description="repeating it answers the earlier result")
    evidence: str | None = None


class CorrectionRequest(BaseModel):
    """One editorial correction a client asks the server to record.

    `base_revision` is the page revision the client edited from, so a tab that has been open since
    before somebody else's correction is told rather than silently overwriting.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="stable id of the correction, as the publishing project uses one")
    target_id: str = Field(description="the page the corrected text is on")
    #: One-based transcription line, as the source's parser counts them. Left untyped on purpose:
    #: a line that is not a positive integer is a placement problem, and `review.corrections` owns
    #: that rule, so the answer is its message rather than FastAPI's schema error.
    line: Any = Field(description="one-based transcription line, as the source's parser counts them")
    original: str = Field(description="the text as published, which must appear exactly once")
    corrected: str = Field(description="what it should say")
    note: str = Field(description="why, in the reviewer's words; the source's schema requires one")
    kind: str = "transcription"
    ruby_field: Literal["rb", "rt", "left"] | None = None
    ruby_base: str | None = None
    entry: dict[str, Any] | None = None
    source_text_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    base_revision: int | None = None


class SourceUpdateRequest(BaseModel):
    """The pages whose corrections a reviewer wants validated for the publishing project."""

    model_config = ConfigDict(extra="forbid")

    page_ids: list[str] = Field(min_length=1, max_length=100)


class RetractRequest(BaseModel):
    """A correction taken back, with the page it was recorded on.

    `base_revision` is the page revision the client was looking at, so a tab that has been open since
    before another reviewer's correction cannot withdraw work it never saw.
    """

    model_config = ConfigDict(extra="forbid")

    page_id: str
    reason: str = ""
    base_revision: int | None = None


class LineRequest(BaseModel):
    """A line the detector missed, drawn by a reviewer."""

    model_config = ConfigDict(extra="forbid")

    page_id: str
    box: Box
    seq: int | None = None
    vertical: bool = True
    role: LineRole = LineRole.MAIN
    text_raw: str = ""
    text: str = ""
    match_method: str | None = None
    match_confidence: float | None = None
    meta: dict[str, Any] = Field(default_factory=dict)
    client_id: str | None = None
    idempotency_key: str | None = None


class UnitRequest(BaseModel):
    """A unit drawn on an existing line."""

    model_config = ConfigDict(extra="forbid")

    line_id: str
    box: Box
    seq: int | None = None
    reading: str | None = None
    text_source: str | None = None
    unicode: str | None = None
    kind: UnitKind = UnitKind.CHAR
    granularity: Literal["char", "sequence", "block"] = "char"
    classification: Classification = Classification.UNASSESSED
    script: Script = Script.UNKNOWN
    voicing: Literal["none", "dakuten", "handakuten"] | None = None
    client_id: str | None = None
    idempotency_key: str | None = None


class State:
    """The lines and units a change may touch, keyed by id."""

    def __init__(self, lines: Iterable[Line] = (), units: Iterable[Unit] = ()) -> None:
        self.lines: dict[str, Line] = {line.id: line for line in lines}
        self.units: dict[str, Unit] = {unit.id: unit for unit in units}

    def get(self, target_id: str) -> Line | Unit | None:
        return self.units.get(target_id) or self.lines.get(target_id)

    def put(self, record: Line | Unit) -> None:
        if isinstance(record, Unit):
            self.units[record.id] = record
        else:
            self.lines[record.id] = record


@dataclass
class Change:
    """The state one event produces, written into the `State` it was computed from."""

    event: Review
    updated: list[Line | Unit] = dc_field(default_factory=list)
    created: list[Line | Unit] = dc_field(default_factory=list)
    retired: list[Unit] = dc_field(default_factory=list)
    warnings: list[str] = dc_field(default_factory=list)
    skipped: bool = False

    def records(self) -> list[Line | Unit]:
        return [*self.updated, *self.created, *self.retired]


class Store:
    """The review state of one dataset directory.

    The store is created from the dataset tables on first use: `documents` names the dataset, and
    `lines` and `units` are copied into SQLite. Every later open reads the store, not the tables, so
    `apply` can write a reviewed state back to the tables without the store losing its own copy of
    the events. `replay` rebuilds the state from the tables plus the log.
    """

    def __init__(self, directory: Path, *, rebuilding: bool = False, exporting: bool = False) -> None:
        # A rebuild reads the tables and replays the events over them, so it does its own reload and
        # keeps the events it holds; an export has to reach the events that a changed table would
        # otherwise hide, because writing them to the log is the way out of that state. Every other
        # open guards against a table another writer changed.
        self.rebuilding = rebuilding
        self.exporting = exporting
        self.directory = Path(directory)
        self.path = self.directory / STORE_NAME
        self.dataset = tables.Dataset(self.directory)
        self._lock = threading.RLock()
        self._pages: dict[str, Page] | None = None
        self._documents: list[Document] | None = None
        self._page_texts: dict[str, str] | None = None
        self._page_texts_stamp: tuple = ()
        with self._lock, self._connection() as conn:
            self._schema(conn)
            stamp = self._source_stamp()
            if self._meta(conn, "loaded") is None:
                self._load(conn, stamp)
            elif not (rebuilding or exporting) and self._meta(conn, "source_stamp") != stamp:
                # The tables changed under the store, which happens when an alignment writes new
                # units into a directory that has been reviewed before. Review events are kept and
                # replayed over the new tables, so no decision is lost; what the store refuses is a
                # store holding events the log has never seen, because rebuilding would be the only
                # way to serve the new tables and that would throw those events away.
                pending = self._unexported_events(conn)
                if pending:
                    raise StaleTables(
                        f"{self.directory}: the tables changed under a store holding {pending} review "
                        f"events that are not in {LOG_NAME}; run `atlas review apply` to write them, "
                        f"then delete {self.path.name} and reopen"
                    )
                conn.execute("DELETE FROM lines")
                conn.execute("DELETE FROM units")
                conn.execute("DELETE FROM revisions")
                self._load(conn, stamp)
                self._replay_events(conn)

    # -- reading the dataset ---------------------------------------------------------------------

    def pages(self) -> dict[str, Page]:
        """Every page by id, read from the tables once."""
        if self._pages is None:
            path = self.dataset.tables["pages"]
            self._pages = {page.id: page for page in tables.read(path, Page)} if path else {}
        return self._pages

    def documents(self) -> list[Document]:
        """Every document, in table order."""
        if self._documents is None:
            path = self.dataset.tables["documents"]
            self._documents = list(tables.read(path, Document)) if path else []
        return self._documents

    def document(self, document_id: str) -> Document | None:
        return next((document for document in self.documents() if document.id == document_id), None)

    def page(self, page_id: str) -> Page | None:
        return self.pages().get(page_id)

    def iter_units(self) -> Iterable[Unit]:
        """Every unit in the store, active or retired, in table order.

        A dashboard counts what is outstanding, and a retired unit is not outstanding work, so the
        caller gets both and decides; the standing derivation is what tells them apart.
        """
        with self._lock, self._connection() as conn:
            for row in conn.execute("SELECT data FROM units ORDER BY page_id, line_id, seq, id"):
                yield Unit.model_validate_json(row["data"])

    def exported_ids(self) -> set[str]:
        """The units whose present state was written back into the tables by an apply.

        `apply` marks what it writes, and a unit carrying that mark arrived reviewed rather than
        machine-produced. A dashboard needs the distinction: an applied review is a decision the
        record holds, and counting it as untouched machine output would understate the work done.
        """
        from ..schema import ReviewState

        written: set[str] = set()
        with self._lock, self._connection() as conn:
            for row in conn.execute("SELECT id, data FROM units"):
                unit = Unit.model_validate_json(row["data"])
                if unit.review in (ReviewState.MACHINE, ReviewState.REJECTED):
                    continue
                written.add(str(row["id"]))
        return written

    def page_text(self, page_id: str) -> str | None:
        """A page's transcription as the import wrote it, or None when the dataset holds none.

        The transcription is not part of the review projection and no event changes it: a correction
        is a layer that names what it replaces (see `review.corrections`), so this reads the table and
        the text stays exactly what was imported.
        """
        from ..schema import PageText

        path = self.dataset.tables.get("page_texts")
        with self._lock:
            files = sorted(path.glob("*.parquet")) if path and path.is_dir() else [path] if path else []
            stamp = tuple((file.name, file.stat().st_ino, file.stat().st_mtime_ns, file.stat().st_size)
                          for file in files)
            if self._page_texts is None or stamp != self._page_texts_stamp:
                self._page_texts = (
                    {row.page_id: row.text_raw for row in tables.read(path, PageText)} if path else {}
                )
                self._page_texts_stamp = stamp
            return self._page_texts.get(page_id)

    def line(self, line_id: str) -> Line | None:
        with self._lock, self._connection() as conn:
            return self._line_row(conn, line_id)

    def unit(self, unit_id: str) -> Unit | None:
        with self._lock, self._connection() as conn:
            return self._unit_row(conn, unit_id)

    def lines_of_page(self, page_id: str) -> list[Line]:
        with self._lock, self._connection() as conn:
            return self._lines_of_page(conn, page_id)

    def units_of_line(self, line_id: str, *, active: bool = True) -> list[Unit]:
        with self._lock, self._connection() as conn:
            return self._units_of_line(conn, line_id, active=active)

    def line_summaries(self, lines: Iterable[Line]) -> list[dict[str, Any]]:
        """Each line with the number of its active units and its own revision."""
        records = list(lines)
        if not records:
            return []
        marks = ",".join("?" * len(records))
        with self._lock, self._connection() as conn:
            counts = {
                row["line_id"]: row["n"]
                for row in conn.execute(
                    f"SELECT line_id, count(*) AS n FROM units WHERE active = 1 "
                    f"AND line_id IN ({marks}) GROUP BY line_id",
                    [record.id for record in records],
                )
            }
            revisions = self._revision_map(conn)
        return [
            {
                "line": line,
                "units": counts.get(line.id, 0),
                "revision": revisions.get(line.id, 0),
            }
            for line in records
        ]

    def events(self) -> list[Review]:
        """The event log, oldest first."""
        with self._lock, self._connection() as conn:
            return self._events(conn)

    def revisions(self, target_ids: Iterable[str]) -> dict[str, int]:
        """The revision of every target named; a target without events has revision 0."""
        wanted = list(dict.fromkeys(target_ids))
        if not wanted:
            return {}
        with self._lock, self._connection() as conn:
            return {target_id: self._revision(conn, target_id) for target_id in wanted}

    def revision(self, target_id: str) -> int:
        return self.revisions([target_id]).get(target_id, 0)

    def unit_snapshot(self, unit_id: str | None = None) -> list[tuple[Unit, int]]:
        """Read each unit and its revision from the same database snapshot."""
        with self._lock, self._connection() as conn:
            rows = conn.execute(
                "SELECT units.data, coalesce(revisions.revision, 0) AS revision FROM units "
                "LEFT JOIN revisions ON revisions.target_id = units.id "
                "WHERE (? IS NULL OR units.id = ?) ORDER BY units.id", (unit_id, unit_id),
            )
            return [(Unit.model_validate_json(row["data"]), row["revision"]) for row in rows]

    def counts(self) -> dict[str, dict[str, int]]:
        """Per document: pages, lines, active units and active units a reviewer has touched."""
        lines, units, reviewed = self._group_counts()
        pages = Counter(page.document_id for page in self.pages().values())
        return {
            document.id: {
                "pages": pages.get(document.id, 0),
                "lines": lines.get(document.id, 0),
                "units": units.get(document.id, 0),
                "reviewed": reviewed.get(document.id, 0),
            }
            for document in self.documents()
        }

    def page_counts(self, page_id: str) -> dict[str, int]:
        """Lines, active units and reviewed units of one page."""
        with self._lock, self._connection() as conn:
            lines = conn.execute("SELECT count(*) FROM lines WHERE page_id = ?", (page_id,)).fetchone()[0]
            units = conn.execute(
                "SELECT count(*) FROM units WHERE page_id = ? AND active = 1", (page_id,)
            ).fetchone()[0]
            reviewed = conn.execute(
                "SELECT count(*) FROM units JOIN revisions ON revisions.target_id = units.id "
                "WHERE page_id = ? AND active = 1",
                (page_id,),
            ).fetchone()[0]
        return {"lines": lines, "units": units, "reviewed": reviewed}

    def queue(self, strategy: str, *, document: str | None = None, seed: int = 0) -> list[dict[str, Any]]:
        """The lines to review, with the counts a queue view shows.

        `unreviewed` keeps the lines no event has touched, `disagreement` the lines whose units the
        classifier left open or labelled against its own top candidate, and `random` shuffles every
        line with `seed`, so that a page of the queue is reproducible. `document` keeps one document.
        """
        with self._lock, self._connection() as conn:
            rows = conn.execute(
                "SELECT data FROM lines WHERE (? IS NULL OR document_id = ?) "
                "ORDER BY document_id, page_id, seq, id",
                (document, document),
            )
            lines = [Line.model_validate_json(row["data"]) for row in rows]
            revisions = self._revision_map(conn)
            items = []
            for line in lines:
                units = self._units_of_line(conn, line.id)
                touched = [unit for unit in units if revisions.get(unit.id)]
                items.append(
                    {
                        "line": line,
                        "document_id": self._line_document(line),
                        "page_id": line.page_id,
                        "units": len(units),
                        "reviewed": len(touched),
                        "revision": revisions.get(line.id, 0),
                        "disagreements": sum(1 for unit in units if _disagrees(unit)),
                    }
                )
        if strategy == "unreviewed":
            items = [item for item in items if not item["reviewed"] and not item["revision"]]
        elif strategy == "disagreement":
            items = [item for item in items if item["disagreements"]]
            items.sort(key=lambda item: (-item["disagreements"], item["line"].id))
        elif strategy == "random":
            random.Random(seed).shuffle(items)
        else:
            raise BadRequest(f"unknown strategy {strategy!r}; expected unreviewed, disagreement or random")
        return items

    # -- writing ---------------------------------------------------------------------------------

    def record(self, request: ReviewRequest) -> dict[str, Any]:
        """Record one review and return the result a client sees."""
        with self._lock, self._connection() as conn:
            duplicate = self._by_key(conn, request.client_id, request.idempotency_key)
            if duplicate is not None:
                return self._result(conn, duplicate, duplicate=True)
            revision = self._revision(conn, request.target_id)
            if request.base_revision is not None and request.base_revision < revision:
                raise Conflict(
                    "stale-revision",
                    target_type=request.target_type,
                    target_id=request.target_id,
                    base_revision=request.base_revision,
                    revision=revision,
                    state=self._state_dump(conn, request.target_type, request.target_id),
                )
            self._check_target(conn, request)
            event = Review(
                id="",
                target_type=request.target_type,
                target_id=request.target_id,
                field=request.field,
                old=None,
                new=request.new,
                role="reviewer",
                actor=request.client_id,
                evidence=request.evidence,
                at=datetime.now(UTC),
            )
            state = self._state_for(conn, event)
            change = _change(state, event, guard=False)
            return self._commit(conn, change, state, request.client_id, request.idempotency_key)

    def record_batch(self, requests: list[ReviewRequest]) -> list[dict[str, Any]]:
        """Record an entire review round atomically, including its projected state."""
        results = []
        with self._lock, self._connection() as conn, self._transaction(conn):
            for request in requests:
                duplicate = self._by_key(conn, request.client_id, request.idempotency_key)
                if duplicate is not None:
                    if (duplicate["target_id"] != request.target_id
                            or duplicate["field"] != request.field
                            or json.loads(duplicate["new"]) != request.new
                            or duplicate["evidence"] != request.evidence):
                        raise BadRequest("This submission ID already belongs to another answer.")
                    results.append(self._result(conn, duplicate, duplicate=True))
                    continue
                revision = self._revision(conn, request.target_id)
                if request.base_revision is not None and request.base_revision != revision:
                    raise Conflict("stale-revision", target_type=request.target_type,
                                   target_id=request.target_id, base_revision=request.base_revision,
                                   revision=revision,
                                   state=self._state_dump(conn, request.target_type, request.target_id))
                self._check_target(conn, request)
                event = Review(id="", target_type=request.target_type, target_id=request.target_id,
                               field=request.field, new=request.new, role="reviewer",
                               actor=request.client_id, evidence=request.evidence, at=datetime.now(UTC))
                state = self._state_for(conn, event)
                change = _change(state, event, guard=False)
                seq = self._last_seq(conn) + 1
                event = change.event.model_copy(update={"id": f"rv{seq:08d}"})
                change.event = event
                conn.execute(
                    "INSERT INTO events (id, target_type, target_id, field, old, new, role, actor, "
                    "evidence, at, client_id, idempotency_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (event.id, event.target_type, event.target_id, event.field, _json(event.old),
                     _json(event.new), event.role, event.actor, event.evidence, event.at.isoformat(),
                     request.client_id or "", request.idempotency_key),
                )
                self._persist(conn, state, change)
                self._bump(conn, event.target_id)
                result = self._build_result(conn, event, change, state)
                conn.execute("UPDATE events SET result = ? WHERE id = ?", (_json(result), event.id))
                self._set_meta(conn, "state_seq", str(seq))
                results.append(result)
        return results

    def submission_results(self, client_id: str, prefix: str) -> list[dict[str, Any]]:
        """Read persisted results for one client's submission, including after a restart."""
        with self._lock, self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM events WHERE client_id = ? AND substr(idempotency_key, 1, ?) = ? "
                "ORDER BY seq", (client_id, len(prefix), prefix),
            )
            return [json.loads(row["result"]) for row in rows if row["result"]]

    def create_line(self, request: LineRequest) -> dict[str, Any]:
        """Record a line a detector missed and return it with its revision."""
        with self._lock, self._connection() as conn:
            duplicate = self._by_key(conn, request.client_id, request.idempotency_key)
            if duplicate is not None:
                return self._result(conn, duplicate, duplicate=True)
            if request.page_id not in self.pages():
                raise NotFound(f"no page {request.page_id}")
            existing = self._lines_of_page(conn, request.page_id)
            seq = request.seq
            if seq is None:
                seq = max((line.seq for line in existing), default=-1) + 1
            line = Line(
                id=f"{request.page_id}:l{_next_number([record.id for record in existing], f'{request.page_id}:l')}",
                page_id=request.page_id,
                seq=seq,
                box=request.box,
                vertical=request.vertical,
                role=request.role,
                text_raw=request.text_raw,
                text=request.text,
                match_method=request.match_method or MANUAL,
                match_confidence=request.match_confidence,
                meta=request.meta,
            )
            event = _created("line", line, request.client_id)
            state = self._state_for(conn, event)
            change = _change(state, event, guard=False)
            return self._commit(conn, change, state, request.client_id, request.idempotency_key)

    def create_unit(self, request: UnitRequest) -> dict[str, Any]:
        """Record a unit drawn on an existing line and return it with its revision."""
        with self._lock, self._connection() as conn:
            duplicate = self._by_key(conn, request.client_id, request.idempotency_key)
            if duplicate is not None:
                return self._result(conn, duplicate, duplicate=True)
            line = self._line_row(conn, request.line_id)
            if line is None:
                raise NotFound(f"no line {request.line_id}")
            existing = self._units_of_line(conn, request.line_id, active=False)
            page = self.page(line.page_id)
            unit = Unit(
                id=f"{request.line_id}:m{_next_number([record.id for record in existing], f'{request.line_id}:m')}",
                document_id=page.document_id if page else None,
                page_id=line.page_id,
                line_id=request.line_id,
                seq=request.seq,
                box=request.box,
                reading=request.reading,
                text_source=request.text_source,
                unicode=request.unicode,
                kind=request.kind,
                granularity=request.granularity,
                classification=request.classification,
                script=request.script,
                voicing=request.voicing,
                method=MANUAL,
                review=ReviewState.TRANSCRIBER,
            )
            event = _created("unit", unit, request.client_id)
            state = self._state_for(conn, event)
            change = _change(state, event, guard=False)
            return self._commit(conn, change, state, request.client_id, request.idempotency_key)

    def export(self) -> dict[str, int]:
        """Write the state back to the dataset tables and the events to `reviews.jsonl`."""
        with self._lock, self._connection() as conn:
            lines = self._all_lines(conn)
            units = self._all_units(conn)
            events = self._events(conn)
        counts: dict[str, int] = {}
        for name, records, model in (("lines", lines, Line), ("units", units, Unit)):
            path = self.dataset.tables[name]
            if path is None and not records:
                counts[name] = 0
                continue
            target = path if path is not None else self.directory / f"{name}.parquet"
            counts[name] = tables.write(
                target,
                records,
                model,
                shard=target.is_dir(),
                command=f"atlas review apply {self.directory}",
            )
        log = self.directory / LOG_NAME
        with log.open("w", encoding="utf-8") as handle:
            for event in events:
                handle.write(json.dumps(event.model_dump(mode="json"), ensure_ascii=False, sort_keys=True) + "\n")
        counts["reviews"] = len(events)
        with self._lock, self._connection() as conn:
            self._set_meta(conn, "exported_events", str(len(events)))
        return counts

    def rebuild(self) -> dict[str, int]:
        """Rebuild the state from the tables and the log, repair it and report what changed.

        Events the store does not have are taken from `reviews.jsonl` first; then every event is
        replayed over the tables in order. An event whose effect is already present is left alone, so
        a rebuilt store equals the tables it was built from. `repaired` counts the state rows the
        rebuild changed, `adopted` the events it took from the log, `skipped` the events that were
        already in effect.
        """
        with self._lock:
            imported = self._import_log()
            with self._connection() as conn:
                events = self._events(conn)
                before_lines = {line.id: line for line in self._all_lines(conn)}
                before_units = {unit.id: unit for unit in self._all_units(conn)}
                state = State(self._table_records("lines"), self._table_records("units"))
                skipped = 0
                for event in events:
                    if _change(state, event, guard=True).skipped:
                        skipped += 1
                repaired = _differences(before_lines, state.lines) + _differences(before_units, state.units)
                self._write_state(conn, state, events, self._last_seq(conn))
            return {
                "lines": len(state.lines),
                "units": len(state.units),
                "events": len(events),
                "repaired": repaired,
                "adopted": imported,
                "skipped": skipped,
            }

    # -- the store's own storage -----------------------------------------------------------------

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, isolation_level=None, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def _transaction(self, conn: sqlite3.Connection) -> Iterator[None]:
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")

    def _schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript(_SCHEMA)

    def _meta(self, conn: sqlite3.Connection, key: str) -> str | None:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def _set_meta(self, conn: sqlite3.Connection, key: str, value: str) -> None:
        conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    def _table_records(self, name: str) -> list[Any]:
        path = self.dataset.tables[name]
        if path is None:
            return []
        return list(tables.read(path, tables.TABLES[name]))

    def _source_stamp(self) -> str:
        """A fingerprint of the tables the store copies: their file names, sizes and mtimes."""
        parts: list[str] = []
        for name in ("lines", "units"):
            path = self.dataset.tables[name]
            if path is None:
                parts.append(f"{name}:-")
                continue
            if path.is_dir():
                files = sorted(path.glob("*.parquet"))
                detail = ";".join(f"{file.name}:{file.stat().st_size}:{int(file.stat().st_mtime)}" for file in files)
            else:
                detail = f"{path.stat().st_size}:{int(path.stat().st_mtime)}"
            parts.append(f"{name}:{detail}")
        return "|".join(parts)

    def _event_count(self, conn: sqlite3.Connection) -> int:
        row = conn.execute("SELECT COUNT(*) AS n FROM events").fetchone()
        return int(row["n"]) if row else 0

    def _unexported_events(self, conn: sqlite3.Connection) -> int:
        """Review events the store holds that `reviews.jsonl` does not.

        `apply` writes the state to the tables and the events to the log, and records how many events
        it exported. When that count matches what the store holds, every decision is already in the
        log and the store can be rebuilt from the tables without losing one; anything else means work
        the log has never seen, which is what the store refuses to throw away. File times are not used
        for this: the database is written when the store closes, so it is routinely newer than the
        log it agrees with.
        """
        count = self._event_count(conn)
        if not count:
            return 0
        exported = self._meta(conn, "exported_events")
        return 0 if exported is not None and int(exported) == count else count

    def _replay_events(self, conn: sqlite3.Connection) -> None:
        """Apply the events the store already holds to the state that was just loaded."""
        events = self._events(conn)
        if not events:
            return
        state = State(self._all_lines(conn), self._all_units(conn))
        for event in events:
            _change(state, event, guard=True)
        self._write_state(conn, state, events, self._last_seq(conn))

    def _load(self, conn: sqlite3.Connection, stamp: str) -> None:
        """Copy the dataset tables into the store, once."""
        lines = self._table_records("lines")
        units = self._table_records("units")
        with self._transaction(conn):
            for line in lines:
                self._insert_line(conn, line)
            for unit in units:
                self._insert_unit(conn, unit)
            self._set_meta(conn, "loaded", "1")
            self._set_meta(conn, "source_stamp", stamp)
            self._set_meta(conn, "schema_version", str(SCHEMA_VERSION))
            self._set_meta(conn, "state_seq", self._meta(conn, "state_seq") or "0")
            self._set_meta(conn, "created_at", datetime.now(UTC).isoformat(timespec="seconds"))

    def _line_document(self, line: Line) -> str | None:
        page = self.pages().get(line.page_id)
        return page.document_id if page else None

    def _insert_line(self, conn: sqlite3.Connection, line: Line) -> None:
        conn.execute(
            "INSERT OR REPLACE INTO lines (id, document_id, page_id, seq, data) VALUES (?, ?, ?, ?, ?)",
            (line.id, self._line_document(line), line.page_id, line.seq, _dump(line)),
        )

    def _insert_unit(self, conn: sqlite3.Connection, unit: Unit) -> None:
        page = self.pages().get(unit.page_id) if unit.page_id else None
        document_id = unit.document_id or (page.document_id if page else None)
        conn.execute(
            "INSERT OR REPLACE INTO units (id, document_id, page_id, line_id, seq, active, data) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (unit.id, document_id, unit.page_id, unit.line_id, unit.seq, int(unit.active), _dump(unit)),
        )

    def _line_row(self, conn: sqlite3.Connection, line_id: str) -> Line | None:
        row = conn.execute("SELECT data FROM lines WHERE id = ?", (line_id,)).fetchone()
        return Line.model_validate_json(row["data"]) if row else None

    def _unit_row(self, conn: sqlite3.Connection, unit_id: str) -> Unit | None:
        row = conn.execute("SELECT data FROM units WHERE id = ?", (unit_id,)).fetchone()
        return Unit.model_validate_json(row["data"]) if row else None

    def _lines_of_page(self, conn: sqlite3.Connection, page_id: str) -> list[Line]:
        rows = conn.execute(
            "SELECT data FROM lines WHERE page_id = ? ORDER BY (seq IS NULL), seq, id", (page_id,)
        )
        return [Line.model_validate_json(row["data"]) for row in rows]

    def _units_of_line(self, conn: sqlite3.Connection, line_id: str, *, active: bool = True) -> list[Unit]:
        clause = " AND active = 1" if active else ""
        rows = conn.execute(
            f"SELECT data FROM units WHERE line_id = ?{clause} ORDER BY (seq IS NULL), seq, id", (line_id,)
        )
        return [Unit.model_validate_json(row["data"]) for row in rows]

    def _all_lines(self, conn: sqlite3.Connection) -> list[Line]:
        return [Line.model_validate_json(row["data"]) for row in conn.execute("SELECT data FROM lines ORDER BY id")]

    def _all_units(self, conn: sqlite3.Connection) -> list[Unit]:
        return [Unit.model_validate_json(row["data"]) for row in conn.execute("SELECT data FROM units ORDER BY id")]

    def _revision_map(self, conn: sqlite3.Connection) -> dict[str, int]:
        return {row["target_id"]: row["revision"] for row in conn.execute("SELECT * FROM revisions")}

    def _revision(self, conn: sqlite3.Connection, target_id: str) -> int:
        row = conn.execute("SELECT revision FROM revisions WHERE target_id = ?", (target_id,)).fetchone()
        return row["revision"] if row else 0

    def _group_counts(self) -> tuple[dict[str, int], dict[str, int], dict[str, int]]:
        with self._connection() as conn:
            lines = {
                row["document_id"]: row["n"]
                for row in conn.execute("SELECT document_id, count(*) AS n FROM lines GROUP BY document_id")
            }
            units = {
                row["document_id"]: row["n"]
                for row in conn.execute(
                    "SELECT document_id, count(*) AS n FROM units WHERE active = 1 GROUP BY document_id"
                )
            }
            reviewed = {
                row["document_id"]: row["n"]
                for row in conn.execute(
                    "SELECT units.document_id AS document_id, count(*) AS n FROM units "
                    "JOIN revisions ON revisions.target_id = units.id WHERE units.active = 1 "
                    "GROUP BY units.document_id"
                )
            }
        return lines, units, reviewed

    def _state_dump(self, conn: sqlite3.Connection, target_type: str, target_id: str) -> dict[str, Any] | None:
        record: Line | Unit | None = None
        if target_type == "unit":
            record = self._unit_row(conn, target_id)
        elif target_type == "line":
            record = self._line_row(conn, target_id)
        return record.model_dump(mode="json") if record else None

    def _check_target(self, conn: sqlite3.Connection, request: ReviewRequest) -> None:
        """Refuse a review of a page, a document or a group that the dataset does not have."""
        if request.target_type == "page" and request.target_id not in self.pages():
            raise NotFound(f"no page {request.target_id}")
        if request.target_type == "document" and self.document(request.target_id) is None:
            raise NotFound(f"no document {request.target_id}")
        if request.target_type == "group":
            path = self.dataset.tables["groups"]
            groups = tables.read(path, tables.TABLES["groups"]) if path else []
            if not any(group.id == request.target_id for group in groups):
                raise NotFound(f"no group {request.target_id}")

    def _state_for(self, conn: sqlite3.Connection, event: Review) -> State:
        """The records a change may touch, read from the store."""
        if event.field == CREATE:
            new = event.new if isinstance(event.new, dict) else {}
            if event.target_type == "unit":
                line_id = new.get("line_id")
                line = self._line_row(conn, line_id) if isinstance(line_id, str) else None
                return State([line] if line else [], self._units_of_line(conn, line_id) if line else [])
            page_id = new.get("page_id")
            return State(self._lines_of_page(conn, page_id) if isinstance(page_id, str) else [])
        if event.field == SEGMENTATION:
            unit = self._unit_row(conn, event.target_id)
            if unit is None:
                raise NotFound(f"no unit {event.target_id}")
            line = self._line_row(conn, unit.line_id) if unit.line_id else None
            siblings = self._units_of_line(conn, unit.line_id, active=False) if unit.line_id else [unit]
            return State([line] if line else [], siblings)
        if event.target_type == "unit":
            unit = self._unit_row(conn, event.target_id)
            if unit is None:
                raise NotFound(f"no unit {event.target_id}")
            return State(units=[unit])
        if event.target_type == "line":
            line = self._line_row(conn, event.target_id)
            if line is None:
                raise NotFound(f"no line {event.target_id}")
            return State(lines=[line])
        return State()

    def _by_key(self, conn: sqlite3.Connection, client_id: str | None, key: str | None) -> sqlite3.Row | None:
        if not key:
            return None
        return conn.execute(
            "SELECT * FROM events WHERE client_id = ? AND idempotency_key = ?", (client_id or "", key)
        ).fetchone()

    def _events(self, conn: sqlite3.Connection) -> list[Review]:
        return [_review(row) for row in conn.execute("SELECT * FROM events ORDER BY seq")]

    def _last_seq(self, conn: sqlite3.Connection) -> int:
        row = conn.execute("SELECT COALESCE(MAX(seq), 0) AS seq FROM events").fetchone()
        return int(row["seq"])

    def _commit(
        self,
        conn: sqlite3.Connection,
        change: Change,
        state: State,
        client_id: str | None,
        idempotency_key: str | None,
    ) -> dict[str, Any]:
        """Append the event, then write the state it implies, in two transactions.

        The two steps are what makes the log the source of truth: a crash after the first leaves an
        event whose effect `replay` can add. The result is stored with the state, so a repeated
        `idempotency_key` needs no work beyond reading it back.
        """
        with self._transaction(conn):
            seq = self._last_seq(conn) + 1
            event = change.event.model_copy(update={"id": f"rv{seq:08d}"})
            conn.execute(
                "INSERT INTO events (id, target_type, target_id, field, old, new, role, actor, evidence, at, "
                "client_id, idempotency_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    event.id,
                    event.target_type,
                    event.target_id,
                    event.field,
                    _json(event.old),
                    _json(event.new),
                    event.role,
                    event.actor,
                    event.evidence,
                    event.at.isoformat(),
                    client_id or "",
                    idempotency_key,
                ),
            )
        change.event = event
        with self._transaction(conn):
            self._persist(conn, state, change)
            self._bump(conn, event.target_id)
            result = self._build_result(conn, event, change, state)
            conn.execute("UPDATE events SET result = ? WHERE id = ?", (_json(result), event.id))
            self._set_meta(conn, "state_seq", str(seq))
        return result

    def _persist(self, conn: sqlite3.Connection, state: State, change: Change) -> None:
        for record in change.records():
            record = state.get(record.id) or record
            if isinstance(record, Unit):
                self._insert_unit(conn, record)
            else:
                self._insert_line(conn, record)

    def _bump(self, conn: sqlite3.Connection, target_id: str) -> None:
        conn.execute(
            "INSERT INTO revisions (target_id, revision) VALUES (?, 1) "
            "ON CONFLICT(target_id) DO UPDATE SET revision = revision + 1",
            (target_id,),
        )

    def _build_result(
        self, conn: sqlite3.Connection, event: Review, change: Change, state: State
    ) -> dict[str, Any]:
        target = state.get(event.target_id)
        return {
            "id": event.id,
            "target_type": event.target_type,
            "target_id": event.target_id,
            "field": event.field,
            "revision": self._revision(conn, event.target_id),
            "duplicate": False,
            "review": event.model_dump(mode="json"),
            "state": target.model_dump(mode="json") if target is not None else None,
            "created": [record.id for record in change.created],
            "retired": [record.id for record in change.retired],
            "warnings": list(change.warnings),
        }

    def _result(self, conn: sqlite3.Connection, row: sqlite3.Row, *, duplicate: bool) -> dict[str, Any]:
        """The earlier answer to a repeated `idempotency_key`, with the revision it has now."""
        stored = json.loads(row["result"]) if row["result"] else self._derived_result(conn, row)
        stored["duplicate"] = duplicate
        stored["revision"] = self._revision(conn, row["target_id"])
        return stored

    def _derived_result(self, conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
        """An answer rebuilt from the event, for an event a crash left without one."""
        event = _review(row)
        return {
            "id": event.id,
            "target_type": event.target_type,
            "target_id": event.target_id,
            "field": event.field,
            "revision": self._revision(conn, event.target_id),
            "duplicate": False,
            "review": event.model_dump(mode="json"),
            "state": self._state_dump(conn, event.target_type, event.target_id),
            "created": [],
            "retired": [],
            "warnings": [],
        }

    def _import_log(self) -> int:
        """Add the events `reviews.jsonl` has and the store does not; return how many."""
        log = self.directory / LOG_NAME
        if not log.exists():
            return 0
        known = {event.id for event in self.events()}
        missing: list[Review] = []
        for line in log.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                event = Review.model_validate_json(line)
            except ValidationError as exc:
                raise BadRequest(f"{log}: {_problem(exc)}") from exc
            if event.id not in known:
                missing.append(event)
                known.add(event.id)
        if not missing:
            return 0
        with self._connection() as conn, self._transaction(conn):
            seq = self._last_seq(conn)
            for event in missing:
                seq += 1
                conn.execute(
                    "INSERT INTO events (seq, id, target_type, target_id, field, old, new, role, actor, "
                    "evidence, at, client_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '')",
                    (
                        seq,
                        event.id,
                        event.target_type,
                        event.target_id,
                        event.field,
                        _json(event.old),
                        _json(event.new),
                        event.role,
                        event.actor,
                        event.evidence,
                        event.at.isoformat(),
                    ),
                )
        return len(missing)

    def _write_state(
        self, conn: sqlite3.Connection, state: State, events: list[Review], state_seq: int
    ) -> None:
        """Replace the state and the revisions with what the log says they are."""
        revisions = Counter(event.target_id for event in events)
        with self._transaction(conn):
            conn.execute("DELETE FROM lines")
            conn.execute("DELETE FROM units")
            conn.execute("DELETE FROM revisions")
            for line in state.lines.values():
                self._insert_line(conn, line)
            for unit in state.units.values():
                self._insert_unit(conn, unit)
            for target_id, revision in revisions.items():
                conn.execute(
                    "INSERT INTO revisions (target_id, revision) VALUES (?, ?)", (target_id, revision)
                )
            self._set_meta(conn, "state_seq", str(state_seq))
            self._set_meta(conn, "replayed_at", datetime.now(UTC).isoformat(timespec="seconds"))


def apply(directory: Path) -> dict[str, int]:
    """Write the reviewed state back to the dataset tables and the events to `reviews.jsonl`.

    Returns the rows written per table and the number of events, `{"lines", "units", "reviews"}`.
    """
    return Store(Path(directory), exporting=True).export()


def replay(directory: Path) -> dict[str, int]:
    """Rebuild the store's state from the tables and the log, and repair what differs.

    Returns `{"lines", "units", "events", "repaired", "adopted", "skipped"}`: the sizes of the
    rebuilt state, the rows repaired, the events taken from `reviews.jsonl`, and the events that
    were already in effect.
    """
    return Store(Path(directory), rebuilding=True).rebuild()


# -- the change an event makes -------------------------------------------------------------------


def _change(state: State, event: Review, *, guard: bool) -> Change:
    """Compute the records one event produces and write them into `state`.

    With `guard` the change leaves an event alone whose effect the state already shows, which is how
    a log is replayed over tables that `apply` has already written. Without it every event is a
    decision the client has just made, and a target that cannot take it raises.
    """
    if event.field == CREATE:
        return _create_change(state, event, guard=guard)
    if event.field == SEGMENTATION:
        return _segment(state, event, guard=guard)
    if event.field in STATELESS or event.target_type not in ("unit", "line"):
        return Change(event=event)
    return _field_change(state, event, guard=guard)


def _field_change(state: State, event: Review, *, guard: bool) -> Change:
    record = state.get(event.target_id)
    if record is None:
        return _missing(event, guard)
    if isinstance(record, Unit) and not record.active:
        if guard:
            return Change(event=event, skipped=True)
        raise Conflict(
            "retired",
            target_type=event.target_type,
            target_id=event.target_id,
            state=record.model_dump(mode="json"),
        )
    if event.field not in type(record).model_fields:
        raise BadRequest(f"{event.target_type} {record.id} has no field {event.field!r}")
    before = getattr(record, event.field)
    data = record.model_dump(mode="json")
    data[event.field] = event.new
    try:
        after = type(record).model_validate(data)
    except ValidationError as exc:
        raise BadRequest(f"{event.field}: {_problem(exc)}") from exc
    if guard and getattr(after, event.field) == before:
        return Change(event=event, skipped=True)
    state.put(after)
    return Change(event=event.model_copy(update={"old": _plain(before)}), updated=[after])


def _create_change(state: State, event: Review, *, guard: bool) -> Change:
    model = Unit if event.target_type == "unit" else Line
    try:
        record = model.model_validate(event.new)
    except ValidationError as exc:
        raise BadRequest(f"{event.target_type}: {_problem(exc)}") from exc
    if state.get(record.id) is not None:
        if guard:
            return Change(event=event, skipped=True)
        raise BadRequest(f"{event.target_type} {record.id} exists already")
    state.put(record)
    return Change(event=event, created=[record])


def _segment(state: State, event: Review, *, guard: bool) -> Change:
    """Retire the inputs of a split or a merge and create the outputs, in one event."""
    new = event.new
    if not isinstance(new, dict) or len({"split", "merge"} & set(new)) != 1:
        raise BadRequest('segmentation needs {"split": [...]} or {"merge": [ids]}')
    if "split" in new:
        return _split(state, event, new["split"], guard=guard)
    return _merge(state, event, new["merge"], guard=guard)


def _split(state: State, event: Review, entries: Any, *, guard: bool) -> Change:
    unit = state.get(event.target_id)
    if unit is None:
        return _missing(event, guard)
    if not isinstance(unit, Unit):
        raise BadRequest(f"{event.target_id} is a line; only a unit can be split")
    if unit.line_id is None:
        raise BadRequest(f"unit {unit.id} has no line; it cannot be split")
    if not unit.active:
        if guard:
            return Change(event=event, skipped=True)
        raise Conflict(
            "retired",
            target_type="unit",
            target_id=unit.id,
            state=unit.model_dump(mode="json"),
        )
    if not isinstance(entries, list) or len(entries) < 2:
        raise BadRequest("a split needs at least two entries, each with a box")
    number = _next_number(state.units, f"{unit.line_id}:m") - 1
    outputs = []
    for entry in entries:
        if not isinstance(entry, dict) or "box" not in entry:
            raise BadRequest("every split entry needs a box")
        unknown = set(entry) - SPLIT_KEYS
        if unknown:
            raise BadRequest(f"a split entry cannot set {', '.join(sorted(unknown))}")
        try:
            box = Box.model_validate(entry["box"])
        except ValidationError as exc:
            raise BadRequest(f"box: {_problem(exc)}") from exc
        if box.w <= 0 or box.h <= 0:
            raise BadRequest(f"box {box.x},{box.y},{box.w},{box.h}: w and h must be positive")
        number += 1
        data = unit.model_dump(mode="json")
        data.update(entry)
        data.update(
            {
                "id": f"{unit.line_id}:m{number}",
                "active": True,
                "split_into": [],
                "merged_into": None,
                "method": MANUAL,
                "review": ReviewState.TRANSCRIBER.value,
            }
        )
        outputs.append(Unit.model_validate(data))
    retired = unit.model_copy(update={"active": False, "split_into": [output.id for output in outputs]})
    state.put(retired)
    for output in outputs:
        state.put(output)
    line = state.lines.get(unit.line_id)
    warnings = _outside(outputs, line)
    updated = _renumber(state, unit.line_id, line.vertical if line else True)
    return Change(
        event=event.model_copy(update={"old": unit.model_dump(mode="json")}),
        updated=updated,
        created=[state.units[output.id] for output in outputs],
        retired=[retired],
        warnings=warnings,
    )


def _merge(state: State, event: Review, ids: Any, *, guard: bool) -> Change:
    if not isinstance(ids, list) or len(ids) < 2 or not all(isinstance(identifier, str) for identifier in ids):
        raise BadRequest("a merge needs the ids of at least two units")
    inputs: list[Unit] = []
    for identifier in ids:
        found = state.units.get(identifier)
        if found is None:
            return _missing(event, guard, f"no unit {identifier}")
        inputs.append(found)
    if event.target_id not in ids:
        raise BadRequest(f"the target {event.target_id} is not one of the merged units")
    lines = {unit.line_id for unit in inputs}
    if len(lines) != 1 or None in lines:
        raise BadRequest("a merge joins units of one line")
    line_id = next(iter(lines))
    if any(not unit.active for unit in inputs):
        if guard:
            return Change(event=event, skipped=True)
        retired = next(unit for unit in inputs if not unit.active)
        raise Conflict(
            "retired",
            target_type="unit",
            target_id=retired.id,
            state=retired.model_dump(mode="json"),
        )
    line = state.lines.get(line_id)
    vertical = line.vertical if line else True
    ordered = sorted(inputs, key=lambda unit: _order_key(unit, vertical))
    output_id = f"{line_id}:m{_next_number(state.units, f'{line_id}:m')}"
    boxes = [unit.box for unit in ordered]
    box = _union(boxes) if all(box is not None for box in boxes) else None
    readings = "".join(unit.reading or "" for unit in ordered)
    unicodes = [unit.unicode for unit in ordered]
    texts = "".join(unit.text_source or "" for unit in ordered)
    data = ordered[0].model_dump(mode="json")
    data.update(
        {
            "id": output_id,
            "box": box.model_dump() if box else None,
            "reading": readings or None,
            "text_source": texts or None,
            "unicode": " ".join(unicodes) if all(unicodes) else None,
            "kind": UnitKind.LIGATURE.value,
            "granularity": "char",
            "classification": Classification.UNASSESSED.value,
            "candidates": [],
            "voicing": None,
            "variants": [],
            "confidence": None,
            "antecedent_ids": [],
            "group_id": None,
            "active": True,
            "split_into": [],
            "merged_into": None,
            "method": MANUAL,
            "review": ReviewState.TRANSCRIBER.value,
        }
    )
    output = Unit.model_validate(data)
    state.put(output)
    retired = [unit.model_copy(update={"active": False, "merged_into": output_id}) for unit in ordered]
    for unit in retired:
        state.put(unit)
    warnings = _outside([output], line)
    updated = _renumber(state, line_id, vertical)
    return Change(
        event=event.model_copy(update={"old": [unit.model_dump(mode="json") for unit in ordered]}),
        updated=updated,
        created=[state.units[output.id]],
        retired=retired,
        warnings=warnings,
    )


def _missing(event: Review, guard: bool, message: str | None = None) -> Change:
    if guard:
        return Change(event=event, skipped=True)
    raise NotFound(message or f"no {event.target_type} {event.target_id}")


def _renumber(state: State, line_id: str, vertical: bool) -> list[Unit]:
    """Number the active units of a line from the top (or the left), 0-based."""
    active = sorted(
        (unit for unit in state.units.values() if unit.line_id == line_id and unit.active),
        key=lambda unit: _order_key(unit, vertical),
    )
    changed = []
    for index, unit in enumerate(active):
        if unit.seq != index:
            unit = unit.model_copy(update={"seq": index})
            state.units[unit.id] = unit
            changed.append(unit)
    return changed


def _outside(outputs: list[Unit], line: Line | None) -> list[str]:
    """Flag an output whose box leaves the box of its line; the split is accepted either way."""
    if line is None or line.box is None:
        return []
    warnings = []
    for unit in outputs:
        box = unit.box
        if box is None:
            continue
        if (
            box.x < line.box.x
            or box.y < line.box.y
            or box.x + box.w > line.box.x + line.box.w
            or box.y + box.h > line.box.y + line.box.h
        ):
            warnings.append(f"unit {unit.id} box {box.x},{box.y},{box.w},{box.h} leaves line {line.id}")
    return warnings


def _order_key(unit: Unit, vertical: bool) -> tuple[int, int, int, str]:
    """Reading order within a line: down the page for vertical text, across it otherwise."""
    if unit.box is None:
        return (1, unit.seq if unit.seq is not None else 1 << 30, 0, unit.id)
    return (
        0,
        unit.box.y if vertical else unit.box.x,
        unit.box.x if vertical else unit.box.y,
        unit.id,
    )


def _union(boxes: list[Box]) -> Box:
    left = min(box.x for box in boxes)
    top = min(box.y for box in boxes)
    right = max(box.x + box.w for box in boxes)
    bottom = max(box.y + box.h for box in boxes)
    return Box(x=left, y=top, w=right - left, h=bottom - top)


def _next_number(ids: Iterable[str], prefix: str) -> int:
    """One past the highest number an id with this prefix carries."""
    pattern = re.compile(rf"^{re.escape(prefix)}(\d+)$")
    numbers = [int(found.group(1)) for identifier in ids if (found := pattern.match(identifier))]
    return max(numbers, default=0) + 1


def _disagrees(unit: Unit) -> bool:
    """Whether the classifier left the unit open or ranked another code point first."""
    if unit.classification in (Classification.AMBIGUOUS, Classification.UNIDENTIFIED):
        return True
    if not unit.candidates:
        return False
    best = max(unit.candidates, key=lambda candidate: candidate.p)
    return best.unicode != (unit.unicode or "")


def _differences(before: dict[str, Any], after: dict[str, Any]) -> int:
    """How many records the rebuild added, dropped or changed."""
    changed = sum(1 for ident, record in after.items() if ident not in before or before[ident] != record)
    return changed + sum(1 for ident in before if ident not in after)


def _created(target_type: Literal["unit", "line"], record: Line | Unit, client_id: str | None) -> Review:
    """The event that brings a record a reviewer drew into the log."""
    return Review(
        id="",
        target_type=target_type,
        target_id=record.id,
        field=CREATE,
        old=None,
        new=record.model_dump(mode="json"),
        role="transcriber",
        actor=client_id,
        at=datetime.now(UTC),
    )


def _review(row: sqlite3.Row) -> Review:
    """A row of the event table as a record of the log."""
    return Review(
        id=row["id"],
        target_type=row["target_type"],
        target_id=row["target_id"],
        field=row["field"],
        old=json.loads(row["old"]) if row["old"] is not None else None,
        new=json.loads(row["new"]) if row["new"] is not None else None,
        role=row["role"],
        actor=row["actor"],
        evidence=row["evidence"],
        at=datetime.fromisoformat(row["at"]),
    )


def _dump(record: Line | Unit) -> str:
    return record.model_dump_json()


def _json(value: Any) -> str:
    return json.dumps(_plain(value), ensure_ascii=False, sort_keys=True)


def _plain(value: Any) -> Any:
    """A value as JSON can hold it: models and enums written out."""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_plain(item) for item in value]
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if hasattr(value, "value"):
        return value.value
    return value


def _problem(exc: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in problem['loc'])}: {problem['msg']}" for problem in exc.errors()[:3]
    )
