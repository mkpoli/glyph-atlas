"""Publish committed Honkoku and Japanese Wikisource works into the search index."""
import json
import sys
from pathlib import Path

from glyph_atlas.corpus.collection import archive_statistics, publish


def main():
    root = Path("work")
    failed = False
    changed = False
    for source in ("honkoku", "wikisource"):
        collection = root / f"{source}-collection"
        listing = collection / "index.json"
        if not listing.exists():
            continue
        count = json.loads(listing.read_text()).get("count", 0)
        receipt = collection / "published.json"
        previous = json.loads(receipt.read_text()).get("published", 0) if receipt.exists() else 0
        if count <= previous:
            continue
        try:
            print(json.dumps(publish(root, source=source), ensure_ascii=False), flush=True)
            changed = True
        except Exception as error:  # noqa: BLE001 - redact diagnostics at the worker boundary
            print(f"{source} publication stopped: {type(error).__name__}", file=sys.stderr)
            failed = True
    if changed or not (root / "corpus-index/archive.json").is_file():
        archive_statistics(root)
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
