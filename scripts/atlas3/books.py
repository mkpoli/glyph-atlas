"""Move the book facts and site-wide values into glyph-atlas-v3 (`migrations-atlas/0005_books.sql`).

    ATLAS3_DIR=work/atlas-v3 python3 scripts/atlas3/books.py          # translate and write the parts
    ATLAS3_DIR=work/atlas-v3 python3 scripts/atlas3/books.py --send   # and load them

The copies `source-document_dating`, `source-corpus_documents`, `source-document_characters` and
`source-metadata` (pull.py) are translated into `out/books.sqlite`, every book named by its integer id
in the local copy and every published occurrence by its crop through `out/mirror.sqlite`, which is only
read:

- `books.title`: the title on the book's first page bundle, else the source its first crop there names,
  else the one its corpus publication gave;
- `book_dates`: both dating axes of each book, less the claim list and the export stamp (the claims
  stay in `assertions`);
- `book_characters`: each occurrence by its integer crop id, marked when the site shows it with its
  review state (a collected crop, or a corpus glyph a round showed, skipped or reviewed); a character
  whose unit the mirror lacks stops the run, and so does a character list before the history is loaded;
- `site`: the publication's `collection`, `corpus_index`, `review_epoch`, `variant_sources` and
  `word_sources`.

Facts about a document that is not a book of the new layout are left out with a count. Only what
differs from the local copy is sent, in parts named by their content and confirmed by their stamps in
`imports`, then replayed on the local copy; a rerun after a fresh copy sends the difference. Every part
removes each row before writing it, so it applies again harmlessly, and `--send` first replays the
parts of earlier runs whose stamps D1 holds and the local copy lacks (one read of `imports`). A dry run
(the default) writes the parts and the figures to `out/books-report.txt` and sends nothing. `--send`
also applies migration 0005 (stamp `migration:0005`) where the local copy lacks it.

Not copied, since a publication derives them from the crops: the corpus glyphs per book
(`corpus_documents.n`) and the homepage sample (`corpus_gallery`), which are answers of `answers.py`.
"""

from __future__ import annotations

import argparse
import fcntl
import gzip
import hashlib
import json
import sqlite3
import sys

from build import HERE, OUT
from load_history import GROUP, SCHEMA, built, deliver, row_statements, stamped
from publish import applied, lit, query, send

SITE_KEYS = ("collection", "corpus_index", "review_epoch", "variant_sources", "word_sources")
DATE_COLS = ("book", "axis", "kind", "start", "end", "precision", "qualifier", "uncertain", "text", "label", "status",
             "calendar", "conversion", "source")
CHARACTER_COLS = ("book", "ord", "crop", "atlas", "data")
# Each table books.py owns, with its key.
TABLES = {"book_dates": (DATE_COLS, ("book", "axis")), "book_characters": (CHARACTER_COLS, ("book", "ord")),
          "site": (("key", "value"), ("key",))}
SOURCES = {"document_dating": ("document", *DATE_COLS[1:]), "corpus_documents": ("document", "title"),
           "document_characters": ("document", "ord", "unit", "data"), "metadata": ("key", "value")}


def source(table):
    path = HERE / f"source-{table}.sqlite"
    if not path.exists():
        sys.exit(f"{path.name} is missing; pull.py {table} copies it")
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    have = {r[1] for r in db.execute(f'PRAGMA table_info("{table}")')}
    if lacking := [c for c in SOURCES[table] if c not in have]:
        sys.exit(f"{path.name} lacks {', '.join(lacking)}; pull.py --fresh {table} copies it again")
    return db


def source_title(value):
    """A crop's `source`: the title itself, or an object (or its JSON) that holds one."""
    if isinstance(value, str) and value.startswith("{"):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return value
    return value.get("title") if isinstance(value, dict) else value


def titles(atlas, corpus):
    """Each book's title: its first page bundle's, else the source of the first of that bundle's crops
    that names one, else its corpus publication's."""
    found, first = {}, {}
    for pid, book, rev in atlas.execute("SELECT id, book, rev FROM pages WHERE rev > 0 ORDER BY id"):
        first.setdefault(book, (pid, rev))
    for book, (pid, rev) in first.items():
        path = OUT / "pages" / f"{pid}-{rev}.json.gz"
        if not path.exists():
            continue
        bundle = json.loads(gzip.decompress(path.read_bytes()))
        if title := (bundle.get("page") or {}).get("title"):
            found[book] = ("pages", title)
        elif title := next((t for c in bundle.get("crops", []) if (t := source_title(c.get("source")))), None):
            found[book] = ("crops", title)
    for book, title in corpus.items():
        if title and book not in found:
            found[book] = ("corpus", title)
    return found


