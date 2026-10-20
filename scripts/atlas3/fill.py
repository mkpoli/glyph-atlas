"""Fill the columns the cutover adds to `crops`, once, then build the cutover's indexes, once.

    ATLAS3_DIR=work/atlas-v3 python3 scripts/atlas3/fill.py          # work out the fill, write the parts, send nothing
    ATLAS3_DIR=work/atlas-v3 python3 scripts/atlas3/fill.py --send   # and load them into glyph-atlas-v3

Every bulk write to D1 happens once and only where needed:

- COLUMNS names each column the cutover adds and the migration that adds it, which adds nothing else.
  `--send` applies a migration (stamp `migration:<number>`) where the local copy lacks its column.
- FILLS names each column's values, worked out from the local copy, `out/mirror.sqlite` (only read)
  and the copies. One UPDATE per crop whose values differ from the local copy sets every filled
  column at once, so a crop row is written once however many columns change.
- Then each index of `migrations-atlas/indexes-cutover.sql` the local copy lacks, in the file's order,
  is built once over the filled rows; no index is kept up row by row during the fill.

`family` (migration 0012): the family a crop is filed under, the grapheme of the family its record
names (`units.family`, a reviewed corpus glyph's overlay before `corpus_units.family`), for every crop:
a crop with a character joins its character's family, and one with none, or whose character a review
clears, joins this one. A family the layout has no grapheme for stops the run.

Parts are named by their content, confirmed by their stamps in `imports` and replayed on the local
copy (as books.py loads them), so a rerun sends only what differs and, once everything is in place,
nothing. Figures go to `out/fill-report.txt`. Run it before the cutover's publish, which then finds
every value in place; `publish.py` refuses a local copy without the columns.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import re
import sqlite3
import sys

from build import HERE, OUT, char_text
from load_history import GROUP, SCHEMA, built, deliver, stamped
from publish import applied, lit, send

COLUMNS = {"family": "0012_family.sql"}
INDEXES = SCHEMA / "indexes-cutover.sql"


def source(table):
    db = sqlite3.connect(f"file:{HERE / f'source-{table}.sqlite'}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    return db


def latest(db, table, cols, where=""):
    # A keyset copy can hold a row twice when it was copied again; the last copy wins.
    return db.execute(f"SELECT {cols} FROM {table} WHERE _rowid IN (SELECT max(_rowid) FROM {table} GROUP BY id){where}")


def families(atlas, mirror):
    """Each crop's family, by crop id, for the crops whose record names one."""
    grapheme = dict(atlas.execute("SELECT text, id FROM graphemes"))

    def id_of(text_id):
        row = mirror.execute("SELECT id FROM ids WHERE text=?", (text_id,)).fetchone()
        return row[0] if row else None

    units = source("units")
    overlay = {r["id"]: r for r in latest(units, "units", "id, character, family", " AND origin='corpus'")}
    wanted, missing = {}, set()

    def file(text_id, character, family):
        if not family:
            return
        gid = grapheme.get(char_text(family))
        if gid is None:
            missing.add(family)
        elif (crop := id_of(text_id)) is not None:
            wanted[crop] = gid

    for r in latest(units, "units", "id, character, family", " AND origin='local'"):
        file(r["id"], r["character"], r["family"])
    for r in latest(source("corpus_units"), "corpus_units", "id, character, family"):
        o = overlay.get(r["id"])
        file(r["id"], (o["character"] if o else None) or r["character"], o["family"] if o else r["family"])
    if missing:
        sys.exit(f"{len(missing)} families have no grapheme in the layout (for example {sorted(missing)[:5]}); "
                 "publish a catalogue that names them first")
    return wanted


FILLS = {"family": families}


