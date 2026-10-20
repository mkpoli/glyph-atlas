"""Build the new layout (decision record 0005) from the tables pull.py copied out of the live D1.

Writes out/atlas.sqlite (the D1 `atlas` tables), out/atlas-runs.sqlite, out/mirror.sqlite (the
publisher's id allocation: text id -> integer id, page and book keys) and out/pages/*.json.gz.
Run once for the first build; later uploads allocate through mirror.sqlite and never renumber.
"""
import gzip
import json
import os
import shutil
import sqlite3
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# Copies of the live tables and the build output live outside the repository.
HERE = Path(os.environ.get("ATLAS3_DIR", ROOT / "work/atlas-v3"))
OUT = HERE / "out"
# Each database's schema is every file of its migrations folder, in order.
SCHEMA = ROOT / "apps/cloudflare/migrations-atlas"
RUNS_SCHEMA = ROOT / "apps/cloudflare/migrations-atlas-runs"


def schema(folder):
    return "\n".join(path.read_text() for path in sorted(folder.glob("*.sql")))
SITE_SLOT = 1 << 23
PAGE = 1 << 24
CORPUS_PAGE = 256


def src(table):
    db = sqlite3.connect(HERE / f"source-{table}.sqlite")
    db.row_factory = sqlite3.Row
    return db


def shuffle(i):
    """31-bit mix of the id (splitmix64 finaliser), stable across republication."""
    z = (i + 0x9E3779B97F4A7C15) & 0xFFFFFFFFFFFFFFFF
    z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & 0xFFFFFFFFFFFFFFFF
    z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & 0xFFFFFFFFFFFFFFFF
    return (z ^ (z >> 31)) & 0x7FFFFFFF


def char_text(value):
    if value and value.startswith("U+"):
        try:
            return "".join(chr(int(p.removeprefix("U+"), 16)) for p in value.split())
        except ValueError:
            return value
    return value


class Codes:
    """Small dictionaries: one integer per distinct value of a text field."""

    def __init__(self, fixed=None):
        self.codes, self.fixed = {}, fixed or {}

    def __call__(self, field, key):
        if key is None or key == "":
            return None
        table = self.codes.setdefault(field, dict(self.fixed.get(field, {})))
        if key not in table:
            table[key] = max(table.values(), default=0) + 1
        return table[key]


# Record fields a bundle leaves out: those a crop's row or its page holds, those a review or a form
# decision sets (the read applies them), and those the Worker computes from the rest.
ROW_FIELDS = frozenset({"id", "state", "revision", "production", "category", "style", "quiz", "suspect", "page_id",
                        "visual_group", "issue", "char", "code_point", "written_character", "grapheme",
                        "identity_status", "identity_basis", "form_decision", "form_cluster", "crop_version",
                        "crop_editable"})


def bundle_entry(data):
    return {k: v for k, v in data.items() if v is not None and k not in ROW_FIELDS}


def replacement(text_id, redirects, live):
    """The first text id on text_id's redirect chain (its target, that target's target, ...) for which
    `live` holds, passing text ids with no crop and retired crops; None when the chain ends without one.
    Each step checks for a loop before it checks for a crop, so a chain that comes back to text_id is a
    loop even when text_id is live. Raises ValueError naming the loop."""
    chain, at = [text_id], redirects[text_id]
    while True:
        if at in chain:
            raise ValueError(" -> ".join(chain[chain.index(at):] + [at]))
        if live(at):
            return at
        if at not in redirects:
            return None
        chain.append(at)
        at = redirects[at]


def run_form(text, graphemes):
    """How a run is written where that differs from its graphemes; None where it is written as them,
    and for a run with no text."""
    return text if text and graphemes and text != graphemes else None


# A book's grid lists this many runs of each length (`/atlas/ngrams`), most frequent first, ties by
# graphemes in code point order, as SQLite orders text.
BOOK_RUNS = 480


