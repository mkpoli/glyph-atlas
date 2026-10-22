"""Move the book facts, the date claims and the site-wide values into glyph-atlas-v3 (`migrations-atlas/0005_books.sql`).

    ATLAS3_DIR=work/atlas-v3 python3 scripts/atlas3/books.py          # translate and write the parts
    ATLAS3_DIR=work/atlas-v3 python3 scripts/atlas3/books.py --send   # and load them

The book facts input (`glyph_atlas.book_facts_input`: the dates export's claims and dating, and the
characters another site lists per book) and the copies `source-corpus_documents` and `source-metadata`
(pull.py) are translated into `out/books.sqlite`, every book named by its integer id in the local copy
and every published occurrence by its crop through `out/mirror.sqlite`, which is only read:

- `books.title`: the title on the book's first page bundle, else the source its first crop there names,
  else the one its corpus publication gave;
- `book_dates`: both dating axes of each book, less the claim list. The input keeps the dating of
  every document the export read, so a document that becomes a book later takes its dates on the
  next run;
- `book_characters`: each occurrence by its integer crop id; one whose unit the site never had (the
  mirror lacks it) keeps its text id in `unit`, as the old site listed it. Only the books of documents
  the input lists (`character_documents`) are replaced; the others keep the rows the site holds. Whether the site shows it with its review state is derived when a route reads it
  (the query is in 0005), since every round changes it;
- `site`: the publication's `collection`, `corpus_index` and `review_epoch`; the citations
  `variant_sources` and `word_sources` are `reference.py`'s.

The dates export's claims go to the ledger as the old export wrote them, against the ledger the
local copy holds (the history, `load_history.py`): a claim that stands nowhere is asserted under its
id, or as `<id>@<stamp>` when an earlier one was retracted, with its source as evidence; a date a
source asserted and the export no longer states is retracted by that source at the stamp; and every
slot that gains a claim or loses one is resolved again in glyph-atlas-v3, by the ledger's resolver
(data/ledger.json) as the Worker runs it, from the rows D1 holds, so a verdict given on the site counts.
A claim's stamp is the export's time.

Facts about a document that is not a book of the new layout are left out with a count. Only what
differs from the local copy is sent, in parts named by their content and confirmed by their stamps in
`imports`, then replayed on the local copy; a rerun after a fresh copy sends the difference. Every part
removes each row before writing it, so its data rows apply again harmlessly; a whole part sent again
fails on its `imports` stamp, which D1 already holds, and D1 rolls the part back. `--send` first
replays the parts of earlier runs whose stamps D1 holds and the local copy lacks (one read of `imports`). A dry run
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
from collections import defaultdict

from build import HERE, OUT
from load_history import GROUP, SCHEMA, built, deliver, row_statements, stamped
from publish import applied, lit, query, send

from glyph_atlas import book_facts_input
from glyph_atlas.dates import RESOLVER
from glyph_atlas.review import ledger

SITE_KEYS = ("collection", "corpus_index", "review_epoch")
DATE_COLS = ("book", "axis", "kind", "start", "end", "precision", "qualifier", "uncertain", "text", "label", "status",
             "calendar", "conversion", "source")
CHARACTER_COLS = ("book", "ord", "crop", "unit", "data")
# Each table books.py writes, with its key and the rows of it books.py owns.
TABLES = {"book_dates": (DATE_COLS, ("book", "axis"), ""), "book_characters": (CHARACTER_COLS, ("book", "ord"), ""),
          "site": (("key", "value"), ("key",), f" WHERE key IN ({','.join(repr(k) for k in SITE_KEYS)})")}
SOURCES = {"corpus_documents": ("document", "title"), "metadata": ("key", "value")}
# The ledger tables a dates export writes.
LEDGER = ("assertions", "assertion_evidence", "assertion_actions")
DATE = "substr(a.predicate, 1, 5) = 'date_'"
METHOD = "atlas dates"
INPUT = None


def source(table):
    path = HERE / f"source-{table}.sqlite"
    if not path.exists():
        sys.exit(f"{path.name} is missing; pull.py {table} copies it")
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    have = {r[1] for r in db.execute(f'PRAGMA table_info("{table}")')}
    if lacking := [c for c in SOURCES[table] if c not in have]:
        sys.exit(f"{path.name} lacks {', '.join(lacking)}; pull.py --fresh {table} copies it again")
    return db


def given(table):
    """The book facts input, once a generator has written `table` into it."""
    global INPUT
    if INPUT is None:
        INPUT = book_facts_input.reading()
    if INPUT.execute("SELECT 1 FROM written WHERE name=?", (table,)).fetchone() is None:
        sys.exit(f"no generator has written {table} into the book facts input; "
                 + ("export_document_characters.py" if table == "document_characters" else "export_dates_cloudflare.py")
                 + " writes it")
    return INPUT


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


def translate(atlas, mirror):
    """out/books.sqlite from the copies, and how many rows were left out, by reason."""
    path = OUT / "books.sqlite"
    path.unlink(missing_ok=True)
    out = sqlite3.connect(path)
    out.executescript("CREATE TABLE books(id INTEGER PRIMARY KEY, title TEXT NOT NULL, origin TEXT NOT NULL);\n"
                      + "\n".join(f"CREATE TABLE {t}({','.join(cols)}, PRIMARY KEY({','.join(key)})) WITHOUT ROWID;"
                                  for t, (cols, key, _) in TABLES.items()))
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
    for r in given("document_dating").execute(f"SELECT document,{','.join(DATE_COLS[1:])} FROM document_dating"):
        if (b := book(r[0], "dates")) is not None:
            rows.append((b, *r[1:6], r[6], int(bool(r[7])), *r[8:]))
    out.executemany(f"INSERT INTO book_dates VALUES({','.join('?' * len(DATE_COLS))})", rows)

    characters = given("document_characters").execute(
        "SELECT document, ord, unit, data FROM document_characters ORDER BY document, ord").fetchall()
    rows = []
    for document, ord_, unit, data in characters:
        if (b := book(document, "characters")) is None:
            continue
        crop = mirror.execute("SELECT id FROM ids WHERE text=?", (unit,)).fetchone()
        if crop is None:
            # The old site listed it with its text id and no review state; so does the new one.
            count("characters naming a unit the site never had, kept by their text id")
        rows.append((b, ord_, crop[0] if crop else None, None if crop else unit, data))
    out.executemany("INSERT INTO book_characters VALUES(?,?,?,?,?)", rows)
    listed = given("document_characters").execute(
        "SELECT document FROM character_documents UNION SELECT document FROM document_characters")
    out.execute("CREATE TABLE character_books(book INTEGER PRIMARY KEY)")
    out.executemany("INSERT OR IGNORE INTO character_books VALUES(?)", [(books[d],) for (d,) in listed if d in books])

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
    statements stay in one group. Also, per table, the rows removed, added and changed, and the rows
    written: one per statement that finds its row, so the DELETE ahead of a new row counts nothing."""
    groups, changes = [], {}

    def add(table, rows, removed=0, added=0):
        for i in range(0, len(rows), GROUP):
            groups.append([s for r in rows[i:i + GROUP] for s in r])
        changes[table] = {"removed": removed, "added": added, "changed": len(rows) - removed - added,
                          "written": sum(len(r) for r in rows) - added}

    columns = {r[1] for r in atlas.execute("PRAGMA table_info(books)")}
    now = dict(atlas.execute("SELECT id, title FROM books")) if "title" in columns else \
        {r[0]: None for r in atlas.execute("SELECT id FROM books")}
    wanted = dict(books.execute("SELECT id, title FROM books"))
    add("books", [[f"UPDATE books SET title = {lit(wanted.get(b))} WHERE id = {b};"]
                  for b in sorted(now) if now[b] != wanted.get(b)])
    # A book whose document the input does not list keeps its characters: the input may hold only some.
    listed = {r[0] for r in books.execute("SELECT book FROM character_books")}
    for table, (cols, key, owned) in TABLES.items():
        names = ",".join(f'"{c}"' for c in cols)
        k = [cols.index(c) for c in key]
        held = {tuple(r[i] for i in k): r for r in atlas.execute(f"SELECT {names} FROM {table}{owned}")} if has(atlas, table) else {}
        if table == "book_characters":
            held = {kk: r for kk, r in held.items() if r[0] in listed}
        new = {tuple(r[i] for i in k): r for r in books.execute(f"SELECT {names} FROM {table}")}
        rows = [[remove(table, cols, key, r)] for kk, r in sorted(held.items()) if kk not in new]
        removed = len(rows)
        # Each row is removed before it is written, so its statements apply again without a conflict.
        rows += [[remove(table, cols, key, r), *row_statements(table, list(cols), list(key), r)]
                 for kk, r in sorted(new.items()) if held.get(kk) != r]
        add(table, rows, removed, sum(kk not in held for kk, r in new.items()))
    return groups, changes


