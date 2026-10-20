"""Copy the accounts into their own D1 database, glyph-atlas-accounts (decision record 0006).

    ATLAS3_DIR=work/atlas-v3 python3 scripts/atlas3/accounts.py pull [--fresh]
    ATLAS3_DIR=work/atlas-v3 python3 scripts/atlas3/accounts.py load [--send]
    ATLAS3_DIR=work/atlas-v3 python3 scripts/atlas3/accounts.py check

pull   finds the authentication tables in the old database's sqlite_master: `user`, `verification`,
       `rateLimit` and every table whose foreign keys lead to `user`, less those pull.py copies for the
       history. A table that is neither, nor named in LEFT, stops it. It creates them (tables, indexes,
       triggers) in `accounts.sqlite`, copies their rows in primary-key ranges, then compares the copy
       with the old database as `check` does. It only reads.
load   writes the schema, the rows (parents before the rows that reference them) and then the indexes
       as SQL parts under `accounts-load/<copy>/`, named by the copy's checksums, and replays them on an
       empty database to confirm they rebuild the copy exactly. With --send each part goes to
       glyph-atlas-accounts and is confirmed by its stamp in `imports`; a rerun sends only what is
       missing, and a database holding another copy's rows or any table of its own is refused.
check  compares the table set, the schema, the row count and a checksum of every row of each table of
       `accounts.sqlite` with the old database, then with glyph-atlas-accounts, and exits 1 on any
       difference. It only reads.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

import publish
from load_history import row_statements
from publish import lit, write_parts
from pull import TABLES as PULLED

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(os.environ.get("ATLAS3_DIR", ROOT / "work/atlas-v3"))
COPY = HERE / "accounts.sqlite"
LOADS = HERE / "accounts-load"
OLD = "glyph-atlas"
DATABASE = "glyph-atlas-accounts"
# The authentication tables no foreign key ties to `user`; the rest are found through their keys.
ROOTS = ("user", "verification", "rateLimit")
# Old tables neither copied here nor by pull.py, and why nothing is lost with them.
EXPORTED = "written from the catalogue by the exporters, which write the new layout's own"
DERIVED = "counts and lists the old Worker derives from the crops; the new layout derives its own"
JOB = "the lock or progress of a job that runs on the old database"
LEFT = {
    **dict.fromkeys(("character_derived", "character_variants", "component_variants", "document_characters",
                     "grapheme_writers", "han_component_names", "han_components", "han_ids", "word_spellings",
                     "words", "form_bases", "form_clusters", "form_families", "form_units"), EXPORTED),
    **dict.fromkeys(("unit_counts", "unit_count_keys", "ngram_counts", "ngram_forms", "corpus_characters",
                     "corpus_gallery"), DERIVED),
    **dict.fromkeys(("form_loading", "corpus_follow_drain", "graphemes_backfill", "hand_order_backfill",
                     "ngram_counts_backfill"), JOB),
    "metadata": "stamps of the old database's exports and counts",
    "d1_migrations": "the old database's migration ledger",
}
CHUNK = 1000
GROUP = 500
IMPORTS = "CREATE TABLE imports(stamp TEXT PRIMARY KEY, at INTEGER NOT NULL);"


def remote(database):
    def run(sql):
        assert sql.lstrip().upper().startswith("SELECT"), sql
        return publish.query(database, sql)
    return run


def local(db):
    def run(sql):
        cur = db.cursor()
        cur.row_factory = sqlite3.Row
        return [dict(r) for r in cur.execute(sql)]
    return run


def q(name):
    return '"' + name.replace('"', '""') + '"'


def stored(query):
    """Every table of a database with its CREATE statement, less SQLite's and D1's own."""
    return {r["name"]: r["sql"] for r in query(
        "SELECT name, sql FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite\\_%' ESCAPE '\\' AND name NOT LIKE '\\_cf\\_%' ESCAPE '\\'")}


def auth_tables(query):
    """The old database's authentication tables: ROOTS and every table whose foreign keys lead to `user`,
    less the tables pull.py copies (`favourites` names a user and moves with the history). Every other
    table must be in LEFT."""
    found = stored(query)
    missing = set(ROOTS) - found.keys()
    if missing:
        sys.exit(f"no table {', '.join(sorted(missing))} in the source")
    # The foreign keys, read from the CREATE statements by SQLite itself.
    shape = sqlite3.connect(":memory:")
    for sql in found.values():
        shape.execute(sql)
    refs = {t: {r[2] for r in shape.execute(f"PRAGMA foreign_key_list({q(t)})")} for t in found}
    auth = {"user"}
    while grown := {t for t in found if refs[t] & auth} - auth - PULLED.keys():
        auth |= grown
    auth |= set(ROOTS)
    unknown = sorted(found.keys() - auth - PULLED.keys() - LEFT.keys())
    if unknown:
        sys.exit(f"the old database holds tables nothing copies: {', '.join(unknown)}; copy them or "
                 "name them in LEFT with the reason")
    return sorted(auth)


def tables(db):
    return sorted(stored(local(db)))


def schema(query, names):
    """The tables' CREATE statements, then those of their indexes and triggers, from sqlite_master."""
    return query("SELECT type, name, tbl_name, sql FROM sqlite_master WHERE tbl_name IN "
                 f"({','.join(lit(t) for t in names)}) AND sql IS NOT NULL "
                 "ORDER BY CASE type WHEN 'table' THEN 0 WHEN 'index' THEN 1 ELSE 2 END, tbl_name, name")


