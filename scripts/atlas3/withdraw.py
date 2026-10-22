"""Write the catalogue that takes withdrawn documents (`data/vocab/withdrawn.yaml`) off the site.

    python3 scripts/atlas3/withdraw.py <withdrawal.sqlite>
    python3 scripts/atlas3/publish.py <withdrawal.sqlite> --dry-run
    python3 scripts/atlas3/publish.py <withdrawal.sqlite>

A document's crops are every crop numbered on its book's pages in out/mirror.sqlite: its collected
crops and its corpus glyphs, live or retired. Each is written as a `units` row with
`origin='withdrawn'` at the revision the local copy (out/atlas.sqlite) holds. The publish deletes
the live ones in D1 under that revision and reads the deletion back: a crop reviewed on the site
since stays, is listed as refused, and needs a fresh catalogue. For each crop D1 no longer holds it
leaves the entry its id and cut alone and takes the images only that entry named out of the image
key map. Reviews and claims stay with the crop's number. Both files are only read here; a document the
layout never had gives no row.
"""
import argparse
import sqlite3
import sys
from pathlib import Path

from build import OUT, PAGE

from glyph_atlas import withdrawn

COLUMNS = ("id", "origin", "character", "visual_group", "production", "category", "state", "revision", "quiz",
           "document", "style", "data")


def rows(documents, mirror, atlas):
    """The withdrawn `units` rows of the crops on each document's pages, by text id."""
    out = []
    for document in sorted(documents):
        book = mirror.execute("SELECT id FROM books WHERE key=?", (document,)).fetchone()
        if book is None:
            continue
        for pid, next_slot in mirror.execute("SELECT id, next_slot FROM pages WHERE book=? ORDER BY id", (book[0],)).fetchall():
            for text, cid in mirror.execute("SELECT text, id FROM ids WHERE id>=? AND id<? ORDER BY id",
                                            (pid * PAGE, pid * PAGE + next_slot)).fetchall():
                row = atlas.execute("SELECT revision FROM crops WHERE id=?", (cid,)).fetchone()
                out.append((text, "withdrawn", None, None, None, None, None, row[0] if row else 0, None, document, None, "{}"))
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.output.exists():
        sys.exit(f"{args.output} exists; a withdrawal is written into a new file")
    mirror = sqlite3.connect(f"file:{OUT / 'mirror.sqlite'}?mode=ro", uri=True)
    atlas = sqlite3.connect(f"file:{OUT / 'atlas.sqlite'}?mode=ro", uri=True)
    found = rows(withdrawn.documents(), mirror, atlas)
    db = sqlite3.connect(args.output)
    db.execute(f"CREATE TABLE units({','.join(COLUMNS)})")
    db.executemany(f"INSERT INTO units VALUES({','.join('?' * len(COLUMNS))})", found)
    db.commit()
    books = len({r[9] for r in found})
    print(f"{len(found)} crops of {books} of {len(withdrawn.documents())} withdrawn documents -> {args.output}", flush=True)


if __name__ == "__main__":
    main()