def ignored(table, cols, r):
    """A ledger row, which is never changed once written: a part sent again leaves it as it is."""
    return f'INSERT OR IGNORE INTO "{table}"({",".join(cols)}) VALUES({",".join(lit(v) for v in r)});'


def base(identity):
    """The claim an assertion states: its id, less the `@<stamp>` of a claim asserted again."""
    return identity.split("@", 1)[0]


# The ledger's resolver (data/ledger.json) as the Worker runs it on glyph-atlas-v3 (`apps/cloudflare/src/
# ledger.ts`): crop evidence is live while it names its crop at the crop's effective cut, and a slot names
# its crop and that cut. A date slot's subject is a book, so both are NULL there.
def effective_cut(crop):
    return (f"(SELECT c.cut+EXISTS(SELECT 1 FROM reviews rv WHERE rv.crop=c.id AND rv.x IS NOT NULL) "
            f"FROM crops c WHERE c.id={crop})")


# The columns the resolver selects, in its order.
RESOLVED = "subject,predicate,scope,slot,status,object,value,members,supporting,claims,crop,cut,resolver,at"
SLOT_CROP = ("(SELECT a.crop FROM assertions a WHERE a.subject=k.subject AND a.predicate=k.predicate AND a.scope=k.scope "
             "AND a.slot=k.slot AND a.crop IS NOT NULL LIMIT 1)")
