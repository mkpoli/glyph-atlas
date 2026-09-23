#!/usr/bin/env python3
"""Collect every Japanese Wikisource text namespace, one work at a time."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from kuzushiji_atlas.corpus.wikisource_queue import Collector
from kuzushiji_atlas.importers.honkoku_queue import OutOfSpace, process_lock


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("work/wikisource-collection"))
    parser.add_argument("--seconds", type=float, default=3600)
    parser.add_argument("--books", type=int)
    parser.add_argument("--discover-batches", type=int)
    args = parser.parse_args()
    try:
        with process_lock(args.root):
            collector = Collector(args.root)
            result = collector.run(seconds=args.seconds, max_books=args.books,
                                   discover_batches=args.discover_batches)
            print(json.dumps(result, ensure_ascii=False), flush=True)
            status = collector.status()
            return 75 if (not status["discovery_complete"] or status["works"].get("pending", 0)) else 0
    except OutOfSpace as error:
        print(str(error), flush=True)
        return 75


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:  # noqa: BLE001 - redact diagnostics at the worker boundary
        # Diagnostic categories are sufficient here; private paths never enter worker logs.
        print(f"Wikisource collection stopped: {type(error).__name__}", flush=True)
        raise SystemExit(75) from None
