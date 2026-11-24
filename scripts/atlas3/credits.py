"""Write the catalogue that gives the pages and crops the site holds the credit a reader should see.

    ATLAS3_DIR=work/atlas-v3 python3 scripts/atlas3/credits.py <credits.sqlite>
    python3 scripts/atlas3/publish.py <credits.sqlite> --dry-run
    python3 scripts/atlas3/publish.py <credits.sqlite>

An export writes a credit as `glyph_atlas.credit_line` gives it; pages and crops published before keep
the one they were published with. The catalogue's `credits` holds each credit of the published bundles
(`out/pages`) that the rule writes otherwise, with what it gives; the publish puts it in their place. A
corpus glyph's credit is in its record pack and changes with a publication of its records.
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import sqlite3
import sys
from collections import Counter
from pathlib import Path

from glyph_atlas.credit_line import credit_line

OUT = Path(os.environ.get("ATLAS3_DIR", Path(__file__).resolve().parents[2] / "work/atlas-v3")) / "out"


def published(atlas: sqlite3.Connection) -> Counter:
    """Each credit the published bundles carry, and how many pages and crops carry it."""
    found = Counter()
    for pid, rev in atlas.execute("SELECT id, rev FROM pages WHERE rev > 0 ORDER BY id"):
        bundle = json.loads(gzip.decompress((OUT / "pages" / f"{pid}-{rev}.json.gz").read_bytes()))
        for record in ((bundle.get("page") or {}), *bundle["crops"]):
            if record.get("attribution"):
                found[record["attribution"]] += 1
    return found


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("output", type=Path)
    args = parser.parse_args(argv)
    if args.output.exists():
        sys.exit(f"{args.output} exists; the credits are written into a new file")
    found = published(sqlite3.connect(f"file:{OUT / 'atlas.sqlite'}?mode=ro", uri=True))
    rows = [(a, credit_line(a)) for a in sorted(found) if credit_line(a) != a]
    db = sqlite3.connect(args.output)
    db.execute("CREATE TABLE credits(attribution TEXT PRIMARY KEY, credit TEXT)")
    db.executemany("INSERT INTO credits VALUES(?, ?)", rows)
    db.commit()
    print(f"{len(rows)} credits of {sum(found[a] for a, _ in rows)} pages and crops -> {args.output}", flush=True)


if __name__ == "__main__":
    main()