def shows(atlas, crop):
    """Whether the site shows a crop with its review state, as the old site's `units` held it: every
    collected crop, and a corpus glyph once a round showed, skipped or reviewed it."""
    kind = atlas.execute("SELECT kind FROM crops WHERE id=?", (crop,)).fetchone()
    if kind is None or kind[0] not in (0, 1):
        return False
    return kind[0] == 0 or atlas.execute(
        "SELECT EXISTS(SELECT 1 FROM seen WHERE crop=?1) OR EXISTS(SELECT 1 FROM skips WHERE crop=?1) "
        "OR EXISTS(SELECT 1 FROM events WHERE crop=?1)", (crop,)).fetchone()[0] == 1


def translate(atlas, mirror):
    """out/books.sqlite from the copies, and how many rows were left out or kept without a crop, by reason."""
    path = OUT / "books.sqlite"
    path.unlink(missing_ok=True)
    out = sqlite3.connect(path)
    out.executescript("CREATE TABLE books(id INTEGER PRIMARY KEY, title TEXT NOT NULL, origin TEXT NOT NULL);\n"
                      + "\n".join(f"CREATE TABLE {t}({','.join(cols)}, PRIMARY KEY({','.join(key)})) WITHOUT ROWID;"
                                  for t, (cols, key) in TABLES.items()))
    books = dict(atlas.execute("SELECT key, id FROM books"))
    left = {}

    def count(reason):
        left[reason] = left.get(reason, 0) + 1

    def book(document, what):
        found = books.get(document)
        if found is None:
            count(f"{what} of documents that are not books, left out")
        return found

    corpus = {}
    for document, title in source("corpus_documents").execute("SELECT document, title FROM corpus_documents"):
        if (b := book(document, "corpus titles")) is not None:
            corpus[b] = title
    out.executemany("INSERT INTO books VALUES(?,?,?)", [(b, t, o) for b, (o, t) in sorted(titles(atlas, corpus).items())])

    rows = []
    for r in source("document_dating").execute(f"SELECT document,{','.join(DATE_COLS[1:])} FROM document_dating"):
        if (b := book(r[0], "dates")) is not None:
            rows.append((b, *r[1:6], r[6], int(bool(r[7])), *r[8:]))
    out.executemany(f"INSERT INTO book_dates VALUES({','.join('?' * len(DATE_COLS))})", rows)

    characters = source("document_characters").execute(
        "SELECT document, ord, unit, data FROM document_characters ORDER BY document, ord").fetchall()
    if characters and not atlas.execute("SELECT 1 FROM imports WHERE stamp LIKE 'history-%'").fetchone():
        sys.exit("the history is not loaded; load_history.py first, since its rounds decide which corpus glyphs count")
    rows, unknown = [], []
    for document, ord_, unit, data in characters:
        if (b := book(document, "characters")) is None:
            continue
        crop = mirror.execute("SELECT id FROM ids WHERE text=?", (unit,)).fetchone()
        if crop is None:
            unknown.append(unit)
            continue
        shown = int(shows(atlas, crop[0]))
        if not shown:
            count("characters the site shows without a review state")
        rows.append((b, ord_, crop[0], shown, data))
    if unknown:
        sys.exit(f"{len(unknown)} characters name no crop of the mirror: {unknown[:5]}")
    out.executemany("INSERT INTO book_characters VALUES(?,?,?,?,?)", rows)

    meta = dict(source("metadata").execute("SELECT key, value FROM metadata"))
    out.executemany("INSERT INTO site VALUES(?,?)", [(k, meta[k]) for k in SITE_KEYS if k in meta])
    for k in SITE_KEYS:
        if k not in meta:
            count(f"site value {k} absent from metadata")
    out.commit()
    return out, left


def has(db, table):
    return db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone() is not None


def remove(table, cols, key, r):
    return f'DELETE FROM "{table}" WHERE {" AND ".join(f'"{c}" = {lit(r[cols.index(c)])}' for c in key)};'


def statements(books, atlas):
    """Groups of statements that turn the local copy's book facts into the translation's; a row's
    statements stay in one group. Also the rows each table writes."""
    groups, written = [], {}

    def add(table, rows):
        for i in range(0, len(rows), GROUP):
            groups.append([s for r in rows[i:i + GROUP] for s in r])
        written[table] = sum(len(r) for r in rows)

    columns = {r[1] for r in atlas.execute("PRAGMA table_info(books)")}
    now = dict(atlas.execute("SELECT id, title FROM books")) if "title" in columns else \
        {r[0]: None for r in atlas.execute("SELECT id FROM books")}
    wanted = dict(books.execute("SELECT id, title FROM books"))
    add("books", [[f"UPDATE books SET title = {lit(wanted.get(b))} WHERE id = {b};"]
                  for b in sorted(now) if now[b] != wanted.get(b)])
    for table, (cols, key) in TABLES.items():
        names = ",".join(f'"{c}"' for c in cols)
        k = [cols.index(c) for c in key]
        held = {tuple(r[i] for i in k): r for r in atlas.execute(f"SELECT {names} FROM {table}")} if has(atlas, table) else {}
        new = {tuple(r[i] for i in k): r for r in books.execute(f"SELECT {names} FROM {table}")}
        rows = [[remove(table, cols, key, r)] for kk, r in sorted(held.items()) if kk not in new]
        # Each row is removed before it is written, so a part applies again without a conflict.
        rows += [[remove(table, cols, key, r), *row_statements(table, list(cols), list(key), r)]
                 for kk, r in sorted(new.items()) if held.get(kk) != r]
        add(table, rows)
    return groups, written


