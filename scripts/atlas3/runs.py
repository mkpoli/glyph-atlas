"""Move the written forms of the runs into glyph-atlas-v3-runs (`migrations-atlas-runs/0002_forms.sql`).

    ATLAS3_DIR=work/atlas-v3 python3 scripts/atlas3/runs.py          # translate and write the parts
    ATLAS3_DIR=work/atlas-v3 python3 scripts/atlas3/runs.py --send   # and load them

The first build kept of each live run (`source-unit_ngrams`, pull.py) its crops, graphemes, hand and
book. This adds, for every run of the local copy `out/atlas-runs.sqlite`, found by its first crop
through `out/mirror.sqlite` (only read) and its length:

- `runs.form`: its text where it differs from its graphemes, and `runs.vertical`;
- `run_forms`: the site's count of each such form, from the local runs with their forms, so it adds
  up with `run_stats`, as the old `ngram_forms` added up with the site's `ngram_counts`;
- `run_books`: each book's BOOK_RUNS most frequent runs of each length, as the old `ngram_counts`
  counted them per book.

A run with no text (graphemes '') is counted in neither. A local run with no live row, or a live run
the local copy lacks, keeps its values and is counted in `out/runs-report.txt`; check.py lists them.
Only what differs from the local copy is sent, in parts named by their content and confirmed by their
stamps in the database's `imports`, then replayed on the local copy. Every part removes each count
row before writing it, so a part applies again harmlessly; a whole part sent again fails on its stamp,
which D1 already holds, and D1 rolls the part back. `--send` first replays the parts of earlier runs
whose stamps D1 holds and the local copy lacks (one read of `imports`), and applies migration 0002
(stamp `migration:0002`) where the local copy lacks it. A dry run (the default) writes the parts and
the figures and sends nothing.

The publisher keeps all three from then on (publish.py), from the `text` and `vertical` of the
catalogue's `unit_ngrams`.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import sqlite3
import sys

from build import BOOK_RUNS, HERE, OUT, run_form
from load_history import GROUP, built, stamped
from publish import CF, applied, insert, lit, query, send

DATABASE = "glyph-atlas-v3-runs"
SCHEMA = CF / "migrations-atlas-runs"
RUN_KEY = ("size", "graphemes", "hand", "shuffle", "first")


def source():
    path = HERE / "source-unit_ngrams.sqlite"
    if not path.exists():
        sys.exit(f"{path.name} is missing; pull.py unit_ngrams copies it")
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    if lacking := {"first", "size", "text", "graphemes", "vertical"} - {r[1] for r in db.execute("PRAGMA table_info(unit_ngrams)")}:
        sys.exit(f"{path.name} lacks {', '.join(sorted(lacking))}; pull.py --fresh unit_ngrams copies it again")
    return db


def has(db, table, column=None):
    if column is None:
        return db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone() is not None
    return any(r[1] == column for r in db.execute(f"PRAGMA table_info({table})"))


def translate(migrated):
    """out/runs-forms.sqlite: each local run's live form and direction and the counts they give, with
    the local copy attached as `local`; the runs whose values change; and how many runs were left as
    they are, by reason. `migrated`: the local copy has 0002's columns and tables."""
    path = OUT / "runs-forms.sqlite"
    path.unlink(missing_ok=True)
    source().close()
    out = sqlite3.connect(f"file:{path}", uri=True)
    out.create_function("run_form", 2, run_form, deterministic=True)
    for name, file in (("src", HERE / "source-unit_ngrams.sqlite"), ("mirror", OUT / "mirror.sqlite"), ("local", OUT / "atlas-runs.sqlite")):
        out.execute(f"ATTACH ? AS {name}", (f"file:{file}?mode=ro",))
    out.executescript("""
      CREATE TABLE live(first INTEGER, size INTEGER, form TEXT, vertical INTEGER, PRIMARY KEY(first, size)) WITHOUT ROWID;
      CREATE TABLE run_forms(size INTEGER, graphemes TEXT, form TEXT, n INTEGER, down INTEGER, PRIMARY KEY(size, graphemes, form)) WITHOUT ROWID;
      CREATE TABLE run_books(book INTEGER, size INTEGER, graphemes TEXT, n INTEGER, down INTEGER, PRIMARY KEY(book, size, graphemes)) WITHOUT ROWID;
      INSERT OR REPLACE INTO live SELECT i.id, u.size, run_form(u.text, u.graphemes), coalesce(u.vertical, 0)
        FROM src.unit_ngrams u JOIN mirror.ids i ON i.text = u.first;
    """)
    form, vertical = ("r.form", "r.vertical") if migrated else ("NULL", "1")
    # Every local run with its live values where it has them (`l`), its own where not.
    out.execute(f"""CREATE TEMP TABLE now AS SELECT r.size, r.graphemes, r.hand, r.shuffle, r.first, r.book,
        {form} AS was_form, {vertical} AS was_vertical, l.first IS NOT NULL AS live,
        iif(l.first IS NULL, {form}, l.form) AS form, iif(l.first IS NULL, {vertical}, l.vertical) AS vertical
      FROM local.runs r LEFT JOIN live l ON l.first = r.first AND l.size = r.size""")
    out.executescript("""
      INSERT INTO run_forms SELECT size, graphemes, form, count(*), sum(vertical) FROM now
        WHERE graphemes <> '' AND form IS NOT NULL GROUP BY size, graphemes, form;
      CREATE TEMP TABLE books AS SELECT book, size, graphemes, count(*) AS n, sum(vertical) AS down FROM now
        WHERE graphemes <> '' GROUP BY book, size, graphemes;
    """)
    out.execute(f"""INSERT INTO run_books SELECT book, size, graphemes, n, down FROM (SELECT *, row_number()
        OVER (PARTITION BY book, size ORDER BY n DESC, graphemes) AS r FROM books) WHERE r <= {BOOK_RUNS}""")
    out.commit()
    changed = [(r[:5], (r[5], r[6])) for r in out.execute(
        "SELECT size, graphemes, hand, shuffle, first, form, vertical FROM now WHERE live AND (form IS NOT was_form OR vertical IS NOT was_vertical)")]
    left = {}
    for reason, n in (("live runs whose first crop the mirror lacks",
                       out.execute("SELECT count(*) FROM src.unit_ngrams u WHERE NOT EXISTS (SELECT 1 FROM mirror.ids i WHERE i.text = u.first)").fetchone()[0]),
                      ("local runs with no live row, kept as they are", out.execute("SELECT count(*) FROM now WHERE NOT live").fetchone()[0]),
                      ("live runs the local copy lacks (publish.py adds them)", out.execute(
                          "SELECT count(*) FROM live l WHERE NOT EXISTS (SELECT 1 FROM local.runs r WHERE r.first = l.first AND r.size = l.size)").fetchone()[0])):
        if n:
            left[reason] = n
    return out, changed, left


