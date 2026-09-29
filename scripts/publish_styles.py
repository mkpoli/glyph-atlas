"""Write the confirmed document styles (`data/vocab/document-styles.yaml`) onto the crops the site holds.

A publication resolves each crop's style as it writes the crop (`glyph_atlas.style`); crops already in
D1 keep the style they were published with. This writes the SQL that gives them their document's:

- a local crop by its `document` column;
- a corpus glyph by the range of ids that start with its document's id and a colon; the `units` row
  of one a round or review has named follows it (migration 0035's `corpus_style` trigger). The range
  is used only for a document whose glyphs, in the corpora under `work/`, all have ids of that form,
  and whose range holds no other document's glyph.

A confirmed document found in no corpus under `work/` is refused: its crops could not be checked. A
crop or page with a style of its own would be overwritten, so the script refuses to run when a crop
or page of a confirmed document states one, or when a review store named with `--review` holds a
style a reviewer gave one crop; such crops reach the site through a publication. A document
confirmed as `mixed` gives its crops no style (`unassessed`).

The file ends by stamping `metadata.units_refreshed_at`, which the Worker's cached listings are keyed by.

    python scripts/publish_styles.py OUTPUT.sql [--review work/DATASET ...]
"""
from __future__ import annotations

import argparse
import sqlite3
from collections import defaultdict
from pathlib import Path

import pyarrow.dataset as ds
from refresh_published_units import VERSION_BUMP, quote

from glyph_atlas import style
from glyph_atlas.corpus import sources


class Refused(ValueError):
    """Writing a document's style would be wrong for some of its crops."""


def upper(prefix: str) -> str:
    """The first string after every string that starts with `prefix`, which ends in ':'."""
    return prefix[:-1] + ";"


def corpus_ranges(documents: set[str], root="work") -> dict[str, str]:
    """The id prefix of each confirmed document's glyphs, checked against every unit corpus."""
    held, stray, own = defaultdict(int), defaultdict(set), {}
    prefixes = {document: document + ":" for document in documents}
    for corpus in sources.discover(root):
        path = corpus.table("units")
        if path is None:
            continue
        dataset = ds.dataset(path, format="parquet")
        styled = "style" in dataset.schema.names
        table = dataset.to_table(columns=["id", "document_id", *(["style"] if styled else [])])
        values = table["style"].to_pylist() if styled else [None] * table.num_rows
        for identity, document, value in zip(table["id"].to_pylist(), table["document_id"].to_pylist(), values, strict=True):
            if document in prefixes:
                if not identity.startswith(prefixes[document]):
                    stray[document].add(identity)
                if value not in (None, style.UNASSESSED):
                    own[identity] = value
                held[document] += 1
            # The id may still fall in a confirmed document's range; a range is `document:`, so the
            # candidate documents are the id's own prefixes that end before a colon.
            head = identity
            while ":" in head:
                head = head.rsplit(":", 1)[0]
                if head in prefixes and head != document:
                    stray[head].add(identity)
    if stray:
        raise Refused("; ".join(f"{document}: {len(ids)} glyphs outside or inside its id range, e.g. {min(ids)}"
                                for document, ids in sorted(stray.items())))
    if own:
        raise Refused(f"{len(own)} crops state a style of their own, e.g. {min(own)}; publish them instead")
    if missing := sorted(document for document in documents if not held[document]):
        raise Refused(f"{len(missing)} confirmed documents are in no corpus under {root}, e.g. {missing[0]}")
    return dict(prefixes)


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


def statements(confirmed: dict[str, dict], ranges: dict[str, str]) -> list[str]:
    out = []
    for document in sorted(confirmed):
        value = style.resolve_values(None, None, document, None)[0]
        out.append(f"UPDATE units SET style={quote(value)} WHERE origin='local' AND document={quote(document)};")
        if document in ranges:
            low, high = quote(ranges[document]), quote(upper(ranges[document]))
            out.append(f"UPDATE corpus_units SET style={quote(value)} WHERE id>={low} AND id<{high};")
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("output", type=Path)
    parser.add_argument("--review", type=Path, action="append", default=[],
                        help="a dataset directory whose review.sqlite may hold styles given to single crops")
    args = parser.parse_args()
    confirmed = style.confirmed()
    documents = set(confirmed)
    if crops := unit_styles(args.review):
        raise Refused(f"{len(crops)} crops have a style of their own, e.g. {crops[0]}; publish them instead")
    if pages := page_styles(documents):
        raise Refused(f"{len(pages)} pages state a style of their own, e.g. {min(pages)}")
    ranges = corpus_ranges(documents)
    lines = statements(confirmed, ranges)
    args.output.write_text("\n".join(lines) + "\n" + VERSION_BUMP, encoding="utf-8")
    print({"documents": len(documents), "corpus_ranges": len(ranges), "statements": len(lines)})


if __name__ == "__main__":
    main()