CROP_PARTS = (("e.ref IS NOT (SELECT c.version FROM crop_now c WHERE c.unit=a.subject)",
               f"(e.crop IS NOT a.crop OR e.cut IS NOT {effective_cut('a.crop')})"),
              ("(SELECT c.version FROM crop_now c WHERE c.unit=k.subject) AS crop_version,",
               f"{SLOT_CROP} AS crop, {effective_cut(SLOT_CROP)} AS cut,"))


def v3_resolve():
    body = ledger.RESOLVE_BODY
    for old, new in CROP_PARTS:
        if body.count(old) != 1:
            sys.exit(f"data/ledger.json's resolver changed: {old}")
        body = body.replace(old, new)
    if "crop_now" in body:
        sys.exit("data/ledger.json's resolver names crop_now where it is not replaced")
    return body


def resolve_statements(slots):
    """The statements that resolve `slots` again in the database they run on, from its own rows: each
    slot's `current_claims` row removed, then written where a claim of it is live (`resolveSlots`)."""
    clear = ("DELETE FROM current_claims WHERE (subject,predicate,scope,slot) IN (SELECT json_extract(value,'$[0]'),"
             "json_extract(value,'$[1]'),json_extract(value,'$[2]'),json_extract(value,'$[3]') FROM json_each(?1))")
    write = f"INSERT INTO current_claims({RESOLVED}) WITH {v3_resolve()}"
    out = []
    for i in range(0, len(slots), ledger.KEYS_PER_STATEMENT):
        bound = lit(ledger.canonical([list(k) for k in slots[i:i + ledger.KEYS_PER_STATEMENT]]))
        out += [" ".join(line.strip() for line in sql.splitlines()).replace("?1", bound) + ";" for sql in (clear, write)]
    return out