def statements(out, changed, migrated):
    """Groups of statements that turn the local copy's forms and counts into the translation's; a row's
    statements stay in one group. Also, per table, the rows removed, added and changed, and the rows
    written: one per statement that finds its row, so the DELETE ahead of a new row counts nothing."""
    groups, changes = [], {}

    def add(table, rows, removed=0, added=0):
        for i in range(0, len(rows), GROUP):
            groups.append([s for r in rows[i:i + GROUP] for s in r])
        changes[table] = {"removed": removed, "added": added, "changed": len(rows) - removed - added,
                          "written": sum(len(r) for r in rows) - added}

    add("runs", [[f"UPDATE runs SET form={lit(form)}, vertical={vertical} WHERE "
                  + " AND ".join(f"{c}={lit(v)}" for c, v in zip(RUN_KEY, key)) + ";"] for key, (form, vertical) in changed])
    for table, key in (("run_forms", ("size", "graphemes", "form")), ("run_books", ("book", "size", "graphemes"))):
        cols = (*key, "n", "down")
        held = {r[:3]: r for r in out.execute(f"SELECT {','.join(cols)} FROM local.{table}")} if migrated else {}
        new = {r[:3]: r for r in out.execute(f"SELECT {','.join(cols)} FROM main.{table}")}

        def remove(r, table=table, key=key):
            return f"DELETE FROM {table} WHERE " + " AND ".join(f"{c}={lit(v)}" for c, v in zip(key, r)) + ";"
        rows = [[remove(r)] for k, r in sorted(held.items()) if k not in new]
        removed = len(rows)
        rows += [[remove(r), insert(table, cols, r)] for k, r in sorted(new.items()) if held.get(k) != r]
        add(table, rows, removed, sum(k not in held for k in new))
    return groups, changes


def figures(out, changes, left):
    kinds = ("removed", "added", "changed", "written")
    lines = ["table\trows\t" + "\t".join(kinds)]
    for table in ("runs", "run_forms", "run_books"):
        n = out.execute(f"SELECT count(*) FROM {'live' if table == 'runs' else 'main.' + table}").fetchone()[0]
        lines.append(f"{table}\t{n}\t" + "\t".join(str(changes[table][k]) for k in kinds))
    lines.append(f"rows written: {sum(c['written'] for c in changes.values())} (a changed run its UPDATE; a removed count "
                 "row 1; a new one its INSERT; a changed one its DELETE and its INSERT)")
    for reason, n in sorted(left.items()):
        lines.append(f"{n} {reason}")
    return "\n".join(lines) + "\n"


