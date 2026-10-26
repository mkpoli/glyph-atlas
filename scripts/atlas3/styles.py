"""Write the catalogue that gives the crops the site holds their document's confirmed style
(`data/vocab/document-styles.yaml`).

    python3 scripts/atlas3/styles.py <styles.sqlite> [--review work/DATASET ...]  (default: every work/*/review.sqlite)
    python3 scripts/atlas3/publish.py <styles.sqlite> --dry-run
    python3 scripts/atlas3/publish.py <styles.sqlite> --approve-styles N

A publication resolves each crop's style as it writes the crop (`glyph_atlas.style`); crops already
published keep the style they were published with. A document confirmed as `mixed` gives its crops
no style (`unassessed`).

The catalogue's `book_styles` holds each confirmed document's style, which the publish gives every
crop of the book whose style differs. Its dry run counts the crop rows and index entries the send
writes, and the send runs only with `--approve-styles` naming that count of crops.

A crop or page with a style of its own would be overwritten, so the script refuses to run when a
glyph or page of a confirmed document in the corpora under `work/` states one, when the layout's
local copy (out/atlas.sqlite) holds a review event that gave one of its crops a style or a review
whose style is not the one it was shown with, or when a review store named with `--review` holds a
style a reviewer gave one crop; such crops reach the site through a publication. In D1 the publish
also passes over a crop with such an event. A confirmed document found in no corpus is refused too:
its crops could not be checked.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

import pyarrow.dataset as ds

from glyph_atlas import style
from glyph_atlas.corpus import sources


class Refused(ValueError):
    """Writing a document's style would be wrong for some of its crops."""


def corpus_styles(documents: set[str], root="work") -> None:
    """Refuse when a glyph of a confirmed document states a style of its own, or a document is in no
    unit corpus."""
    held, own = defaultdict(int), {}
    for corpus in sources.discover(root):
        path = corpus.table("units")
        if path is None:
            continue
        dataset = ds.dataset(path, format="parquet")
        styled = "style" in dataset.schema.names
        table = dataset.to_table(columns=["id", "document_id", *(["style"] if styled else [])])
        values = table["style"].to_pylist() if styled else [None] * table.num_rows
        for identity, document, value in zip(table["id"].to_pylist(), table["document_id"].to_pylist(), values, strict=True):
            if document in documents:
                if value not in (None, style.UNASSESSED):
                    own[identity] = value
                held[document] += 1
    if own:
        raise Refused(f"{len(own)} crops state a style of their own, e.g. {min(own)}; publish them instead")
    if missing := sorted(document for document in documents if not held[document]):
        raise Refused(f"{len(missing)} confirmed documents are in no corpus under {root}, e.g. {missing[0]}")


def page_styles(documents: set[str], root="work") -> dict[str, str]:
    """A page of a confirmed document that states a style of its own, by page id."""
    found = {}
    for corpus in sources.discover(root):
        path = corpus.table("pages")
        if path is None:
            continue
        dataset = ds.dataset(path, format="parquet")
        if "style" not in dataset.schema.names:
            continue
        table = dataset.to_table(columns=["id", "document_id", "style"])
        for page, document, value in zip(*(table[c].to_pylist() for c in ("id", "document_id", "style")), strict=True):
            if document in documents and value not in (None, style.UNASSESSED):
                found[page] = value
    return found


def unit_styles(reviews: list[Path]) -> list[str]:
    """The crops a reviewer gave a style of their own, in the review stores named."""
    found = []
    for review in reviews:
        with sqlite3.connect(f"file:{review / 'review.sqlite'}?mode=ro", uri=True) as db:
            found += [row[0] for row in db.execute(
                "SELECT DISTINCT target_id FROM events WHERE target_type='unit' AND field='style'")]
    return found


def book_styles(confirmed: dict[str, dict]) -> list[tuple[str, str]]:
    """Each confirmed document with the style its crops take."""
    return [(document, style.resolve_values(None, None, document, None)[0]) for document in sorted(confirmed)]


def layout_styles(documents: set[str], mirror, atlas) -> list[int]:
    """Crops of the confirmed documents a review gave a style of their own in the layout: a latest
    style event that names a style (publish.py OWN_STYLE), or a review whose style differs from the one
    the crop was shown with."""
    books = [row[0] for document in sorted(documents)
             if (row := mirror.execute("SELECT id FROM books WHERE key=?", (document,)).fetchone())]
    found = set()
    for i in range(0, len(books), 500):
        marks = ",".join("?" * len(books[i:i + 500]))
        found.update(r[0] for r in atlas.execute(
            f"SELECT e.crop FROM events e JOIN crops c ON c.id=e.crop WHERE c.book IN ({marks}) AND e.kind=9 "
            f"AND e.style IS NOT NULL AND NOT EXISTS (SELECT 1 FROM events l WHERE l.crop=e.crop AND l.kind=9 "
            f"AND l.revision>e.revision)",
            books[i:i + 500]))
        found.update(r[0] for r in atlas.execute(
            f"SELECT r.crop FROM reviews r JOIN crops c ON c.id=r.crop WHERE r.style IS NOT r.base_style AND c.book IN ({marks})",
            books[i:i + 500]))
    return sorted(found)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("output", type=Path, help="the catalogue to write")
    parser.add_argument("--review", type=Path, action="append", default=[],
                        help="a dataset directory whose review.sqlite may hold styles given to single crops; "
                             "by default every work/*/review.sqlite")
    args = parser.parse_args()
    if args.output.exists():
        sys.exit(f"{args.output} exists; the styles are written into a new file")
    confirmed = style.confirmed()
    documents = set(confirmed)
    reviews = args.review or sorted(path.parent for path in Path("work").glob("*/review.sqlite"))
    if crops := unit_styles(reviews):
        raise Refused(f"{len(crops)} crops have a style of their own, e.g. {crops[0]}; publish them instead")
    if pages := page_styles(documents):
        raise Refused(f"{len(pages)} pages state a style of their own, e.g. {min(pages)}")
    corpus_styles(documents)
    from build import OUT
    own = layout_styles(documents, sqlite3.connect(f"file:{OUT / 'mirror.sqlite'}?mode=ro", uri=True),
                        sqlite3.connect(f"file:{OUT / 'atlas.sqlite'}?mode=ro", uri=True))
    if own:
        raise Refused(f"{len(own)} crops of the layout have a style a review gave them, e.g. crop {own[0]}; publish them instead")
    db = sqlite3.connect(args.output)
    db.execute("CREATE TABLE book_styles(document TEXT PRIMARY KEY, style TEXT NOT NULL)")
    db.executemany("INSERT INTO book_styles VALUES(?,?)", book_styles(confirmed))
    db.commit()
    print({"documents": len(documents)})


if __name__ == "__main__":
    main()