def keys(db, table):
    info = list(db.execute(f"PRAGMA table_info({q(table)})"))
    return [r[1] for r in sorted(info, key=lambda r: r[5]) if r[5]] or ["rowid"]


def columns(db, table):
    return [r[1] for r in db.execute(f"PRAGMA table_info({q(table)})")]


def ordered(db):
    """The tables with every table a foreign key names before the tables that name it."""
    names = tables(db)
    refs = {t: {r[2] for r in db.execute(f"PRAGMA foreign_key_list({q(t)})")} & set(names) - {t} for t in names}
    out = []
    while len(out) < len(names):
        ready = [t for t in names if t not in out and refs[t] <= set(out)]
        if not ready:
            sys.exit(f"the foreign keys of {sorted(set(names) - set(out))} form a cycle")
        out += ready
    return out


def ranges(query, table, key, select):
    """Every row of a table in key order, CHUNK rows a read, each read a range of the primary key."""
    order = ",".join(q(k) if k != "rowid" else k for k in key)
    names = ",".join(f"{q(k) if k != 'rowid' else k} AS _k{i}" for i, k in enumerate(key))
    last = None
    while True:
        where = "" if last is None else f"WHERE ({order}) > ({','.join(lit(v) for v in last)})"
        rows = query(f"SELECT {names}, {select} FROM {q(table)} {where} ORDER BY {order} LIMIT {CHUNK}")
        yield from rows
        if len(rows) < CHUNK:
            return
        last = [rows[-1][f"_k{i}"] for i in range(len(key))]


def summary(query, db):
    """Each table's row count and a checksum of its rows: every value as its SQL literal, in key order."""
    out = {}
    for table in tables(db):
        cols = columns(db, table)
        row = "||','||".join(f"quote({q(c)})" for c in cols)
        digest, n = hashlib.sha256(), 0
        for r in ranges(query, table, keys(db, table), f"{row} AS _row"):
            digest.update(r["_row"].encode() + b"\n")
            n += 1
        out[table] = (n, digest.hexdigest())
    return out


