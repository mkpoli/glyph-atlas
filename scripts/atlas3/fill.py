"""Fill the columns the cutover adds to `crops`, once, then build the cutover's indexes, once.

    ATLAS3_DIR=work/atlas-v3 python3 scripts/atlas3/fill.py          # work out the fill, write the parts, send nothing
    ATLAS3_DIR=work/atlas-v3 python3 scripts/atlas3/fill.py --send   # and load them into glyph-atlas-v3

Every bulk write to D1 happens once and only where needed:

- COLUMNS names each column the cutover adds and the migration that adds it; 0012 also extends the empty cutover tally.
  `--send` applies a migration (stamp `migration:<number>`) where the local copy lacks its column.
- FILLS names each column's values, worked out from the local copy, `out/mirror.sqlite`
  and the copies. One UPDATE per crop whose values differ from the local copy sets every filled
  column at once, so a crop row is written once however many columns change.
- Then each index of `migrations-atlas/indexes-cutover.sql` the local copy lacks, in the file's order,
  is built once over the filled rows; no index is kept up row by row during the fill. An index on a
  table the local copy lacks yet (`book_dates`, which books.py creates after the fill) waits for the
  next fill, which builds it alone.

`family` (migration 0012): the family a crop is filed under, the grapheme of the family its record
names (`units.family`, a reviewed corpus glyph's overlay before `corpus_units.family`), for every crop:
a crop with a character joins its character's family, and one with none, or whose character a review
clears, joins this one. A family the layout has no grapheme for stops the run.

`priority` (0011) comes from collected units, with 1 for other crops. `cluster` comes from the
validated lists loaded by `crop_facts.py --send --defer-crops`. That load keeps form decisions paused
and records its handoff in the mirror. Fill releases them after every crop update and index succeeds.
Keep the site's global write freeze throughout this cutover operation.

Parts are named by their content, confirmed by their stamps in `imports` and replayed on the local
copy (as books.py loads them), so a rerun sends only what differs and, once everything is in place,
nothing. A pending run resumes its saved SQL plan after verifying its file hashes and the remote
build stamp. Figures go to `out/fill-report.txt`. Run it before the cutover's publish, which then finds
every value in place; `publish.py` refuses a local copy without the columns.
"""

from __future__ import annotations

import argparse
import fcntl
import gzip
import hashlib
import json
import re
import sqlite3
import sys

from build import HERE, OUT, char_text
from load_history import GROUP, SCHEMA, built, deliver, stamped
from publish import applied, lit, send

COLUMNS = {"priority": "0011_browse.sql", "family": "0012_family.sql"}
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


def priorities(atlas, mirror):
    """Collected crops keep the copied priority; every other crop defaults to one."""
    wanted = dict(atlas.execute("SELECT id, 1 FROM crops"))
    for row in latest(source("units"), "units", "id, priority", " AND origin='local'"):
        crop = mirror.execute("SELECT id FROM ids WHERE text=?", (row["id"],)).fetchone()
        if crop and crop[0] in wanted:
            if row["priority"] not in (0, 1, 2):
                sys.exit(f"invalid review priority for {row['id']}")
            wanted[crop[0]] = row["priority"]
    return wanted


def clusters(atlas, mirror):
    """Use the validated lists that crop_facts loaded, before deriving any clearing updates."""
    wanted = {}
    live = {r[0] for r in atlas.execute("SELECT id FROM crops")}
    for number, key, family, count, name in atlas.execute("SELECT id,key,family,count,list FROM form_clusters"):
        path = OUT / "forms" / f"{name}.json.gz"
        if not path.exists():
            sys.exit(f"missing cluster list {path.name}; finish crop_facts.py --send --defer-crops first")
        try:
            raw = gzip.decompress(path.read_bytes())
            body = json.loads(raw)
            valid = (name == f"{number}-{hashlib.sha256(raw).hexdigest()[:12]}" and
                     body["family"] == family and body["key"] == key and len(body["members"]) == count)
            for member in body["members"]:
                crop = member[0]
                if type(crop) is not int or crop not in live or crop in wanted:
                    valid = False
                wanted[crop] = number
        except (OSError, ValueError, KeyError, IndexError, TypeError):
            valid = False
        if not valid:
            sys.exit(f"invalid cluster list {path.name}; no crop updates prepared")
    return wanted


FILLS = {"family": families, "priority": priorities, "cluster": clusters}


