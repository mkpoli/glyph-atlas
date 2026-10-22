"""Archive the old site's reviews for the review export (decision: the export serves each review as the
old site's `/atlas/reviews` did, so receipts the offline store already holds stay valid).

    ATLAS3_DIR=work/atlas-v3 python3 scripts/atlas3/review_archive.py --snapshots SNAPSHOTS.sqlite [--send]

Reads the frozen copies `source-events.sqlite` and `source-units.sqlite`, and `units(id, snapshot)` for
the collected and retired crops those events name: the publication record each crop had on the old site,
which the copies lack. Each review becomes the record the old export gave it, the event and its evidence
string byte for byte (`review_evidence.legacy`); an undo, or a review of a crop the old site no longer
held, is kept hidden. The records go by page to `out/review-legacy/<page>.json.gz` under
`legacy:<old event id>`, the id glyph-atlas-v3's `events.legacy` keeps; `--send` uploads them to
`v3/review-legacy/`. An event on a crop the mirror lacks has no row in the new layout and is counted.
Nothing is written to D1.
"""
from __future__ import annotations

import argparse
import gzip
import json
import sqlite3
import sys
from collections import Counter, defaultdict

import review_evidence
from build import HERE, OUT, PAGE

PREFIX = "v3/review-legacy/"


def write(folder, page, reviews):
    raw = json.dumps({"version": 1, "reviews": reviews}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    path = folder / f"{page}.json.gz"
    path.write_bytes(gzip.compress(raw, mtime=0))
    return path


def archive(snapshots):
    """The records by page, and the counts left out."""
    mirror = sqlite3.connect(f"file:{OUT / 'mirror.sqlite'}?mode=ro", uri=True)
    units = sqlite3.connect(f"file:{HERE / 'source-units.sqlite'}?mode=ro", uri=True)
    units.row_factory = sqlite3.Row
    events = sqlite3.connect(f"file:{HERE / 'source-events.sqlite'}?mode=ro", uri=True)
    events.row_factory = sqlite3.Row
    extracted = sqlite3.connect(f"file:{snapshots}?mode=ro", uri=True)
    published = dict(extracted.execute("SELECT id, snapshot FROM units"))
    pages, left = defaultdict(dict), Counter()
    rows = events.execute("SELECT * FROM events WHERE kind IN ('review','undo') ORDER BY rowid").fetchall()
    # Each named unit's latest row, read in one pass over the copy.
    named, latest = {r["target"] for r in rows}, {}
    for u in units.execute("SELECT * FROM units ORDER BY _rowid"):
        if u["id"] in named:
            latest[u["id"]] = u
    for r in rows:
        found = mirror.execute("SELECT id FROM ids WHERE text=?", (r["target"],)).fetchone()
        if found is None:
            left["crop the mirror lacks"] += 1
            continue
        unit = dict(latest[r["target"]]) if r["target"] in latest else None
        if unit and unit["origin"] != "corpus":
            if r["target"] not in published:
                sys.exit(f"{r['target']}: no publication record in {snapshots}")
            unit["snapshot"] = published[r["target"]]
        pages[found[0] // PAGE][f"legacy:{r['id']}"] = review_evidence.legacy(r, unit)
    return pages, left


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--snapshots", required=True, help="SQLite file with units(id, snapshot) from the old site")
    parser.add_argument("--send", action="store_true", help="upload the archives to v3/review-legacy/")
    args = parser.parse_args()
    pages, left = archive(args.snapshots)
    folder = OUT / "review-legacy"
    folder.mkdir(parents=True, exist_ok=True)
    files = [write(folder, page, reviews) for page, reviews in sorted(pages.items())]
    reviews = sum(len(r) for r in pages.values())
    hidden = sum(1 for r in pages.values() for v in r.values() if v.get("legacy_hidden"))
    size = sum(f.stat().st_size for f in files)
    print(f"{reviews} reviews ({hidden} hidden) in {len(files)} page archives, {size} bytes gzipped -> {folder}", flush=True)
    for why, n in left.items():
        print(f"left out ({n}): {why}", flush=True)
    if args.send:
        from publish import upload
        upload(files, PREFIX)
        print(f"uploaded to {PREFIX}", flush=True)


if __name__ == "__main__":
    main()
