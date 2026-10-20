"""Write the documents' dates (`atlas dates`) into the book facts input (`glyph_atlas.book_facts_input`):
the ledger claims they rest on and what each document shows.

    uv run atlas dates --fetch --site SITE_DOCUMENTS.txt
    uv run scripts/export_dates_cloudflare.py work/dates

Each date is an assertion of the ledger (data/ledger.json, `date_<kind>`) with its source as evidence;
`document_dating` is each document's dating on both axes as `glyph_atlas.dates` resolved it. Both
tables are replaced, and the export's time is noted as its stamp. `scripts/atlas3/books.py` loads them:
the claims into the ledger of glyph-atlas-v3, a date no source states any more retracted by its
source, and the dating of each book into `book_dates`.
"""
from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from glyph_atlas import book_facts_input
from glyph_atlas.date_claims import assertion
from glyph_atlas.dates import RESOLVER
from glyph_atlas.review import ledger
from glyph_atlas.schema import DateClaim


def column(value):
    return ledger.canonical(value) if isinstance(value, (dict, list)) else value


def claim_rows(claims: list[DateClaim]) -> list[tuple]:
    """Each claim as the ledger asserts it: id, subject, predicate, value, tier, asserter and its evidence."""
    rows = []
    for claim in claims:
        found = assertion(claim)
        evidence = found["evidence"]
        rows.append((found["id"], found["subject"], found["predicate"], ledger.canonical(found["value"]), found["tier"],
                     found["asserted_by"], evidence["kind"], evidence["ref"], evidence["locator"]))
    return rows


def dating_rows(resolved: list[dict]) -> list[tuple]:
    rows = []
    for document in resolved:
        for axis in ("witness", "composed"):
            if axis in document:
                found = {**document[axis], "document": document["document"], "resolver": RESOLVER}
                rows.append(tuple(column(found.get(c)) for c in book_facts_input.DATING_COLUMNS))
    return rows


def export(source: Path, path: Path | None = None, stamp: str | None = None) -> dict[str, int | str]:
    """The claims and dating of `source` (an `atlas dates` output) in the input; figures of what it holds."""
    read = lambda name: [json.loads(line) for line in (source / name).read_text(encoding="utf-8").splitlines() if line]
    claims = [DateClaim.model_validate(c) for c in read("claims.jsonl")]
    resolved = read("resolved.jsonl")
    stamp = stamp or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    claimed, dated = claim_rows(claims), dating_rows(resolved)
    with book_facts_input.writing(path) as db:
        db.execute("DELETE FROM date_claims")
        db.executemany(f"INSERT OR REPLACE INTO date_claims VALUES({','.join('?' * len(book_facts_input.CLAIM_COLUMNS))})",
                       claimed)
        db.execute("DELETE FROM document_dating")
        db.executemany(f"INSERT OR REPLACE INTO document_dating({','.join(book_facts_input.DATING_COLUMNS)}) "
                       f"VALUES({','.join('?' * len(book_facts_input.DATING_COLUMNS))})", dated)
        for table in ("date_claims", "document_dating"):
            book_facts_input.noted(db, table, stamp)
    return {"claims": len(claims), "documents": len(resolved), "dated_axes": len(dated),
            "slots": len({(r[1], r[2], r[3]) for r in claimed}), "stamp": stamp}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", type=Path, help="the directory `atlas dates` wrote")
    parser.add_argument("--input", type=Path, help="the book facts input to write (default: book-facts-input.sqlite in ATLAS3_DIR)")
    args = parser.parse_args()
    print(json.dumps(export(args.source, args.input), ensure_ascii=False))


if __name__ == "__main__":
    main()