def statements(atlas, mirror):
    """One UPDATE per crop whose filled columns differ from the local copy, then the indexes to build."""
    have = {r[1] for r in atlas.execute("PRAGMA table_info(crops)")}
    by_crop, counts = {}, {}
    for column, values in FILLS.items():
        wanted = values(atlas, mirror)
        current = dict(atlas.execute(f"SELECT id, {column} FROM crops WHERE {column} IS NOT NULL")) if column in have else {}
        live = {r[0] for r in atlas.execute("SELECT id FROM crops")} if wanted else set()
        changed = {crop: value for crop, value in wanted.items() if crop in live and current.get(crop, 1 if column == "priority" and column not in have else None) != value}
        changed.update({crop: None for crop in current if crop not in wanted})
        counts[column] = len(changed)
        for crop, value in changed.items():
            by_crop.setdefault(crop, {})[column] = value
    rows = [f"UPDATE crops SET {','.join(f'{c}={lit(v)}' for c, v in values.items())} WHERE id={crop};"
            for crop, values in sorted(by_crop.items())]
    groups = [rows[i:i + GROUP] for i in range(0, len(rows), GROUP)]
    built_already = {r[0] for r in atlas.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    tables = {r[0] for r in atlas.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    missing = [s for s in index_statements() if re.match(r"CREATE INDEX (\w+)", s)[1] not in built_already]
    indexes = [s for s in missing if re.match(r"CREATE INDEX \w+ ON (\w+)", s)[1] in tables]
    waiting = [s for s in missing if s not in indexes]
    return groups + [[s] for s in indexes], counts, len(by_crop), indexes, waiting


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


def validate_plan(run):
    """A saved plan must retain every original SQL part, byte for byte."""
    try:
        expected = json.loads((run / "manifest.json").read_text())
        actual = {part.name: hashlib.sha256(part.read_bytes()).hexdigest() for part in run.glob("*.sql")}
        if not expected or actual != expected:
            raise ValueError("parts differ")
    except (OSError, ValueError):
        sys.exit(f"fill plan {run.name} is incomplete or changed; restore its original files")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--send", action="store_true", help="apply the column migrations and load the parts into glyph-atlas-v3")
    args = parser.parse_args()
    lock = open(OUT / "mirror.lock", "w")  # noqa: SIM115 -- held until the process exits
    fcntl.flock(lock, fcntl.LOCK_EX)
    mirror = sqlite3.connect(OUT / "mirror.sqlite")
    mirror.execute("CREATE TABLE IF NOT EXISTS pending(run TEXT PRIMARY KEY)")
    mirror.commit()
    pending = [r[0] for r in mirror.execute("SELECT run FROM pending")]
    if any(not name.startswith("fill/") for name in pending):
        sys.exit("a publish or crop facts load is unfinished; finish it first")
    if len(pending) > 1:
        sys.exit("multiple crop fills are pending; restore the original fill state")
    atlas = sqlite3.connect(OUT / "atlas.sqlite")
    if args.send:
        build = mirror.execute("SELECT value FROM meta WHERE key='build'").fetchone()[0]
        if not applied("glyph-atlas-v3", f"build:{build}"):
            sys.exit(f"glyph-atlas-v3 was not loaded from build {build}; the local copy does not describe it")
    if pending and pending[0].startswith("fill/plan/"):
        run = OUT / "fill-load" / pending[0].removeprefix("fill/plan/")
        if not run.is_dir():
            sys.exit("the pending fill plan is missing; restore its original directory")
        if not args.send:
            print(f"unfinished fill {run.name}; --send resumes its saved plan", flush=True)
            return
        validate_plan(run)
        deliver(run, atlas)
        mirror.execute("DELETE FROM pending WHERE run=?", (pending[0],))
        mirror.commit()
        print(f"finished {run.name}", flush=True)
        return
    source_run = pending[0].removeprefix("fill/source/") if pending else None
    if source_run and atlas.execute("SELECT lists,loading FROM form_state WHERE id=0").fetchone() != (source_run, 1):
        sys.exit("the deferred crop facts state no longer matches its handoff")
    # Validate every source and list before any remote schema or row changes.
    groups, counts, crops, indexes, waiting = statements(atlas, mirror)
    if args.send:
        migrate(atlas)
    report = "".join(f"{column}: {n} crops change\n" for column, n in counts.items())
    report += f"crop rows written: {crops}\nindexes to build: {', '.join(re.match(r'CREATE INDEX (\w+)', s)[1] for s in indexes) or 'none'}\n"
    if waiting:
        # An index on a table a later load creates (book_dates, books.py) is built by the next fill.
        report += "indexes waiting for their table: " + ", ".join(
            "{} on {}".format(*re.match(r"CREATE INDEX (\w+) ON (\w+)", s).groups()) for s in waiting) + "\n"
    (OUT / "fill-report.txt").write_text(report)
    print(report, end="", flush=True)
    if source_run:
        groups.append([f"UPDATE form_state SET loading=0 WHERE id=0 AND lists={lit(source_run)};"])
    if not groups:
        print("every column is filled and every index built; nothing to send", flush=True)
        return
    digest = hashlib.sha256("\n".join(s for g in groups for s in g).encode()).hexdigest()[:12]
    run = OUT / "fill-load" / f"fill-{digest}"
    n = built(run, "rows", groups, manifest=True)
    validate_plan(run)
    print(f"{n if n is not None else 'existing'} parts -> {run}", flush=True)
    if not args.send:
        print("dry run: nothing sent", flush=True)
        return
    mirror.execute("DELETE FROM pending WHERE run LIKE 'fill/%'")
    mirror.execute("INSERT INTO pending VALUES(?)", ("fill/plan/" + run.name,))
    mirror.commit()
    deliver(run, atlas)
    mirror.execute("DELETE FROM pending WHERE run=?", ("fill/plan/" + run.name,))
    mirror.commit()
    print("filled", flush=True)


if __name__ == "__main__":
    main()
