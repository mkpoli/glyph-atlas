"""Copy the accounts into their own D1 database, glyph-atlas-accounts (decision record 0006).

    ATLAS3_DIR=work/atlas-v3 python3 scripts/atlas3/accounts.py pull [--fresh]
    ATLAS3_DIR=work/atlas-v3 python3 scripts/atlas3/accounts.py load [--send]
    ATLAS3_DIR=work/atlas-v3 python3 scripts/atlas3/accounts.py check

pull   finds the authentication tables in the old database's sqlite_master: `user`, `verification`,
       `rateLimit` and every table whose foreign keys lead to `user`, less those pull.py copies for the
       history. A table that is neither, nor named in LEFT, stops it. It creates them (tables, indexes,
       triggers) in `accounts.sqlite`, copies their rows in primary-key ranges, then compares the copy
       with the old database as `check` does. It only reads.
load   writes, as SQL parts under `accounts-load/<copy>-<base>/`, what turns the loaded copy
       (`accounts-loaded.sqlite`, every part sent so far replayed) into this copy: on the first load the
       schema, the rows (parents before the rows that reference them) and then the indexes; after it the
       difference, an INSERT per new row, an UPDATE per changed row and a DELETE per row gone (children
       first), so a fresh copy sends only what changed and nothing already loaded. It replays the parts on
       the loaded copy to confirm they rebuild this copy exactly. With --send each part goes to
       glyph-atlas-accounts, is confirmed by its stamp in `imports` and replayed on the loaded copy; a
       rerun sends only what is missing, and a database holding a stamp the loaded copy does not know, or
       tables no load wrote, is refused.
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
# Every part sent to glyph-atlas-accounts, replayed: what the database holds, for the next difference.
LOADED = HERE / "accounts-loaded.sqlite"
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
                     "words", "form_clusters", "form_families", "form_units"), EXPORTED),
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


def summary(query, db, names=None):
    """Each table's row count and a checksum of its rows: every value as its SQL literal, in key order.
    The tables are `names`, or every table of `db`, whose columns and keys the reads follow."""
    out = {}
    for table in names or tables(db):
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
    # Sessions and account tokens: readable by the owner only.
    os.close(os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
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


def private(path):
    """An SQLite file readable by the owner only: sessions and account tokens."""
    if not path.exists():
        os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
    return sqlite3.connect(path)


def stamps_of(db):
    return {r[0] for r in db.execute("SELECT stamp FROM imports")} if "imports" in stored(local(db)) else set()


def run_name(db, base):
    """The run that turns `base` (the loaded copy) into `db`: named by both, so a run built against an
    older loaded copy is never sent over a newer one."""
    digest = hashlib.sha256("\n".join(sorted(stamps_of(base))).encode()).hexdigest()[:12]
    return f"{copy_name(db)}-{digest}"


def key_of(db, table):
    """The columns that name a row: the primary key, or every column of a table without one."""
    key = keys(db, table)
    return columns(db, table) if key == ["rowid"] else key


def where(key, values):
    return " AND ".join(f"{q(k)} IS {lit(v)}" for k, v in zip(key, values))


def difference(db, base):
    """Groups of statements that turn `base`'s rows into `db`'s, and the new, changed and gone rows per
    table. Rows gone are deleted children first; new and changed rows follow parents first. A changed row
    is updated in place, so no delete cascades to the rows that reference it."""
    gone, groups, counts = [], [], {}
    order = ordered(db)
    for table in order:
        cols, key = columns(db, table), key_of(db, table)
        at = [cols.index(k) for k in key]
        select = f"SELECT {','.join(q(c) for c in cols)} FROM {q(table)}"
        old = {tuple(r[i] for i in at): r for r in base.execute(select)}
        n, batch = {"new": 0, "changed": 0, "gone": 0}, []
        for r in db.execute(select + f" ORDER BY {','.join(q(k) for k in key)}"):
            k = tuple(r[i] for i in at)
            was = old.pop(k, None)
            if was == r:
                continue
            if was is None:
                n["new"] += 1
                batch += row_statements(table, cols, key, r)
            else:
                n["changed"] += 1
                sets = ",".join(f"{q(c)} = {lit(v)}" for c, v in zip(cols, r) if c not in key)
                statement = f"UPDATE {q(table)} SET {sets} WHERE {where(key, k)};"
                if len(statement.encode()) > publish.STATEMENT_BYTES:
                    sys.exit(f"a changed row of {table} needs a statement over {publish.STATEMENT_BYTES} bytes")
                batch.append(statement)
            if len(batch) >= GROUP:
                groups.append(batch)
                batch = []
        if batch:
            groups.append(batch)
        n["gone"] = len(old)
        gone.append([f"DELETE FROM {q(table)} WHERE {where(key, k)};" for k in old])
        counts[table] = n
    deletes = [g for table in reversed(gone) for g in (table[i:i + GROUP] for i in range(0, len(table), GROUP))]
    return deletes + groups, counts


def parts(db, base):
    """The parts that turn the loaded copy `base` into the copy: on the first load the imports table and
    the tables, the rows, then the indexes and triggers; after it the difference of the rows."""
    orphans = db.execute("PRAGMA foreign_key_check").fetchall()
    if orphans:
        sys.exit(f"{len(orphans)} rows name a missing parent, e.g. {orphans[:5]}; D1 would refuse them")
    run = LOADS / run_name(db, base)
    if run.exists():
        return run
    entries = schema(local(db), tables(db))
    first = not stamps_of(base)
    if not first:
        here = {(e["type"], e["name"]): e["sql"] for e in entries}
        there = {(e["type"], e["name"]): e["sql"] for e in schema(local(base), tables(db))}
        if here != there:
            sys.exit("the auth tables' schema changed since the first load; a schema change is applied by hand")
    tmp = LOADS / ".building" / run.name
    shutil.rmtree(tmp, ignore_errors=True)
    for d in (LOADS, tmp.parent, tmp):
        d.mkdir(mode=0o700, exist_ok=True)
        d.chmod(0o700)
    if first:
        write_parts(tmp, "1-schema", [[IMPORTS] + [e["sql"] + ";" for e in entries if e["type"] == "table"]])
        empty = sqlite3.connect(":memory:")
        for e in entries:
            if e["type"] == "table":
                empty.execute(e["sql"])
        groups, counts = difference(db, empty)
    else:
        groups, counts = difference(db, base)
    write_parts(tmp, "2-rows", groups)
    if first:
        write_parts(tmp, "3-indexes", [[e["sql"] + ";" for e in entries if e["type"] != "table"]])
    (tmp / "figures.txt").write_text("".join(f"{t}: {c['new']} new, {c['changed']} changed, {c['gone']} gone\n"
                                             for t, c in counts.items()))
    tmp.rename(run)
    return run


def stamp(run, part):
    label, k = part.stem.rsplit("-", 1)
    return f"{run.name}.{label}.{k}"


def apply(target, part):
    target.executescript("BEGIN;\n" + part.read_text() + "COMMIT;\n")


def replay(run, db, base):
    """Apply the parts to a copy of the loaded copy as D1 would, foreign keys on, and compare it with the copy."""
    target = sqlite3.connect(":memory:")
    base.backup(target)
    target.execute("PRAGMA foreign_keys = ON")
    for part in sorted(run.glob("*.sql")):
        apply(target, part)
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


def deliver(run, query, base):
    """Each part to the database, then to the loaded copy, in order. A part the database holds and the
    loaded copy lacks (a run stopped between the two) is only replayed."""
    stamps, tables = loaded(query)
    if tables and not stamps:
        sys.exit(f"{DATABASE} holds tables no copy loaded ({', '.join(tables)}); it must be empty")
    ours = {stamp(run, part) for part in run.glob("*.sql")}
    unknown = sorted(stamps - stamps_of(base) - ours)
    if unknown:
        sys.exit(f"{DATABASE} holds {unknown[0]}, which {LOADED.name} lacks; it no longer describes the database")
    lost = sorted(stamps_of(base) - stamps)
    if lost:
        sys.exit(f"{LOADED.name} holds {lost[0]}, which {DATABASE} lacks; it no longer describes the database")
    sent = 0
    for part in sorted(run.glob("*.sql")):
        if stamp(run, part) in stamps_of(base):
            continue
        if stamp(run, part) not in stamps:
            if not send(DATABASE, part, stamp(run, part), query):
                sys.exit(f"{part.name} did not apply; rerun to finish")
            sent += 1
            print(f"{part.name} applied", flush=True)
        apply(base, part)
    print(f"{run.name}: {sent} parts sent, {len(ours) - sent} already there", flush=True)


def load(do_send, query=None):
    db = sqlite3.connect(f"file:{COPY}?mode=ro", uri=True)
    base = private(LOADED)
    run = parts(db, base)
    replay(run, db, base)
    print((run / "figures.txt").read_text(), end="", flush=True)
    print(f"{len(list(run.glob('*.sql')))} parts -> {run}; they turn {LOADED.name} into the copy", flush=True)
    if not do_send:
        print("dry run: nothing sent; --send loads them", flush=True)
        return
    deliver(run, query or remote(DATABASE), base)


def differences(db, query, names=None):
    """Lines naming each table missing on either side or whose schema, row count or checksum differs
    from the copy, and the copy's counts and checksums. `names` is the table set the other side should
    hold; without it, the copy's."""
    ours_names = tables(db)
    out = [f"{t}: {'only in the copy' if t in ours_names else 'not in the copy'}"
           for t in sorted(set(ours_names) ^ set(names or ours_names))]
    ours = {(e["type"], e["name"]): e["sql"] for e in schema(local(db), ours_names)}
    # An index or trigger only the other side has came from its own migrations (migrations-accounts adds
    # `user_created`); the copy's own must all be there as the copy has them.
    theirs = {(e["type"], e["name"]): e["sql"] for e in schema(query, ours_names)
              if e["type"] == "table" or (e["type"], e["name"]) in ours}
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
