"""Publish committed Honkoku and Japanese Wikisource works into the search index.

Each source is published in a process of its own, so its memory is returned before the next: a run that
built the index for both in one process peaked at the unit's 2 GB limit, and later ones were killed.
"""
import json
import subprocess
import sys
from pathlib import Path

from publish_collection_status import push as push_status

from glyph_atlas.corpus.collection import archive_statistics, listing_digest, publish

ROOT = Path("work")
SOURCES = ("honkoku", "wikisource")


def publish_source(source: str) -> int:
    try:
        print(json.dumps(publish(ROOT, source=source), ensure_ascii=False), flush=True)
    except Exception as error:  # noqa: BLE001 - redact diagnostics at the worker boundary
        print(f"{source} publication stopped: {type(error).__name__}", file=sys.stderr)
        return 1
    return 0


def pending(source: str) -> bool:
    """Whether the committed listing differs from the one last published."""
    collection = ROOT / f"{source}-collection"
    listing = collection / "index.json"
    if not listing.exists():
        return False
    # The set of committed books, not their number: a reopened work and a new one keep the count.
    books = json.loads(listing.read_text()).get("books", [])
    receipt = collection / "published.json"
    previous = json.loads(receipt.read_text()).get("listing_sha256") if receipt.exists() else None
    return bool(books) and listing_digest(books) != previous


def main():
    failed = False
    changed = False
    for source in SOURCES:
        if not pending(source):
            continue
        code = subprocess.run([sys.executable, __file__, source], check=False).returncode
        if code < 0:
            print(f"{source} publication stopped: signal {-code}", file=sys.stderr)
        failed |= code != 0
        changed |= code == 0
    if changed or not (ROOT / "corpus-index/archive.json").is_file():
        archive_statistics(ROOT)
    push_status()
    return int(failed)


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] in SOURCES:
        raise SystemExit(publish_source(sys.argv[1]))
    raise SystemExit(main())
