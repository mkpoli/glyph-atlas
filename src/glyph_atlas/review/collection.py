"""Read-only progress for the background book collector."""
from __future__ import annotations

import fcntl
import json
import shutil
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter


def _json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def worker_active(path: Path) -> bool:
    try:
        with path.open("r") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            fcntl.flock(handle, fcntl.LOCK_UN)
    except FileNotFoundError:
        pass
    return False


def _honkoku_status(root: Path) -> dict:
    collection = root / "honkoku-collection"
    saved = _json(collection / "status.json")
    active = worker_active(collection / "worker.lock")
    free = shutil.disk_usage(collection if collection.exists() else root).free
    result = {"status": "idle", "phase": "collecting", "total": 0, "completed": 0,
              "pending": 0, "failed": 0, "current": None, "last_completed": None,
              "next_at": None, "pause_seconds": saved.get("book_pause", 60),
              "pages": 0, "text_pages": 0, "disk_free_bytes": free,
              "pause_reason": None, "updated_at": saved.get("updated_at"), "additions": [],
              "id": "honkoku", "name": "Honkoku", "discovery_complete": False,
              "character_crops": 0, "published_books": 0}
    for path in root.glob("*/MANIFEST.json"):
        summary = _json(path).get("collection")
        if isinstance(summary, dict):
            result["additions"].append(summary)
    db = collection / "queue.sqlite"
    if not db.exists():
        return result
    connection = sqlite3.connect(db.as_uri() + "?mode=ro", uri=True, timeout=2)
    connection.row_factory = sqlite3.Row
    try:
        counts = dict(connection.execute("SELECT state, count(*) FROM books GROUP BY state"))
        total = sum(counts.values())
        result.update(total=total, completed=counts.get("done", 0),
                      pending=counts.get("pending", 0) + counts.get("in_progress", 0),
                      failed=sum(counts.get(s, 0) for s in ("failed", "missing", "skipped")))
        current = connection.execute("SELECT entry_id AS id, label AS title FROM books WHERE state='in_progress' LIMIT 1").fetchone()
        last = connection.execute("SELECT entry_id AS id, label AS title, finished_at FROM books WHERE state='done' ORDER BY finished_at DESC LIMIT 1").fetchone()
        pages = connection.execute("SELECT coalesce(sum(pages),0), coalesce(sum(transcriptions),0) FROM books WHERE state='done'").fetchone()
        result.update(current=dict(current) if current else None, last_completed=dict(last) if last else None,
                      pages=pages[0], text_pages=pages[1])
        discovery = connection.execute("SELECT count(*) FROM projects WHERE state IN ('pending','failed')").fetchone()[0]
        discovery += connection.execute("SELECT count(*) FROM collections WHERE state IN ('pending','failed')").fetchone()[0]
        result["phase"] = "discovering" if discovery else "collecting" if result["pending"] else "complete"
        result["discovery_complete"] = not discovery
        result["status"] = "running" if active and (current or discovery) else "waiting" if active else "idle"
        if last and last["finished_at"]:
            when = datetime.fromisoformat(last["finished_at"]) + timedelta(seconds=result["pause_seconds"])
            if active and when > datetime.now(UTC):
                result["next_at"] = when.isoformat()
                if not discovery:
                    result["status"] = "waiting"
        if free < saved.get("min_free_bytes", 5 * 1024**3):
            result.update(status="paused", pause_reason="Less than 5 GiB free on disk")
    finally:
        connection.close()
    result["published_books"] = _json(collection / "published.json").get("published", 0)
    return result


def _wikisource_status(root: Path) -> dict:
    collection = root / "wikisource-collection"
    saved = _json(collection / "status.json")
    active = worker_active(collection / "worker.lock")
    works = saved.get("works", {})
    pages = saved.get("page_states", {})
    complete = bool(saved.get("discovery_complete"))
    pending = works.get("pending", 0) + works.get("in_progress", 0)
    free = shutil.disk_usage(collection if collection.exists() else root).free
    result = {"id": "wikisource", "name": "Japanese Wikisource", "total": sum(works.values()),
              "completed": works.get("done", 0), "pending": pending, "failed": works.get("failed", 0),
              "current": saved.get("current"), "pages": saved.get("pages", 0),
              "text_pages": saved.get("text_pages", 0), "discovered_pages": sum(pages.values()),
              "completed_pages": pages.get("done", 0), "discovery_complete": complete,
              "published_books": _json(collection / "published.json").get("published", 0),
              "character_crops": 0, "disk_free_bytes": free, "pause_seconds": saved.get("book_pause", 60),
              "pause_reason": None, "next_at": None, "updated_at": saved.get("updated_at"),
              "phase": "discovering" if not complete else "collecting" if pending else "complete",
              "status": "running" if active and (not complete or saved.get("current")) else "waiting" if active else "idle"}
    if free < saved.get("min_free_bytes", 5 * 1024**3):
        result.update(status="paused", pause_reason="Less than 5 GiB free on collection storage")
    return result


def status(root: Path) -> dict:
    """Read saved progress only; expensive archive counts are built by the publisher."""
    honkoku = _honkoku_status(root)
    wikisource = _wikisource_status(root)
    archive = _json(root / "corpus-index" / "archive.json")
    result = {**honkoku, "sources": [honkoku, wikisource]}
    extraction = _json(root / "character-extraction" / "status.json")
    if extraction:
        result["extraction"] = extraction
    if archive:
        result["archive"] = archive
        counts = archive.get("sources", {})
        honkoku["character_crops"] = sum(counts.get(name, {}).get("character_crops", 0)
                                            for name in ("honkoku-lines", "ainu-records"))
        wikisource["character_crops"] = counts.get("wikisource", {}).get("character_crops", 0)
    for source in result["sources"]:
        source["import_geometry"] = "page_text_only"
        source["alignment_included"] = False
        collection = root / f"{source['id']}-collection"
        if worker_active(collection / "publish.lock"):
            source.update(phase="publishing", status="running")
    return result


def router(root: Path) -> APIRouter:
    routes = APIRouter()

    @routes.get("/atlas/collection/status")
    def progress():
        return status(root.resolve())

    return routes