def book_runs(runs, books=None):
    """Each book's BOOK_RUNS most frequent runs of each length, as `run_books` rows, from the runs
    table; only `books` when given."""
    where = "graphemes <> ''" + (f" AND book IN ({','.join('?' * len(books))})" if books is not None else "")
    counted = defaultdict(list)
    for book_, size, g, n, down in runs.execute(
            f"SELECT book, size, graphemes, count(*), sum(vertical) FROM runs WHERE {where} GROUP BY book, size, graphemes",
            list(books or ())):
        counted[(book_, size)].append((g, n, down))
    return [(book_, size, g, n, down) for (book_, size), rows in counted.items()
            for g, n, down in sorted(rows, key=lambda r: (-r[1], r[0]))[:BOOK_RUNS]]


STATE = {"pending": 0, "checked": 1, "flagged": 2}
STYLE = {"cursive": 0, "running": 1, "unassessed": 2, "mixed": 3, "formal": 4, "printed": 5}


def main():
    OUT.mkdir(exist_ok=True)
    if (OUT / "mirror.sqlite").exists() and "--replace" not in sys.argv:
        sys.exit("out/mirror.sqlite holds the published id allocation; a rebuild would renumber every crop. "
                 "Use publish.py for changes, or --replace to start a new layout from scratch.")
    for name in ("atlas.sqlite", "atlas-runs.sqlite", "mirror.sqlite"):
        (OUT / name).unlink(missing_ok=True)
    atlas = sqlite3.connect(OUT / "atlas.sqlite")
    atlas.executescript(schema(SCHEMA))
    mirror = sqlite3.connect(OUT / "mirror.sqlite")
    mirror.executescript("""
      CREATE TABLE ids(text TEXT PRIMARY KEY, id INTEGER NOT NULL UNIQUE) WITHOUT ROWID;
      CREATE TABLE pages(key TEXT PRIMARY KEY, id INTEGER NOT NULL UNIQUE, book INTEGER NOT NULL,
        next_slot INTEGER NOT NULL) WITHOUT ROWID;
      CREATE TABLE books(key TEXT PRIMARY KEY, id INTEGER NOT NULL UNIQUE) WITHOUT ROWID;
      CREATE TABLE codes(field TEXT, key TEXT, code INTEGER, PRIMARY KEY(field, key)) WITHOUT ROWID;
      CREATE TABLE book_years(key TEXT PRIMARY KEY, year INTEGER) WITHOUT ROWID;
      CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
    """)
    # The build id ties this allocation to the databases loaded from it (load.sh writes it to D1).
    build_id = time.strftime("%Y%m%dT%H%M%S")
    mirror.execute("INSERT INTO meta VALUES('build', ?)", (build_id,))
    code = Codes({"state": STATE, "style": STYLE})

    # Graphemes: every character the site knows, with its family head.
    graphemes, heads = {}, {}
    for r in src("characters").execute("SELECT code_point, character, head FROM characters"):
        graphemes.setdefault(r["character"], len(graphemes) + 1)
        if r["head"]:
            heads[r["character"]] = char_text(r["head"])

    def grapheme(text):
        text = char_text(text)
        if not text:
            return None
        if text not in graphemes:
            graphemes[text] = len(graphemes) + 1
        return graphemes[text]

    # Books and their witness year.
    books, years = {}, {}
    for r in src("document_dating").execute("SELECT document, start FROM document_dating WHERE axis='witness'"):
        years[r["document"]] = r["start"]

    def book(key):
        key = key or "unknown"
        if key not in books:
            books[key] = len(books) + 1
        return books[key]

    # Productions: stable ids; the tree is the path, parent is the prefix.
    productions = {}

    def production(key):
        key = key or "unknown"
        if key not in productions:
            if "/" in key:
                production(key.rsplit("/", 1)[0])
            productions[key] = len(productions) + 1
        return productions[key]

    pages, crops, ids = {}, [], {}
    page_facts = {r["id"]: (r["document"], r["data"]) for r in src("pages").execute("SELECT id, document, data FROM pages")}
    media = src("media")
    media.execute("CREATE INDEX IF NOT EXISTS media_key ON media(key)")

    def page(key, document):
        if key not in pages:
            pages[key] = {"id": len(pages) + 1, "book": book(document), "next": 0, "crops": []}
        return pages[key]

    def add(text_id, pg, kind, row, bundle):
        slot = pg["next"]
        pg["next"] += 1
        cid = pg["id"] * PAGE + slot
        ids[text_id] = cid
        crops.append((cid, kind, pg["book"], *row, shuffle(cid)))
        pg["crops"].append({"slot": slot, "id": text_id, **bundle})
        return cid

    # Collected crops: their page from the record, in id order within the page.
    units = src("units")
    by_page = defaultdict(list)
    corpus_overlay = {}
    for r in units.execute("SELECT * FROM units WHERE _rowid IN (SELECT max(_rowid) FROM units GROUP BY id) AND origin <> 'retired' ORDER BY id"):
        if r["origin"] == "corpus":
            corpus_overlay[r["id"]] = r
            continue
        data = json.loads(r["data"])
        by_page[data.get("page_id") or f"book:{r['document']}"].append((r, data))
    for key in sorted(by_page):
        for r, data in by_page[key]:
            pg = page(key, r["document"])
            year = years.get(r["document"])
            flags = 1 if data.get("suspect") else 0
            row = (grapheme(r["character"]), None, None, code("visual_group", r["visual_group"]),
                   production(r["production"]), code("category", r["category"]), code("style", r["style"]),
                   code("state", r["state"]), r["quiz"], year, 0, r["revision"], flags)
            bundle = bundle_entry(data)
            for field in ("image", "context_image"):
                image_key = (data.get(field) or "").rsplit("/", 1)[-1].split(".")[0]
                found = image_key and media.execute("SELECT object, offset, size FROM media WHERE key=?", (image_key,)).fetchone()
                if found:
                    bundle[field + "_place"] = list(found)
            add(r["id"], pg, 0, row, bundle)

    # Corpus glyphs: synthetic pages of CORPUS_PAGE per source and book, in id order.
    corpus = src("corpus_units")
    groups = defaultdict(list)
    for r in corpus.execute("SELECT * FROM corpus_units WHERE _rowid IN (SELECT max(_rowid) FROM corpus_units GROUP BY id) ORDER BY id"):
        groups[(r["id"].split("|")[0].split(":")[0], r["document"] or "")].append(r)
    for (source, document), rows in sorted(groups.items()):
        for start in range(0, len(rows), CORPUS_PAGE):
            pg = page(f"corpus:{source}:{document}:{start // CORPUS_PAGE}", document or source)
            for r in rows[start:start + CORPUS_PAGE]:
                o = corpus_overlay.get(r["id"])
                state = code("state", o["state"]) if o else STATE["pending"]
                quiz = o["quiz"] if o else 1
                row = (grapheme((o["character"] if o else None) or r["character"]), None, None,
                       code("visual_group", r["visual_group"]), production(r["production"]), None,
                       code("style", r["style"]), state, quiz, years.get(r["document"]), 0,
                       o["revision"] if o else 0, 0)
                add(r["id"], pg, 1, row, {"label": r["label"], "record_place": [r["object"], r["offset"], r["size"]]})

    print(f"crops {len(crops)} pages {len(pages)} books {len(books)} graphemes {len(graphemes)}", flush=True)
    atlas.executemany("INSERT INTO crops(id,kind,book,char,form,cluster,visual_group,production,category,style,"
                      "state,quiz,year,cut,revision,flags,shuffle) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", crops)
    atlas.executemany("INSERT INTO pages(id,key,book,rev,next_slot) VALUES(?,?,?,1,?)",
                      [(p["id"], k, p["book"], p["next"]) for k, p in pages.items()])
    atlas.executemany("INSERT INTO books(id,key) VALUES(?,?)", [(v, k) for k, v in books.items()])
    atlas.executemany("INSERT INTO graphemes(id,text,head) VALUES(?,?,?)",
                      [(v, k, graphemes.get(heads[k]) if k in heads else None) for k, v in graphemes.items()])
    atlas.executemany("INSERT INTO productions(id,key,parent) VALUES(?,?,?)",
                      [(v, k, productions.get(k.rsplit("/", 1)[0]) if "/" in k else None) for k, v in productions.items()])
    atlas.executemany("INSERT INTO codes(field,code,key) VALUES(?,?,?)",
                      [(f, c, k) for f, t in code.codes.items() for k, c in t.items()])
    # The Worker reads the build id here to find the build's R2 objects (`v3/votes/<build>/`).
    atlas.execute("INSERT INTO codes(field,code,key) VALUES('build',0,?)", (build_id,))
    aliases = src("aliases")
    atlas.executemany("INSERT OR IGNORE INTO names(text,code_point,rank) VALUES(?,?,?)",
                      [(r["query"], int(r["code_point"].split()[0].removeprefix("U+"), 16), r["rank"])
                       # A name for a sequence of code points has no single code point to file under.
                       for r in aliases.execute("SELECT * FROM aliases") if r["code_point"].startswith("U+") and " " not in r["code_point"]])
    atlas.execute("INSERT INTO generation VALUES(0, 1)")
    atlas.commit()

    mirror.executemany("INSERT INTO ids VALUES(?,?)", ids.items())
    mirror.executemany("INSERT INTO pages VALUES(?,?,?,?)", [(k, p["id"], p["book"], p["next"]) for k, p in pages.items()])
    mirror.executemany("INSERT INTO books VALUES(?,?)", books.items())
    mirror.executemany("INSERT INTO book_years VALUES(?,?)", years.items())
    mirror.executemany("INSERT INTO codes VALUES(?,?,?)", [(f, k, c) for f, t in code.codes.items() for k, c in t.items()])
    mirror.commit()

    # Page bundles.
    shutil.rmtree(OUT / "pages", ignore_errors=True)
    # A new layout starts with no vote objects; its objects go under its own `v3/votes/<build>/`.
    shutil.rmtree(OUT / "votes", ignore_errors=True)
    (OUT / "pages").mkdir()
    for key, p in pages.items():
        facts = page_facts.get(key)
        body = {"key": key, "book": p["book"], "rev": 1, "page": json.loads(facts[1]) if facts else None, "crops": p["crops"]}
        (OUT / "pages" / f"{p['id']}-1.json.gz").write_bytes(gzip.compress(json.dumps(body, ensure_ascii=False).encode()))
    print("bundles written", flush=True)

    # Runs.
    runs = sqlite3.connect(OUT / "atlas-runs.sqlite")
    runs.executescript(schema(RUNS_SCHEMA))
    ngrams = src("unit_ngrams")
    out, stats, forms, missing = [], defaultdict(lambda: [0, 0]), defaultdict(lambda: [0, 0]), 0
    for r in ngrams.execute("SELECT * FROM unit_ngrams"):
        a, b = ids.get(r["first"]), ids.get(r["second"])
        c = ids.get(r["third"]) if r["third"] else None
        if a is None or b is None or (r["third"] and c is None):
            missing += 1
            continue
        g, form = r["graphemes"] or "", run_form(r["text"], r["graphemes"])
        out.append((r["size"], g, r["hand_order"], shuffle(a), a, b, c, book(r["document"]), form, r["vertical"]))
        for counts in (stats[(r["size"], g)], *([forms[(r["size"], g, form)]] if form is not None else [])):
            counts[0] += 1
            counts[1] += r["vertical"]
    runs.executemany("INSERT OR IGNORE INTO runs(size,graphemes,hand,shuffle,first,second,third,book,form,vertical) "
                     "VALUES(?,?,?,?,?,?,?,?,?,?)", out)
    runs.executemany("INSERT INTO run_stats(size,graphemes,n,down) VALUES(?,?,?,?)", [(k[0], k[1], v[0], v[1]) for k, v in stats.items()])
    runs.executemany("INSERT INTO run_forms(size,graphemes,form,n,down) VALUES(?,?,?,?,?)", [(*k, *v) for k, v in forms.items()])
    runs.executemany("INSERT INTO run_books(book,size,graphemes,n,down) VALUES(?,?,?,?,?)", book_runs(runs))
    runs.commit()
    print(f"runs {len(out)} stats {len(stats)} forms {len(forms)} skipped {missing}", flush=True)
    if missing:
        sys.exit(f"{missing} runs name crops the build does not have")
    # Runs can name books no crop row did; both copies of the book list take them.
    atlas.executemany("INSERT OR IGNORE INTO books(id,key) VALUES(?,?)", [(v, k) for k, v in books.items()])
    atlas.commit()
    mirror.executemany("INSERT OR IGNORE INTO books VALUES(?,?)", books.items())
    mirror.commit()
    verify(atlas, mirror, units, corpus)


