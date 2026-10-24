"""Write a catalogue that gives each published page where it can be opened at its source.

    python3 page_sources.py <catalogue.sqlite> <dataset>...

Each page's published record is read from the local copy (out/atlas.sqlite and its bundles in
out/pages), its book from the `documents.parquet` of the datasets named, and its book's manifest from
the manifest cache (`glyph_atlas.page_sources`). A page whose record gains or changes a non-null `manifest`,
`canvas`, `canvas_index` or `catalogue_url` is written to the catalogue's `pages` table as the
exporter writes it, which `publish.py` then publishes as a new revision of the page; the rest of the
record stays as published. A page whose book no dataset holds is left out and counted.
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

import pyarrow.parquet as pq

from glyph_atlas.page_sources import catalogue_urls, page_sources

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(os.environ.get("ATLAS3_DIR", ROOT / "work/atlas-v3")) / "out"
FIELDS = ("manifest", "canvas", "canvas_index", "catalogue_url")


def documents(datasets: list[Path]) -> dict[str, tuple[dict, dict]]:
    """Each document's `source_refs` and `meta`, a later dataset's over an earlier one's."""
    found = {}
    for dataset in datasets:
        for row in pq.read_table(dataset / "documents.parquet", columns=["id", "source_refs", "meta"]).to_pylist():
            found[row["id"]] = tuple(json.loads(row[k]) if isinstance(row[k], str) else (row[k] or {}) for k in ("source_refs", "meta"))
    return found


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("catalogue", type=Path)
    parser.add_argument("datasets", type=Path, nargs="+")
    args = parser.parse_args()
    if args.catalogue.exists():
        sys.exit(f"{args.catalogue} exists; name a new catalogue")
    books = documents(args.datasets)
    atlas = sqlite3.connect(f"file:{OUT / 'atlas.sqlite'}?mode=ro", uri=True)
    records = {}
    for pid, rev in atlas.execute("SELECT id, rev FROM pages WHERE rev > 0 ORDER BY id"):
        page = json.loads(gzip.decompress((OUT / "pages" / f"{pid}-{rev}.json.gz").read_bytes())).get("page")
        if page:
            records[page["id"]] = page
    catalogue = catalogue_urls((d, *refs) for d, refs in books.items())
    counts, rows = Counter(), []
    for key, page in records.items():
        if page["document"] not in books:
            counts["book in no dataset"] += 1
            continue
        found = page_sources(books[page["document"]][0], page.get("canvas"), page.get("service"), catalogue.get(page["document"]))
        record = {**page, **found}
        for field in FIELDS:
            counts[f"{field} given"] += record[field] is not None
        # A field the record lacks reads as null, so a page that gains nothing gets no new revision.
        if all(page.get(field) == record[field] for field in FIELDS):
            counts["unchanged"] += 1
            continue
        rows.append((key, page["document"], json.dumps(record, ensure_ascii=False, separators=(",", ":"))))
    db = sqlite3.connect(args.catalogue)
    db.execute("CREATE TABLE pages(id TEXT PRIMARY KEY, document TEXT NOT NULL, data TEXT NOT NULL)")
    db.executemany("INSERT INTO pages VALUES (?,?,?)", rows)
    db.commit()
    print(json.dumps({"pages": len(records), "written": len(rows), **counts}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