def date_ledger(atlas):
    """The ledger rows the dates export adds to the local copy's, and the resolution of each slot they
    change, as groups of statements; also the rows each table gains or changes. None when the export
    states no claims."""
    claims = given("date_claims").execute(
        f"SELECT {','.join(book_facts_input.CLAIM_COLUMNS)} FROM date_claims ORDER BY id").fetchall()
    if not claims:
        return None
    stamp = given("date_claims").execute("SELECT at FROM written WHERE name='date_claims'").fetchone()[0]
    if not has(atlas, "assertions"):
        return "the local copy holds no ledger yet; load_history.py --send loads it"
    held = {r[0]: r for r in atlas.execute(f"SELECT a.id, a.subject, a.predicate, a.scope, a.slot, a.asserted_by FROM assertions a WHERE {DATE}")}
    retracted = {r[0] for r in atlas.execute(
        f"SELECT x.assertion FROM assertion_actions x JOIN assertions a ON a.id = x.assertion WHERE x.action = 'retract' AND {DATE}")}
    standing = defaultdict(list)
    for identity in sorted(held):
        if identity not in retracted:
            standing[base(identity)].append(identity)
    evidence = {(r[0], r[1], r[2]) for r in atlas.execute(
        f"SELECT e.assertion, e.kind, e.ref FROM assertion_evidence e JOIN assertions a ON a.id = e.assertion WHERE {DATE}")}
    v3 = {t: [r[1] for r in atlas.execute(f"PRAGMA table_info({t})")] for t in LEDGER}
    new = {t: [] for t in LEDGER}
    # Each changed slot's rows; evidence added to a claim that already stands changes no slot.
    by_slot, loose = defaultdict(list), []
    stated = set()
    for identity, subject, predicate, value, tier, by, kind, ref, locator in claims:
        stated.add(identity)
        live = standing[identity]
        if not live:
            # A retraction is never undone: a claim that returns is asserted anew under the export's stamp.
            fresh = f"{identity}@{stamp}" if identity in held else identity
            # An id this stamp asserted and a later run retracted stays retracted.
            live = [] if fresh in held else [fresh]
            if live:
                row = (fresh, None, subject, None, None, predicate, "", value, None, value, None, tier,
                       by, stamp, None, None, METHOD, RESOLVER, None)
                new["assertions"].append(row)
                by_slot[(subject, predicate, "", value)].append(("assertions", row))
        for a in live:
            if (a, kind, ref) not in evidence:
                row = (a, kind, ref, None, None, locator)
                new["assertion_evidence"].append(row)
                (by_slot[(subject, predicate, "", value)] if a not in held else loose).append(("assertion_evidence", row))
    for identity, (_, subject, predicate, scope, slot, by) in sorted(held.items()):
        if identity not in retracted and by.startswith("source:") and base(identity) not in stated:
            row = (f"dt-retract:{identity}:{stamp}", None, identity, "retract", by, stamp,
                   f"no source states it at export {stamp}")
            new["assertion_actions"].append(row)
            by_slot[(subject, predicate, scope, slot)].append(("assertion_actions", row))
    slots = sorted(by_slot)
    groups = [[ignored(t, v3[t], r) for t, r in loose[i:i + GROUP]] for i in range(0, len(loose), GROUP)]
    # Each slot is resolved in D1 from the rows D1 holds, which may include verdicts the local copy
    # lacks; the local copy replays the same statements over its own rows. A slot's rows and its
    # resolution share a group, so no part lands its rows without resolving it.
    order = {t: n for n, t in enumerate(LEDGER)}
    for i in range(0, len(slots), ledger.KEYS_PER_STATEMENT):
        chunk = slots[i:i + ledger.KEYS_PER_STATEMENT]
        rows = sorted((r for k in chunk for r in by_slot[k]), key=lambda r: order[r[0]])
        groups.append([ignored(t, v3[t], r) for t, r in rows] + resolve_statements(chunk))
    changes = {t: {"added": len(new[t]), "written": len(new[t])} for t in LEDGER}
    # At most each slot's row removed and written again.
    changes["current_claims"] = {"changed": len(slots), "written": 2 * len(slots)}
    return groups, changes


def figures(books, changes, left):
    """Rows and bytes per table of the translation, and what a load changes and writes."""
    kinds = ("removed", "added", "changed", "written")
    lines = ["table\trows\tbytes\t" + "\t".join(kinds)]
    for table in ("books", *TABLES):
        cols = ["title"] if table == "books" else list(TABLES[table][0])
        size = " + ".join(f"coalesce(length(CAST(\"{c}\" AS BLOB)), 0)" for c in cols)
        n, b = books.execute(f"SELECT count(*), coalesce(sum({size}), 0) FROM {table}").fetchone()
        got = changes.get(table, {})
        lines.append(f"{'books.title' if table == 'books' else table}\t{n}\t{b}\t"
                     + "\t".join(str(got.get(k, 0)) for k in kinds))
    for table in (*LEDGER, "current_claims"):
        if table in changes:
            got = changes[table]
            lines.append(f"{table} (date claims)\t\t\t" + "\t".join(str(got.get(k, 0)) for k in kinds))
    lines.append(f"rows written: {sum(c['written'] for c in changes.values())} (a removed row 1; a new row its INSERT "
                 "and the UPDATE per piece of a long value; a changed row its DELETE and those)")
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
    groups, changes = statements(books, atlas)
    claims = date_ledger(atlas)
    if isinstance(claims, str):
        if args.send:
            sys.exit(f"the dates export states claims, but {claims}")
        left[f"date claims of the export, left out: {claims}"] = 1
    elif claims:
        groups += claims[0]
        changes.update(claims[1])
    report = figures(books, changes, left)
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