def pull(query, fresh):
    if COPY.exists() and not fresh:
        sys.exit(f"{COPY} exists; --fresh copies the accounts again")
    names = auth_tables(query)
    tmp = COPY.with_name(COPY.name + ".building")
    tmp.unlink(missing_ok=True)
    db = sqlite3.connect(tmp)
    entries = schema(query, names)
    for e in entries:
        db.execute(e["sql"])
    for table in names:
        cols = columns(db, table)
        n = 0
        batch = []
        for r in ranges(query, table, keys(db, table), ",".join(q(c) for c in cols)):
            batch.append([r[c] for c in cols])
            if len(batch) >= CHUNK:
                n += insert(db, table, cols, batch)
                batch = []
        n += insert(db, table, cols, batch)
        db.commit()
        print(f"{table} {n}", flush=True)
    print(f"{len(entries)} statements and the rows of {len(names)} tables", flush=True)
    if not compare(db, query, OLD):
        sys.exit(f"the copy differs from {OLD}; {tmp} is kept for a look, and a rerun copies again")
    db.close()
    tmp.replace(COPY)
    print(f"-> {COPY}", flush=True)


def insert(db, table, cols, rows):
    db.executemany(f"INSERT INTO {q(table)}({','.join(q(c) for c in cols)}) VALUES({','.join('?' * len(cols))})", rows)
    return len(rows)


def copy_name(db):
    digest = hashlib.sha256()
    for e in schema(local(db), tables(db)):
        digest.update(e["sql"].encode() + b"\n")
    for table, (n, sha) in summary(local(db), db).items():
        digest.update(f"{table} {n} {sha}\n".encode())
    return "accounts-" + digest.hexdigest()[:12]


def parts(db):
    """The copy's parts: imports and the tables, the rows, then the indexes and triggers."""
    orphans = db.execute("PRAGMA foreign_key_check").fetchall()
    if orphans:
        sys.exit(f"{len(orphans)} rows name a missing parent, e.g. {orphans[:5]}; D1 would refuse them")
    run = LOADS / copy_name(db)
    if run.exists():
        return run
    entries = schema(local(db), tables(db))
    tmp = LOADS / ".building" / run.name
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    write_parts(tmp, "1-schema", [[IMPORTS] + [e["sql"] + ";" for e in entries if e["type"] == "table"]])
    groups = []
    for table in ordered(db):
        cols, key = columns(db, table), keys(db, table)
        batch = []
        for r in db.execute(f"SELECT {','.join(q(c) for c in cols)} FROM {q(table)} ORDER BY "
                            f"{','.join(q(k) if k != 'rowid' else k for k in key)}"):
            batch += row_statements(table, cols, key, r)
            if len(batch) >= GROUP:
                groups.append(batch)
                batch = []
        if batch:
            groups.append(batch)
    write_parts(tmp, "2-rows", groups)
    write_parts(tmp, "3-indexes", [[e["sql"] + ";" for e in entries if e["type"] != "table"]])
    tmp.rename(run)
    return run


def stamp(run, part):
    label, k = part.stem.rsplit("-", 1)
    return f"{run.name}.{label}.{k}"


def replay(run, db):
    """Apply the parts to an empty database as D1 would, foreign keys on, and compare it with the copy."""
    target = sqlite3.connect(":memory:")
    target.execute("PRAGMA foreign_keys = ON")
    for part in sorted(run.glob("*.sql")):
        target.executescript("BEGIN;\n" + part.read_text() + "COMMIT;\n")
    if differences(db, local(target))[0]:
        sys.exit(f"the parts in {run} do not rebuild the copy")


def loaded(query):
    """The stamps the target holds, and the tables it holds besides `imports`."""
    tables = list(stored(query))
    stamps = {r["stamp"] for r in query("SELECT stamp FROM imports")} if "imports" in tables else set()
    return stamps, [t for t in tables if t != "imports"]


def send(database, file, stamp_, query):
    """Import one SQL file; D1 applies it whole or not at all, and wrangler can report an applied file as
    failed, so it counts as applied when its stamp is there."""
    for _ in range(4):
        if stamp_ in loaded(query)[0]:
            return True
        publish.wrangler("d1", "execute", database, "--remote", "--yes", "--file", str(file))
        time.sleep(5)
    return stamp_ in loaded(query)[0]


