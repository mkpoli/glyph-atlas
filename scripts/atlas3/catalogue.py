"""Write the catalogue the cutover publishes: the live tables as pull.py copied them.

    ATLAS3_DIR=work/atlas-v3 python3 scripts/atlas3/catalogue.py

`publish.py` takes a catalogue in the exporters' format; at the cutover it is the live database itself,
from its copies `source-<table>.sqlite`: `units` (collected crops, the corpus review overlay and retired
crops), `corpus_units`, `media`, `unit_ngrams`, `pages`, `document_dating` and `unit_redirects`. Each
table keeps the live columns, less the copy's `_rowid`. A copy read by rowid holds a row twice when the
live row was rewritten during the copy; the copy at the higher rowid is the row as it stands, so only it
is kept. The catalogue is written to `catalogue.sqlite` beside the copies, through a `.building` file.

    python3 scripts/atlas3/publish.py work/atlas-v3/catalogue.sqlite --dry-run
"""

from __future__ import annotations

import os
import sqlite3
import sys

from build import HERE

# Each table and the column naming its row; a table with no such column is copied whole.
TABLES = {"units": "id", "corpus_units": "id", "media": "key", "unit_ngrams": None, "pages": "id",
          "document_dating": None, "unit_redirects": "id"}


def main():
    target = HERE / "catalogue.sqlite"
    building = target.with_name(target.name + ".building")
    building.unlink(missing_ok=True)
    db = sqlite3.connect(building)
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


if __name__ == "__main__":
    main()
