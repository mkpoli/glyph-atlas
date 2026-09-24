"""Durable receipts for feedback batches, separate from their review history."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

ACKNOWLEDGED_OUTCOMES = frozenset({
    "resolved", "confirmed", "withheld", "unchanged", "recropped", "split",
    "already-processed", "stale", "retired", "unconfirmed",
})


def fingerprint(record: dict) -> str:
    """Identify the immutable event; mutable export snapshots do not identify it.

    A reset can reuse an event number. Its timestamp, actor, target, evidence and
    decision still distinguish the new event from one handled before the reset.
    Local and external-corpus events have separate origins.
    """
    event = record.get("event")
    if not isinstance(event, dict) or not event.get("id") or not event.get("target_id"):
        raise ValueError("A feedback receipt needs an event id and target id.")
    identity = {"origin": record.get("origin", "local"), "event": event}
    encoded = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


class FeedbackReceipts:
    def __init__(self, dataset: Path):
        self.path = Path(dataset) / "feedback-receipts.sqlite"

    def processed(self) -> set[str]:
        if not self.path.is_file():
            return set()
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='feedback_receipts'").fetchone():
                return set()
            return {row[0] for row in db.execute("SELECT fingerprint FROM feedback_receipts")}

    def filter(self, records: list[dict]) -> tuple[list[dict], dict[str, int]]:
        seen = self.processed()
        pending = [record for record in records if fingerprint(record) not in seen]
        return pending, {"total": len(records), "processed": len(records) - len(pending),
                         "unprocessed": len(pending)}

    def mark(self, records: list[dict], *, batch: str, outcomes: dict[str, str] | None = None) -> int:
        """Acknowledge only the supplied exact events, atomically and idempotently.

        Called after a successful offline batch and its durable report. Online
        review saving and background inference never acknowledge feedback.
        """
        at = datetime.now(UTC).isoformat()
        # Validate the complete input before opening a transaction. A broken row
        # must not acknowledge the prefix of a batch.
        rows = [(fingerprint(record), record.get("origin", "local"), record["event"]["id"],
                 record["event"]["target_id"], at, Path(batch).name,
                 (outcomes or {}).get(fingerprint(record), "handled")) for record in records]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS feedback_receipts (
                fingerprint TEXT PRIMARY KEY, origin TEXT NOT NULL, event_id TEXT NOT NULL,
                target_id TEXT NOT NULL, processed_at TEXT NOT NULL,
                batch TEXT NOT NULL, outcome TEXT NOT NULL)""")
            before = db.total_changes
            db.executemany("INSERT OR IGNORE INTO feedback_receipts VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
            return db.total_changes - before


def write_batch_report(path: Path, result: dict) -> None:
    """Publish a complete result before marking any input event handled."""
    import os
    import tempfile

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=path.name + ".", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(result, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def complete_batch(dataset: Path, payload: dict, result: dict, output: Path, *, apply: bool) -> dict:
    """Write the successful batch result, then acknowledge its exact input events.

    A failure during refinement never reaches this function. A report write
    failure leaves every review unprocessed, so it can be exported again safely.
    """
    records = payload.get("reviews", [])
    identities = [fingerprint(record) for record in records]
    write_batch_report(output, result)
    if not apply:
        return {"marked": 0}
    statuses = {key: item["status"]
                for item in result.get("feedback", {}).get("items", [])
                if item.get("event_fingerprint") and item.get("status") in ACKNOWLEDGED_OUTCOMES
                for key in (item["event_fingerprint"], item.get("local_event_fingerprint")) if key}
    # Nonlocal, untrusted and noncurrent input can be skipped by refinement.
    # Receiving a file containing those records does not mean they were handled.
    # Only an explicit outcome bound to the exact input event acknowledges it.
    handled = [record for key, record in zip(identities, records, strict=True) if key in statuses]
    marked = FeedbackReceipts(dataset).mark(handled, batch=output.name, outcomes=statuses)
    return {"marked": marked}