def deliver(run, query):
    stamps, tables = loaded(query)
    others = sorted(s for s in stamps if not s.startswith(run.name + "."))
    if others:
        sys.exit(f"{DATABASE} holds rows of another copy ({others[0]}); delete and create the database "
                 "again to load this one")
    if tables and not stamps:
        sys.exit(f"{DATABASE} holds tables no copy loaded ({', '.join(tables)}); it must be empty")
    sent = 0
    for part in sorted(run.glob("*.sql")):
        if stamp(run, part) in stamps:
            continue
        if not send(DATABASE, part, stamp(run, part), query):
            sys.exit(f"{part.name} did not apply; rerun to finish")
        sent += 1
        print(f"{part.name} applied", flush=True)
    print(f"{run.name}: {sent} parts sent, {len(list(run.glob('*.sql'))) - sent} already there", flush=True)


def load(do_send, query=None):
    db = sqlite3.connect(f"file:{COPY}?mode=ro", uri=True)
    run = parts(db)
    replay(run, db)
    print(f"{len(list(run.glob('*.sql')))} parts -> {run}; they rebuild the copy", flush=True)
    if not do_send:
        print("dry run: nothing sent; --send loads them", flush=True)
        return
    deliver(run, query or remote(DATABASE))


def differences(db, query, names=None):
    """Lines naming each table missing on either side or whose schema, row count or checksum differs
    from the copy, and the copy's counts and checksums. `names` is the table set the other side should
    hold; without it, the copy's."""
    ours_names = tables(db)
    out = [f"{t}: {'only in the copy' if t in ours_names else 'not in the copy'}"
           for t in sorted(set(ours_names) ^ set(names or ours_names))]
    ours = {(e["type"], e["name"]): e["sql"] for e in schema(local(db), ours_names)}
    theirs = {(e["type"], e["name"]): e["sql"] for e in schema(query, ours_names)}
    for k in sorted(ours.keys() | theirs.keys()):
        if ours.get(k) != theirs.get(k):
            out.append(f"{k[0]} {k[1]}: schema differs")
    here = summary(local(db), db)
    if out:
        return out, here
    there = summary(query, db)
    for table in ours_names:
        if here[table] != there[table]:
            out.append(f"{table}: {here[table][0]} rows here, {there[table][0]} there, checksums "
                       f"{here[table][1][:12]} and {there[table][1][:12]}")
    return out, here


def compare(db, query, name):
    """Print how the copy compares with a database; True when they match. Against the old database the
    table set is the one auth_tables finds there now."""
    found, here = differences(db, query, auth_tables(query) if name == OLD else None)
    for line in found:
        print(f"{name}: {line}", flush=True)
    if not found:
        print(f"{name} matches {COPY.name}: " + ", ".join(f"{t} {n} rows {sha[:12]}" for t, (n, sha) in here.items()),
              flush=True)
    return not found


def check(query=None, old=None):
    """The chain old database -> copy -> accounts database, each link compared."""
    db = sqlite3.connect(f"file:{COPY}?mode=ro", uri=True)
    source = compare(db, old or remote(OLD), OLD)
    target = compare(db, query or remote(DATABASE), DATABASE)
    if not (source and target):
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="step", required=True)
    sub.add_parser("pull", help="copy the auth tables out of the old database").add_argument(
        "--fresh", action="store_true", help="replace an existing accounts.sqlite")
    sub.add_parser("load", help="write the parts; with --send, load them").add_argument(
        "--send", action="store_true", help=f"apply the parts to {DATABASE}")
    sub.add_parser("check", help=f"compare {DATABASE} with accounts.sqlite")
    args = parser.parse_args()
    if args.step == "pull":
        pull(remote(OLD), args.fresh)
    elif args.step == "load":
        load(args.send)
    else:
        check()


if __name__ == "__main__":
    main()
