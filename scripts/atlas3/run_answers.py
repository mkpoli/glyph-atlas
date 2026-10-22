"""The runs' answers in R2 (decision record 0005), written from the local copies by runs.py and publish.py.

- `v3/runs/grid-<n>.json.gz`: Explore's grid of runs of n crops, the GRID most frequent by graphemes,
  each `[graphemes, n, down, forms]`, `forms` counting the written forms it gathers.
- `v3/runs/books/<book>-<n>.json.gz`: one book's grid, each `[graphemes, n, down]`.
- `v3/runs/forms/<n>-<first code point, hex>.json.gz`: for every graphemes of that length and first
  code point, `[forms, [[form, n], ...]]`: how many written forms its runs gather and the FORMS_LISTED most
  frequent, ties in code point order, as SQLite orders text.

A run's written form is the text the publisher holds for it in the local copy's `run_text`, a table of
the local copy alone (runs.py fills it from the live runs, publish.py from each catalogue's). A run with
no text (graphemes '') is counted in none. Every run is written
down the page, so a grid's `down` is its `n`. Objects are written under out/runs-answers/, and
`uploaded.json` there holds the digest of each object as R2 has it, so only those that differ go up.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import sqlite3
from collections import defaultdict

from build import OUT

GRID = 480
FORMS_LISTED = 24
ANSWERS = OUT / "runs-answers"
PREFIX = "v3/runs/"
MANIFEST = ANSWERS / "uploaded.json"
# The local copy's own table of each run's written text, which D1 does not hold: the Worker writes a run
# from its crops' `run_written` rows, and the answers count the texts the publications gave.
RUN_TEXT = """CREATE TABLE IF NOT EXISTS run_text(first INTEGER, size INTEGER, text TEXT NOT NULL,
  PRIMARY KEY(first, size)) WITHOUT ROWID"""


def forms_query(where):
    return ("SELECT r.size, r.graphemes, t.text, count(*) FROM runs r JOIN run_text t ON t.first = r.first AND t.size = r.size "
            f"WHERE r.graphemes <> '' AND {where} GROUP BY 1, 2, 3")


def encode(value):
    return gzip.compress(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode(), mtime=0)


def ranked(rows, n):
    return sorted(rows, key=lambda r: (-r[1], r[0]))[:n]


def shard(size, graphemes):
    return f"{size}-{ord(graphemes[0]):x}"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pending():
    """{folder: [paths]} of the objects whose bytes R2 does not hold yet."""
    held = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}
    found = defaultdict(list)
    for path in sorted(ANSWERS.rglob("*.json.gz")):
        name = str(path.relative_to(ANSWERS))
        if held.get(name) != digest(path):
            found[str(path.parent.relative_to(ANSWERS)).replace(".", "")].append(path)
    return found


def uploaded(paths):
    held = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}
    held.update({str(p.relative_to(ANSWERS)): digest(p) for p in paths})
    MANIFEST.write_text(json.dumps(held, sort_keys=True))


class Answers:
    """Writes answers from the local runs copy, read only; with `texts`, a translation whose `texts`
    table stands in for the copy's `run_text`, for a load that has not written it yet."""

    def __init__(self, texts=None):
        self.runs = sqlite3.connect(f"file:{OUT / 'atlas-runs.sqlite'}?mode=ro", uri=True)
        if texts is not None:
            self.runs.execute("ATTACH ? AS tr", (f"file:{texts}?mode=ro",))
            self.runs.execute("CREATE TEMP VIEW run_text AS SELECT first, size, text FROM tr.texts")

    def close(self):
        """Lets the copies go, so the load and the publisher can write them."""
        self.runs.close()

    def put(self, folder, name, value):
        path = ANSWERS / folder / f"{name}.json.gz" if folder else ANSWERS / f"{name}.json.gz"
        data = encode(value)
        if not path.exists() or path.read_bytes() != data:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

    def forms(self, where, args=()):
        """{(size, graphemes): [(text, n)]} of the runs `where` selects."""
        found = defaultdict(list)
        for size, graphemes, text, n in self.runs.execute(forms_query(where), args):
            found[(size, graphemes)].append((text, n))
        return found

    def shards(self, keys=None):
        """The forms shards: all, or those of `keys` ({(size, first code point)})."""
        if keys is None:
            found = defaultdict(dict)
            for (size, graphemes), rows in self.forms("1").items():
                found[shard(size, graphemes)][graphemes] = [len(rows), [list(r) for r in ranked(rows, FORMS_LISTED)]]
            for name, value in found.items():
                self.put("forms", name, value)
            return
        for size, cp in sorted(keys):
            rows = self.forms("r.size = ? AND r.graphemes >= ? AND r.graphemes < ?", (size, chr(cp), chr(cp + 1)))
            path = ANSWERS / "forms" / f"{size}-{cp:x}.json.gz"
            if not rows and path.exists():
                # No runs are left under it; an empty shard answers as a missing one would.
                self.put("forms", f"{size}-{cp:x}", {})
            elif rows:
                self.put("forms", f"{size}-{cp:x}", {g: [len(r), [list(x) for x in ranked(r, FORMS_LISTED)]] for (_s, g), r in rows.items()})

    def grids(self):
        """Explore's grids, from `run_stats` and the forms counted in the shards, which are written first."""
        for size in (2, 3):
            top = self.runs.execute("SELECT graphemes, n, down FROM run_stats WHERE size = ? AND graphemes <> '' "
                                    f"ORDER BY n DESC, graphemes LIMIT {GRID}", (size,)).fetchall()
            read = {}

            def forms(graphemes, size=size, read=read):
                name = shard(size, graphemes)
                if name not in read:
                    path = ANSWERS / "forms" / f"{name}.json.gz"
                    read[name] = json.loads(gzip.decompress(path.read_bytes())) if path.exists() else {}
                return read[name].get(graphemes, [0])[0]
            self.put("", f"grid-{size}", [[g, n, d, forms(g)] for g, n, d in top])

    def books(self, books=None):
        """Each book's grid of each length: every book's, or those of `books`, which a book with no runs
        left empties."""
        where = "graphemes <> ''" + (f" AND book IN ({','.join('?' * len(books))})" if books is not None else "")
        counted = defaultdict(list)
        for book, size, g, n, down in self.runs.execute(
                f"SELECT book, size, graphemes, count(*), count(*) FROM runs WHERE {where} GROUP BY book, size, graphemes",
                list(books or ())):
            counted[(book, size)].append((g, n, down))
        for book in (books if books is not None else sorted({b for b, _s in counted})):
            for size in (2, 3):
                rows = ranked(counted.get((book, size), []), GRID)
                if rows or (ANSWERS / "books" / f"{book}-{size}.json.gz").exists():
                    self.put("books", f"{book}-{size}", [list(r) for r in rows])