def migrate(runs):
    """Migration 0002 on D1 and the local copy, once; a layout built with it already has its columns."""
    if has(runs, "runs", "form"):
        return
    path = OUT / "runs-load" / "0002.sql"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(stamped((SCHEMA / "0002_forms.sql").read_text(), "migration:0002"))
    if not applied(DATABASE, "migration:0002") and not send(DATABASE, path, "migration:0002"):
        sys.exit("migration 0002 did not apply; rerun")
    runs.executescript("BEGIN;\n" + path.read_text() + "COMMIT;\n")
    print("migration 0002 applied", flush=True)


def catch_up(runs):
    """Replay on the local copy the parts of earlier runs that D1 holds and the local copy lacks, as a
    run stopped between the two leaves them, so the difference is taken from what D1 holds."""
    local = {r[0] for r in runs.execute("SELECT stamp FROM imports WHERE stamp LIKE 'runs-%'")}
    for r in query(DATABASE, "SELECT stamp FROM imports WHERE stamp LIKE 'runs-%' ORDER BY at, stamp"):
        if r["stamp"] in local:
            continue
        run, part = r["stamp"].split(".", 1)
        path = OUT / "runs-load" / run / f"{part}.sql"
        if not path.exists():
            sys.exit(f"{DATABASE} holds {r['stamp']}, whose part {path} is gone; the local copy cannot follow it")
        runs.executescript("BEGIN;\n" + path.read_text() + "COMMIT;\n")
        print(f"replayed {r['stamp']} from D1's imports", flush=True)


def deliver(run, runs):
    """Each part of the run to D1, then to the local copy, in order."""
    for part in sorted(run.glob("*.sql")):
        stamp = f"{run.name}.{part.stem}"
        if runs.execute("SELECT 1 FROM imports WHERE stamp=?", (stamp,)).fetchone():
            continue
        if not send(DATABASE, part, stamp):
            sys.exit(f"{part.name} did not apply; rerun to finish")
        runs.executescript("BEGIN;\n" + part.read_text() + "COMMIT;\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--send", action="store_true", help=f"load the parts into {DATABASE}")
    args = parser.parse_args()
    lock = open(OUT / "mirror.lock", "w")  # noqa: SIM115 -- held until the process exits
    fcntl.flock(lock, fcntl.LOCK_EX)
    mirror = sqlite3.connect(f"file:{OUT / 'mirror.sqlite'}?mode=ro", uri=True)
    if has(mirror, "pending") and mirror.execute("SELECT 1 FROM pending").fetchone():
        sys.exit("a publish is unfinished; finish it with publish.py first")
    # A dry run only reads the local copy.
    runs = sqlite3.connect(OUT / "atlas-runs.sqlite") if args.send else \
        sqlite3.connect(f"file:{OUT / 'atlas-runs.sqlite'}?mode=ro", uri=True)
    if args.send:
        runs.execute("CREATE TABLE IF NOT EXISTS imports(stamp TEXT PRIMARY KEY, at INTEGER NOT NULL)")
        runs.commit()
        build = mirror.execute("SELECT value FROM meta WHERE key='build'").fetchone()[0]
        if not applied("glyph-atlas-v3", f"build:{build}"):
            sys.exit(f"glyph-atlas-v3 was not loaded from build {build}; the local copy does not describe it")
        migrate(runs)
        catch_up(runs)
    # A dry run before 0002 compares with its columns and tables as they start: no forms, every run vertical.
    migrated = has(runs, "runs", "form")
    out, changed, left = translate(migrated)
    groups, changes = statements(out, changed, migrated)
    report = figures(out, changes, left)
    (OUT / "runs-report.txt").write_text(report)
    print(report, end="", flush=True)
    if not groups:
        print("the local copy already holds these forms; nothing to send", flush=True)
        return
    digest = hashlib.sha256("\n".join(s for g in groups for s in g).encode()).hexdigest()[:12]
    run = OUT / "runs-load" / f"runs-{digest}"
    n = built(run, "rows", groups)
    print(f"{n if n is not None else 'existing'} parts -> {run}", flush=True)
    if not args.send:
        print("dry run: nothing sent", flush=True)
        return
    deliver(run, runs)
    print("written forms loaded", flush=True)


if __name__ == "__main__":
    main()
