#!/usr/bin/env python
"""Collect honkoku.org books, slowly, into an immutable dataset per book.

    python scripts/collect_honkoku.py --root work/honkoku-collection --seed
    python scripts/collect_honkoku.py --root work/honkoku-collection --books 5
    python scripts/collect_honkoku.py --root work/honkoku-collection --status
    python scripts/collect_honkoku.py --root work/honkoku-collection --index

The worker holds one book at a time, waits between requests and between books, and
stops below a free-space floor. Running it again resumes where it left off. It fetches
API JSON only: no page images, no inference.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from kuzushiji_atlas.importers import honkoku_queue as hq


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("work/honkoku-collection"),
        help="the collection directory (default: work/honkoku-collection)",
    )
    parser.add_argument(
        "--snapshot",
        type=Path,
        default=None,
        help="a honkoku-data checkout whose info.tsv rows seed the queue",
    )
    parser.add_argument("--seed", action="store_true", help="seed from the snapshot and stop")
    parser.add_argument(
        "--discover", action="store_true", help="walk live projects, collections and entries, then stop"
    )
    parser.add_argument("--books", type=int, default=None, help="collect at most this many books this run")
    parser.add_argument("--seconds", type=float, default=None, help="stop after this many seconds")
    parser.add_argument(
        "--book-pause", type=float, default=hq.BOOK_PAUSE, help="seconds between books (default: 60)"
    )
    parser.add_argument(
        "--host-pause",
        type=float,
        default=hq.MIN_HOST_PAUSE,
        help="seconds between requests to the host (default: 3)",
    )
    parser.add_argument(
        "--min-free-gib",
        type=float,
        default=hq.MIN_FREE_BYTES / 1024**3,
        help="stop below this much free space (default: 5)",
    )
    parser.add_argument("--entry", default=None, help="collect one named book, for a smoke test")
    parser.add_argument("--status", action="store_true", help="print the status and stop")
    parser.add_argument("--index", action="store_true", help="print the collected-book index and stop")
    parser.add_argument(
        "--checkpoint",
        type=int,
        default=None,
        metavar="GENERATION",
        help="mark every finished book as published in this generation",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    # Reading the outputs does not open the queue: a reader must not create tables,
    # directories or a book claim as a side effect of asking what happened.
    if args.status:
        saved = hq.load_status(args.root)
        print(
            json.dumps(
                saved if saved is not None else {"kind": "honkoku-collection-status", "state": "not-started"},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0 if saved is not None else 1
    if args.index:
        saved = hq.load_index(args.root)
        print(
            json.dumps(
                saved if saved is not None else {"kind": "honkoku-collection-index", "state": "not-started"},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0 if saved is not None else 1

    # Everything else writes, so it happens under the lock, and the collector is built
    # inside it: opening the queue is itself a write.
    with hq.process_lock(args.root):
        collector = hq.make_collector(
            args.root,
            host_pause=args.host_pause,
            book_pause=args.book_pause,
            min_free_bytes=int(args.min_free_gib * 1024**3),
        )
        # Startup, under the lock, before any work: a claim left by a dead worker goes
        # back to the queue so the run resumes instead of finding nothing to do.
        recovered = collector.recover()
        if args.checkpoint is not None:
            pending = [row["entry_id"] for row in collector.queue.unpublished()]
            marked = collector.queue.mark_published(pending, generation=args.checkpoint)
            # A checkpoint is the publisher's receipt: it says a generation now holds
            # these books, so the next merge only has to read what comes after it.
            collector.write_outputs()
            print(json.dumps({"generation": args.checkpoint, "marked": marked}, ensure_ascii=False))
            return 0
        if recovered:
            print(json.dumps({"recovered": recovered}, ensure_ascii=False))
        if args.entry:
            result = collector.collect_entry(args.entry)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result.get("status") in ("collected", "already-collected") else 1
        if args.snapshot is not None:
            added = collector.queue.seed_from_snapshot(args.snapshot)
            print(json.dumps({"seeded": added}, ensure_ascii=False))
        if args.seed:
            collector.write_outputs()
            return 0
        if args.discover:
            print(json.dumps(collector.discover(), ensure_ascii=False))
            collector.write_outputs()
            return 0
        collector.discover()
        result = collector.run(max_books=args.books, max_seconds=args.seconds)
        print(json.dumps({k: v for k, v in result.items() if k != "status"}, ensure_ascii=False, indent=2))
        unfinished = collector.queue.counts()["books"].get("pending", 0)
        discovery_failed = len(collector.queue.projects(state="failed")) + len(collector.queue.collections(state="failed"))
        return 75 if unfinished or discovery_failed else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:  # noqa: BLE001 — report failures without private path tracebacks
        print(re.sub(r"/home/[^/\s]+", "~", f"Collection stopped: {error}"), file=sys.stderr)
        sys.exit(1)