def verify(atlas, mirror, units, corpus):
    """Every live crop is in the new layout exactly once, under the id its page and slot give."""
    want = (units.execute("SELECT count(DISTINCT id) FROM units WHERE origin='local'").fetchone()[0]
            + corpus.execute("SELECT count(DISTINCT id) FROM corpus_units").fetchone()[0])
    got = atlas.execute("SELECT count(*) FROM crops").fetchone()[0]
    checks = {
        "crop count matches the source": want == got,
        "every crop's page exists": not atlas.execute(
            "SELECT 1 FROM crops c LEFT JOIN pages p ON p.id = c.id / 16777216 WHERE p.id IS NULL LIMIT 1").fetchone(),
        "slots below each page's next_slot": not atlas.execute(
            "SELECT 1 FROM crops c JOIN pages p ON p.id = c.id / 16777216 WHERE c.id % 16777216 >= p.next_slot LIMIT 1").fetchone(),
        "every crop's book exists": not atlas.execute(
            "SELECT 1 FROM crops c LEFT JOIN books b ON b.id = c.book WHERE b.id IS NULL LIMIT 1").fetchone(),
        "labelled crops have a grapheme": atlas.execute("SELECT count(*) FROM crops WHERE char IS NULL").fetchone()[0]
            <= units.execute("SELECT count(*) FROM units WHERE origin='local' AND (character IS NULL OR character='')").fetchone()[0]
            + corpus.execute("SELECT count(*) FROM corpus_units WHERE character IS NULL OR character=''").fetchone()[0],
    }
    pages_want = units.execute("SELECT count(DISTINCT coalesce(json_extract(data,'$.page_id'), 'book:'||document)) "
                               "FROM units WHERE origin='local'").fetchone()[0]
    pages_got = atlas.execute("SELECT count(DISTINCT c.id / 16777216) FROM crops c WHERE kind = 0").fetchone()[0]
    checks["collected crops keep their record's page"] = pages_want == pages_got
    # Exactly: every collected crop's integer id names the page its record names.
    page_of = dict(mirror.execute("SELECT id, key FROM pages"))
    id_of = dict(mirror.execute("SELECT text, id FROM ids"))
    wrong = sum(1 for r in units.execute("SELECT id, coalesce(json_extract(data,'$.page_id'), 'book:'||document) "
                                          "FROM units WHERE origin='local' GROUP BY id")
                if page_of.get(id_of.get(r[0], -1) // 16777216) != r[1])
    checks["every collected crop's id names its record's page"] = wrong == 0
    checks["every source id has exactly one crop"] = len(id_of) == got == len(set(id_of.values()))
    print(f"source {want} crops on {pages_want} pages, built {got} on {pages_got}")
    for name, ok in checks.items():
        print(("ok   " if ok else "FAIL ") + name)
    if not all(checks.values()):
        sys.exit(1)


if __name__ == "__main__":
    main()
