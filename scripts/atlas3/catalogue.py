"""Write the catalogue the cutover publishes: the live tables as pull.py copied them.

    ATLAS3_DIR=work/atlas-v3 python3 scripts/atlas3/catalogue.py [--parts N]

`publish.py` takes a catalogue in the exporters' format; at the cutover it is the live database itself,
from its copies `source-<table>.sqlite`: `units` (collected crops, the corpus review overlay and retired
crops), `corpus_units`, `media`, `unit_ngrams`, `pages`, `document_dating` and `unit_redirects`. Each
table keeps the live columns, less the copy's `_rowid`. A copy read by rowid holds a row twice when the
live row was rewritten during the copy; the copy at the higher rowid is the row as it stands, so only it
is kept. The catalogue is written to `catalogue.sqlite` beside the copies, through a `.building` file.

    python3 scripts/atlas3/publish.py work/atlas-v3/catalogue.sqlite --dry-run

A publish holds its whole catalogue in memory; the live database's (2.1M crops, 5.3M images, 2.3M
runs) outgrows a 16 GB machine. `--parts N` also writes `catalogue-<k>.sqlite`, N catalogues of whole
books, balanced by their crops, glyphs and runs, published one after another. A book's crops, corpus
glyphs (with their review overlay), runs, pages and dating go together; every part carries every
redirect, and the images its collected crops name. A row with no book goes in part 0. The image key map
takes the whole `media` table apart from them (`media_map.py source-media.sqlite`).
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections import Counter
from itertools import batched

from build import HERE

# Each table and the column naming its row; a table with no such column is copied whole.
TABLES = {"units": "id", "corpus_units": "id", "media": "key", "unit_ngrams": None, "pages": "id",
          "document_dating": None, "unit_redirects": "id"}


def key_of(path):
    """An image key as publish.py looks it up: the file name of the image's URL, less its extension."""
    return (path or "").rsplit("/", 1)[-1].split(".")[0] or None


def split(source, n):
    """Write catalogue-<k>.sqlite for k < n from the whole catalogue; the parts' paths."""
    db = sqlite3.connect(source)
    weight = Counter()
    for table in ("units", "corpus_units", "unit_ngrams"):
        weight.update(dict(db.execute(f'SELECT document, count(*) FROM "{table}" WHERE document IS NOT NULL GROUP BY document')))
    # Heaviest book first, each to the lightest part.
    load, part_of = [0] * n, {}
    for document, w in sorted(weight.items(), key=lambda kv: (-kv[1], kv[0])):
        k = load.index(min(load))
        part_of[document] = k
        load[k] += w
    db.execute("CREATE TEMP TABLE part_of(document TEXT PRIMARY KEY, part INTEGER NOT NULL)")
    db.executemany("INSERT INTO temp.part_of VALUES(?, ?)", part_of.items())
    db.execute("CREATE INDEX IF NOT EXISTS media_key ON media(key)")
    # A corpus glyph's review overlay has no book of its own: it goes with its glyph.
    db.execute("CREATE TEMP TABLE overlay_part AS SELECT u.id, coalesce(p.part, 0) AS part FROM units u "
               "LEFT JOIN corpus_units c ON c.id = u.id LEFT JOIN temp.part_of p ON p.document = c.document "
               "WHERE u.origin = 'corpus'")
    paths = []
    for k in range(n):
        path = HERE / f"catalogue-{k}.sqlite"
        building = path.with_name(path.name + ".building")
        building.unlink(missing_ok=True)
        db.execute("ATTACH ? AS part", (str(building),))
        mine = f"coalesce((SELECT part FROM temp.part_of WHERE document = t.document), 0) = {k}"
        for table in TABLES:
            cols = ",".join(f'"{r[1]}"' for r in db.execute(f'PRAGMA main.table_info("{table}")'))
            db.execute(f'CREATE TABLE part."{table}"({cols})')
            if table == "units":
                where = (f"(t.origin <> 'corpus' AND {mine}) OR "
                         f"(t.origin = 'corpus' AND (SELECT part FROM temp.overlay_part o WHERE o.id = t.id) = {k})")
            elif table == "unit_redirects":
                where = "1"
            elif table == "media":
                continue
            else:
                where = mine
            db.execute(f'INSERT INTO part."{table}" SELECT {cols} FROM main."{table}" t WHERE {where}')
        keys = {key for data in db.execute("SELECT data FROM part.units WHERE origin <> 'corpus'")
                for key in (key_of(json.loads(data[0]).get(f)) for f in ("image", "context_image")) if key}
        cols = ",".join(f'"{r[1]}"' for r in db.execute('PRAGMA main.table_info("media")'))
        for chunk in batched(sorted(keys), 500):
            db.execute(f'INSERT INTO part.media SELECT {cols} FROM main.media WHERE key IN ({",".join("?" * len(chunk))})', chunk)
        db.commit()
        counts = {t: db.execute(f'SELECT count(*) FROM part."{t}"').fetchone()[0] for t in TABLES}
        db.execute("DETACH part")
        os.replace(building, path)
        print(f"{path.name}: " + ", ".join(f"{t} {n:,}" for t, n in counts.items()), flush=True)
        paths.append(path)
    return paths


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--parts", type=int, default=0, help="also write N catalogues of whole books")
    args = parser.parse_args()
    target = HERE / "catalogue.sqlite"
    building = target.with_name(target.name + ".building")
    building.unlink(missing_ok=True)
    db = sqlite3.connect(f"file:{building}", uri=True)
    for table, key in TABLES.items():
        path = HERE / f"source-{table}.sqlite"
        if not path.exists():
            sys.exit(f"no {path.name}; pull.py copies it")
        db.execute("ATTACH ? AS src", (f"file:{path}?mode=ro",))
        done = db.execute("SELECT key FROM src._progress WHERE tbl = ?", (table,)).fetchone()
        if not done or done[0] != '"done"':
            sys.exit(f"the copy of {table} is unfinished; finish pull.py first")
        cols = [r[1] for r in db.execute(f'PRAGMA src.table_info("{table}")') if r[1] != "_rowid"]
        names = ",".join(f'"{c}"' for c in cols)
        db.execute(f'CREATE TABLE "{table}"({names})')
        rowid = "_rowid" in {r[1] for r in db.execute(f'PRAGMA src.table_info("{table}")')}
        latest = (f' WHERE _rowid IN (SELECT max(_rowid) FROM src."{table}" GROUP BY "{key}")' if key and rowid else "")
        db.execute(f'INSERT INTO "{table}" SELECT {names} FROM src."{table}"{latest}')
        n = db.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
        copied = db.execute(f'SELECT count(*) FROM src."{table}"').fetchone()[0]
        db.commit()
        db.execute("DETACH src")
        print(f"{table}: {n:,} rows" + (f" ({copied - n:,} older copies of a row left out)" if copied != n else ""), flush=True)
    db.close()
    os.replace(building, target)
    print(f"-> {target}", flush=True)
    if args.parts:
        split(target, args.parts)


if __name__ == "__main__":
    main()