def figures(books, written, left):
    """Rows and bytes per table of the translation, and the rows a load writes."""
    lines = ["table\trows\tbytes\tstatements"]
    for table in ("books", *TABLES):
        cols = ["title"] if table == "books" else list(TABLES[table][0])
        size = " + ".join(f"coalesce(length(CAST(\"{c}\" AS BLOB)), 0)" for c in cols)
        n, b = books.execute(f"SELECT count(*), coalesce(sum({size}), 0) FROM {table}").fetchone()
        lines.append(f"{'books.title' if table == 'books' else table}\t{n}\t{b}\t{written.get(table, 0)}")
    lines.append(f"statements to send (each writes a row or removes one): {sum(written.values())}")
    for reason, n in sorted(left.items()):
        lines.append(f"{n} {reason}")
    return "\n".join(lines) + "\n"


def catch_up(atlas):
    """Replay on the local copy the parts of earlier runs that D1 holds and the local copy lacks, as a
    run stopped between the two leaves them, so the difference is taken from what D1 holds."""
    local = {r[0] for r in atlas.execute("SELECT stamp FROM imports WHERE stamp LIKE 'books-%'")}
    for r in query("glyph-atlas-v3", "SELECT stamp FROM imports WHERE stamp LIKE 'books-%' ORDER BY at, stamp"):
        if r["stamp"] in local:
            continue
        run, part = r["stamp"].split(".", 1)
        path = OUT / "books-load" / run / f"{part}.sql"
        if not path.exists():
            sys.exit(f"glyph-atlas-v3 holds {r['stamp']}, whose part {path} is gone; the local copy cannot follow it")
        atlas.executescript("BEGIN;\n" + path.read_text() + "COMMIT;\n")
        print(f"replayed {r['stamp']} from D1's imports", flush=True)


def migrate(atlas):
    """Migration 0005 on D1 and the local copy, once; a layout built with it already has its tables."""
    if has(atlas, "book_dates"):
        return
    path = OUT / "books-load" / "0005.sql"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(stamped((SCHEMA / "0005_books.sql").read_text(), "migration:0005"))
    if not applied("glyph-atlas-v3", "migration:0005") and not send("glyph-atlas-v3", path, "migration:0005"):
        sys.exit("migration 0005 did not apply; rerun")
    atlas.executescript("BEGIN;\n" + path.read_text() + "COMMIT;\n")
    print("migration 0005 applied", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--send", action="store_true", help="load the parts into glyph-atlas-v3")
    args = parser.parse_args()
    lock = open(OUT / "mirror.lock", "w")  # noqa: SIM115 -- held until the process exits
    fcntl.flock(lock, fcntl.LOCK_EX)
    mirror = sqlite3.connect(f"file:{OUT / 'mirror.sqlite'}?mode=ro", uri=True)
    if has(mirror, "pending") and mirror.execute("SELECT 1 FROM pending").fetchone():
        sys.exit("a publish is unfinished; finish it with publish.py first")
    atlas = sqlite3.connect(OUT / "atlas.sqlite")
    if args.send:
        build = mirror.execute("SELECT value FROM meta WHERE key='build'").fetchone()[0]
        if not applied("glyph-atlas-v3", f"build:{build}"):
            sys.exit(f"glyph-atlas-v3 was not loaded from build {build}; the local copy does not describe it")
        migrate(atlas)
        catch_up(atlas)
    books, left = translate(atlas, mirror)
    groups, written = statements(books, atlas)
    report = figures(books, written, left)
    (OUT / "books-report.txt").write_text(report)
    print(report, end="", flush=True)
    if not groups:
        print("the local copy already holds these facts; nothing to send", flush=True)
        return
    digest = hashlib.sha256("\n".join(s for g in groups for s in g).encode()).hexdigest()[:12]
    run = OUT / "books-load" / f"books-{digest}"
    n = built(run, "rows", groups)
    print(f"{n if n is not None else 'existing'} parts -> {run}", flush=True)
    if not args.send:
        print("dry run: nothing sent", flush=True)
        return
    deliver(run, atlas)
    print("book facts loaded", flush=True)


if __name__ == "__main__":
    main()