def statements(atlas, mirror):
    """One UPDATE per crop whose filled columns differ from the local copy, then the indexes to build."""
    have = {r[1] for r in atlas.execute("PRAGMA table_info(crops)")}
    by_crop, counts = {}, {}
    for column, values in FILLS.items():
        wanted = values(atlas, mirror)
        current = dict(atlas.execute(f"SELECT id, {column} FROM crops WHERE {column} IS NOT NULL")) if column in have else {}
        live = {r[0] for r in atlas.execute("SELECT id FROM crops")} if wanted else set()
        changed = {crop: value for crop, value in wanted.items() if crop in live and current.get(crop) != value}
        changed.update({crop: None for crop in current if crop not in wanted})
        counts[column] = len(changed)
        for crop, value in changed.items():
            by_crop.setdefault(crop, {})[column] = value
    rows = [f"UPDATE crops SET {','.join(f'{c}={lit(v)}' for c, v in values.items())} WHERE id={crop};"
            for crop, values in sorted(by_crop.items())]
    groups = [rows[i:i + GROUP] for i in range(0, len(rows), GROUP)]
    built_already = {r[0] for r in atlas.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    indexes = [s for s in index_statements() if re.match(r"CREATE INDEX (\w+)", s)[1] not in built_already]
    return groups + [[s] for s in indexes], counts, len(by_crop), indexes


def index_statements():
    text = "\n".join(line for line in INDEXES.read_text().splitlines() if not line.startswith("--"))
    return [s.strip() + ";" for s in text.split(";") if s.strip()]


def migrate(atlas):
    """Each column's migration on D1 and the local copy, once."""
    have = {r[1] for r in atlas.execute("PRAGMA table_info(crops)")}
    for column, name in COLUMNS.items():
        if column in have:
            continue
        stamp = f"migration:{name[:4]}"
        path = OUT / "fill-load" / f"{name[:4]}.sql"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(stamped((SCHEMA / name).read_text(), stamp))
        if not applied("glyph-atlas-v3", stamp) and not send("glyph-atlas-v3", path, stamp):
            sys.exit(f"migration {name} did not apply; rerun")
        atlas.executescript("BEGIN;\n" + path.read_text() + "COMMIT;\n")
        print(f"migration {name} applied", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--send", action="store_true", help="apply the column migrations and load the parts into glyph-atlas-v3")
    args = parser.parse_args()
    lock = open(OUT / "mirror.lock", "w")  # noqa: SIM115 -- held until the process exits
    fcntl.flock(lock, fcntl.LOCK_EX)
    mirror = sqlite3.connect(f"file:{OUT / 'mirror.sqlite'}?mode=ro", uri=True)
    if mirror.execute("SELECT 1 FROM sqlite_master WHERE name='pending'").fetchone() and mirror.execute("SELECT 1 FROM pending").fetchone():
        sys.exit("a publish is unfinished; finish it with publish.py first")
    atlas = sqlite3.connect(OUT / "atlas.sqlite")
    if args.send:
        build = mirror.execute("SELECT value FROM meta WHERE key='build'").fetchone()[0]
        if not applied("glyph-atlas-v3", f"build:{build}"):
            sys.exit(f"glyph-atlas-v3 was not loaded from build {build}; the local copy does not describe it")
        migrate(atlas)
    groups, counts, crops, indexes = statements(atlas, mirror)
    report = "".join(f"{column}: {n} crops change\n" for column, n in counts.items())
    report += f"crop rows written: {crops}\nindexes to build: {', '.join(re.match(r'CREATE INDEX (\w+)', s)[1] for s in indexes) or 'none'}\n"
    (OUT / "fill-report.txt").write_text(report)
    print(report, end="", flush=True)
    if not groups:
        print("every column is filled and every index built; nothing to send", flush=True)
        return
    digest = hashlib.sha256("\n".join(s for g in groups for s in g).encode()).hexdigest()[:12]
    run = OUT / "fill-load" / f"fill-{digest}"
    n = built(run, "rows", groups)
    print(f"{n if n is not None else 'existing'} parts -> {run}", flush=True)
    if not args.send:
        print("dry run: nothing sent", flush=True)
        return
    deliver(run, atlas)
    print("filled", flush=True)


if __name__ == "__main__":
    main()
