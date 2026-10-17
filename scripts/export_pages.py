"""Write the pages of the crops review datasets hold (0078) as SQL parts D1 can import.

    python scripts/export_pages.py OUT DATASET[@CATALOGUE] ...

A publication writes the pages of the crops it publishes (`export_cloudflare.page_rows`); this writes them
for crops published before it did. A dataset is read in place and read-only: its tables and its review
database, which records each unit's page, line and place on it. With `@CATALOGUE`, an export's
`catalogue.sqlite`, a page holds the crops that export published; without, the dataset's active crops
whose book the site may show. A crop the site does not hold is left out when the page is read. The rows
are written as a publication writes them (`page_upsert`), so a page cut in two datasets keeps the crops of
both, and a row the site already holds as written is left alone. OUT/part-NN.sql stays under D1's upload
size; OUT/pages.json counts the rows and their bytes.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from cloudflare_schema import page_upsert, schema
from export_cloudflare import page_rows

from glyph_atlas import tables, withdrawn
from glyph_atlas.corpus.api import PROXYABLE

PART_BYTES = 40 * 1024 * 1024
STATEMENT_BYTES = 95 * 1024


def records(dataset: Path, conn: sqlite3.Connection, name: str) -> dict:
    """A dataset's table by id, with the records imported into its review database, as `Store` reads it."""
    path = dataset / f"{name}.parquet"
    found = {row.id: row for row in (tables.read(path, tables.TABLES[name]) if path.exists() else [])}
    for identity, data in conn.execute("SELECT id, data FROM imported_records WHERE table_name = ? ORDER BY id", (name,)):
        found.setdefault(identity, tables.TABLES[name].model_validate_json(data))
    return found


def dataset_pages(dataset: Path, catalogue: Path | None, db: sqlite3.Connection) -> None:
    """Add the pages of `dataset` to `db`, merging those another dataset already put there."""
    with sqlite3.connect(f"file:{dataset / 'review.sqlite'}?mode=ro", uri=True) as conn:
        pages, documents = records(dataset, conn, "pages"), records(dataset, conn, "documents")
        units = {identity: SimpleNamespace(id=identity, document_id=document, page_id=page, line_id=line, seq=seq)
                 for identity, document, page, line, seq in conn.execute(
                     "SELECT id, document_id, page_id, line_id, seq FROM units WHERE active = 1 AND page_id IS NOT NULL")}
    if catalogue:
        with sqlite3.connect(f"file:{catalogue}?mode=ro", uri=True) as source:
            published = [i for i, in source.execute("SELECT id FROM units WHERE origin='local'")]
    else:
        gone = withdrawn.documents()

        def shown(unit):
            page = pages.get(unit.page_id)
            doc = documents.get(unit.document_id or (page.document_id if page else None))
            return (doc is not None and doc.id not in gone and doc.image_rights is not None
                    and str(doc.image_rights.licence) in PROXYABLE)

        published = [identity for identity, unit in units.items() if shown(unit)]
    scratch = sqlite3.connect(":memory:")
    schema(scratch)
    page_rows(scratch, sorted(published), units, pages, documents)
    for row in scratch.execute("SELECT id,document,data FROM pages"):
        db.execute(page_upsert(*row))
    db.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("out", type=Path)
    parser.add_argument("datasets", nargs="+", help="a dataset directory, or one and an export's catalogue as DATASET@CATALOGUE")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    db = sqlite3.connect(":memory:")
    schema(db)
    for spec in args.datasets:
        dataset, _, catalogue = spec.partition("@")
        dataset_pages(Path(dataset), Path(catalogue) if catalogue else None, db)
        print(json.dumps({"dataset": spec, "pages": db.execute("SELECT count(*) FROM pages").fetchone()[0]}), flush=True)
    parts, part, size, largest, total = [], [], 0, 0, 0

    def flush():
        nonlocal part, size
        if part:
            path = args.out / f"part-{len(parts) + 1:02d}.sql"
            path.write_text("".join(part), encoding="utf-8")
            parts.append(path.name)
            part, size = [], 0

    for row in db.execute("SELECT id,document,data FROM pages ORDER BY id"):
        statement = page_upsert(*row) + "\n"
        length = len(statement.encode())
        if length > STATEMENT_BYTES:
            raise SystemExit(f"page {row[0]} is {length} bytes as a statement, over D1's limit")
        if size + length > PART_BYTES:
            flush()
        part.append(statement)
        size += length
        largest, total = max(largest, length), total + len(row[2].encode())
    flush()
    count = db.execute("SELECT count(*) FROM pages").fetchone()[0]
    crops = db.execute("SELECT sum(json_array_length(data,'$.crops')) FROM pages").fetchone()[0] or 0
    summary = {"pages": count, "crops": crops, "data_bytes": total, "largest_statement": largest, "parts": parts}
    (args.out / "pages.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
