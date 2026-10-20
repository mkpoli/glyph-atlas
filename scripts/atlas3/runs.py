"""Move how the runs are written into glyph-atlas-v3-runs (`migrations-atlas-runs/0002_forms.sql`) and R2.

    ATLAS3_DIR=work/atlas-v3 python3 scripts/atlas3/runs.py          # translate, write the parts and answers
    ATLAS3_DIR=work/atlas-v3 python3 scripts/atlas3/runs.py --send   # and load them

The first build kept each run's crops and graphemes, but not how its crops are written. This adds:

- `run_written`: each crop of a local run whose written character differs from its grapheme family's
  head in the local `graphemes`, with that character. A crop's written character is the one the old
  triggers wrote into its runs' text (migration 0063): its `units` row's character, else its
  `corpus_units` character, else its corpus label, from the copies. A run's written form is then its
  crops' rows, each else the head its graphemes name, as the old `unit_ngrams.text` held it; check.py
  compares the two run by run;
- `run_text`, a table of the local copy alone: each local run's live text, from which the answers count;
- the runs' R2 answers (`run_answers.py`): Explore's grid of each length with the forms each run
  gathers, each book's grid, and the forms of every run sharded by length and first code point.

The translation is `out/runs-forms.sqlite` (`written`, `texts`).

Only what differs from the local copy is sent, in parts named by their content and confirmed by their
stamps in the database's `imports`, then replayed on the local copy; a part applies again harmlessly. A
crop's row is written once, by an upsert that skips an unchanged row. `--send` first replays the parts of
earlier runs whose stamps D1 holds and the local copy lacks (one read of `imports`), applies migration
0002 (stamp `migration:0002`) where the local copy lacks it, then sends the rows, uploads the answers
whose bytes changed and, last, inserts one stamp into glyph-atlas-v3's `imports`, which moves the
publication key the runs' cached answers are kept under. A dry run (the default) writes the parts, the
answers and the figures (`out/runs-report.txt`) and sends nothing; the answers it writes go up with
the next `--send`.

The publisher keeps all of this from then on (publish.py), and a review that changes a crop's written
character writes its row.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import sqlite3
import sys

from build import HERE, OUT
from load_history import GROUP, built, stamped
from publish import CF, applied, lit, query, send, upload
from run_answers import ANSWERS, PREFIX, RUN_TEXT, Answers, pending, uploaded

DATABASE = "glyph-atlas-v3-runs"
SCHEMA = CF / "migrations-atlas-runs"


def has(db, table):
    return db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone() is not None


def written_statements(want, have):
    """The statements that turn `have` ({crop: char}) into `want`: an upsert for each new or changed
    crop, a delete for each crop no longer written apart from its head."""
    out = [f"DELETE FROM run_written WHERE crop={crop};" for crop in sorted(have.keys() - want.keys())]
    out += [f"INSERT INTO run_written(crop,char) VALUES({crop},{lit(char)}) ON CONFLICT(crop) DO UPDATE SET char=excluded.char "
            "WHERE char<>excluded.char;" for crop, char in sorted(want.items()) if have.get(crop) != char]
    return out


def migrate(runs):
    """Migration 0002 on D1 and the local copy, once; a layout built with it already has its table."""
    if has(runs, "run_written"):
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


TRANSLATION = OUT / "runs-forms.sqlite"


def translate():
    """out/runs-forms.sqlite from the copies and the local copies: `written` (crop, char) and `texts`
    (first, size, text); with how many runs and crops were left out, by reason."""
    TRANSLATION.unlink(missing_ok=True)
    out = sqlite3.connect(f"file:{TRANSLATION}", uri=True)
    for name, path in (("src", HERE / "source-unit_ngrams.sqlite"), ("u", HERE / "source-units.sqlite"),
                       ("k", HERE / "source-corpus_units.sqlite"), ("m", OUT / "mirror.sqlite"), ("a", OUT / "atlas.sqlite"),
                       ("r", OUT / "atlas-runs.sqlite")):
        if not path.exists():
            sys.exit(f"{path.name} is missing")
        out.execute(f"ATTACH ? AS {name}", (f"file:{path}?mode=ro",))
    out.executescript("""
      CREATE TABLE texts(first INTEGER, size INTEGER, text TEXT NOT NULL, PRIMARY KEY(first, size)) WITHOUT ROWID;
      INSERT OR REPLACE INTO texts SELECT i.id, u.size, u.text FROM src.unit_ngrams u JOIN m.ids i ON i.text = u.first
        WHERE u.text IS NOT NULL;
      CREATE TEMP TABLE members(crop INTEGER PRIMARY KEY);
      INSERT OR IGNORE INTO members SELECT first FROM r.runs UNION SELECT second FROM r.runs
        UNION SELECT third FROM r.runs WHERE third IS NOT NULL;
      CREATE TEMP TABLE named(crop INTEGER PRIMARY KEY, t TEXT NOT NULL);
      INSERT INTO named SELECT c.crop, i.text FROM members c JOIN m.ids i ON i.id = c.crop;
      CREATE TEMP TABLE uc(t TEXT PRIMARY KEY, c TEXT) WITHOUT ROWID;
      INSERT OR REPLACE INTO uc SELECT id, character FROM u.units WHERE id IN (SELECT t FROM named) ORDER BY _rowid;
      CREATE TEMP TABLE kc(t TEXT PRIMARY KEY, c TEXT, l TEXT) WITHOUT ROWID;
      INSERT OR REPLACE INTO kc SELECT id, character, label FROM k.corpus_units WHERE id IN (SELECT t FROM named) ORDER BY _rowid;
      CREATE TABLE written(crop INTEGER PRIMARY KEY, char TEXT NOT NULL);
      INSERT INTO written SELECT crop, w FROM (SELECT n.crop, coalesce(iif(uc.t IS NOT NULL, uc.c, kc.c), kc.l) AS w
        FROM named n LEFT JOIN uc ON uc.t = n.t LEFT JOIN kc ON kc.t = n.t)
        WHERE w IS NOT NULL AND w IS NOT coalesce((SELECT h.text FROM a.graphemes g JOIN a.graphemes h ON h.id = g.head WHERE g.text = w), w);
    """)
    out.commit()
    left = {}
    for reason, sql in (("local runs with no live text", ("SELECT count(*) FROM r.runs x WHERE x.graphemes <> '' AND NOT EXISTS "
                                                          "(SELECT 1 FROM texts t WHERE t.first = x.first AND t.size = x.size)")),
                        ("run crops the mirror lacks", "SELECT (SELECT count(*) FROM members) - (SELECT count(*) FROM named)")):
        if n := out.execute(sql).fetchone()[0]:
            left[reason] = n
    return out, left


def answers():
    """Every answer, from the local runs with the translation's texts; {folder: [paths R2 lacks]}."""
    written = Answers(TRANSLATION)
    written.shards()
    written.books()
    written.grids()
    written.close()
    return pending()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--send", action="store_true", help=f"load the rows into {DATABASE} and the answers into R2")
    args = parser.parse_args()
    lock = open(OUT / "mirror.lock", "w")  # noqa: SIM115 -- held until the process exits
    fcntl.flock(lock, fcntl.LOCK_EX)
    mirror = sqlite3.connect(f"file:{OUT / 'mirror.sqlite'}?mode=ro", uri=True)
    if has(mirror, "pending") and mirror.execute("SELECT 1 FROM pending").fetchone():
        sys.exit("a publish is unfinished; finish it with publish.py first")
    # A dry run only reads the local copies.
    mode = "" if args.send else "?mode=ro"
    runs = sqlite3.connect(f"file:{OUT / 'atlas-runs.sqlite'}{mode}", uri=True)
    atlas = sqlite3.connect(f"file:{OUT / 'atlas.sqlite'}{mode}", uri=True)
    if args.send:
        build = mirror.execute("SELECT value FROM meta WHERE key='build'").fetchone()[0]
        if not applied("glyph-atlas-v3", f"build:{build}"):
            sys.exit(f"glyph-atlas-v3 was not loaded from build {build}; the local copy does not describe it")
        runs.execute("CREATE TABLE IF NOT EXISTS imports(stamp TEXT PRIMARY KEY, at INTEGER NOT NULL)")
        runs.commit()
        migrate(runs)
        catch_up(runs)
    out, left = translate()
    want = dict(out.execute("SELECT crop, char FROM written"))
    have = dict(runs.execute("SELECT crop, char FROM run_written")) if has(runs, "run_written") else {}
    statements = written_statements(want, have)
    changed = answers()
    objects = sum(len(paths) for paths in changed.values())
    size = sum(p.stat().st_size for paths in changed.values() for p in paths)
    total = sum(1 for _ in ANSWERS.rglob("*.json.gz"))
    report = (f"run_written\t{len(want)} rows\t{len(statements)} statements\t"
              f"{sum(c not in have for c in want)} added\t{len(have.keys() - want.keys())} removed\t"
              f"{sum(c in have and have[c] != v for c, v in want.items())} changed\n"
              f"rows written: {len(statements)} in {DATABASE}, and 1 stamp in glyph-atlas-v3\n"
              f"R2 answers: {objects} of {total} objects to upload, {size} bytes, under {PREFIX}\n"
              + "".join(f"  {folder or 'grid'}: {len(paths)}\n" for folder, paths in sorted(changed.items()))
              + "".join(f"{n} {reason}\n" for reason, n in sorted(left.items())))
    (OUT / "runs-report.txt").write_text(report)
    print(report, end="", flush=True)
    digest = hashlib.sha256(("\n".join(statements) + "".join(str(p) for v in changed.values() for p in v)).encode()).hexdigest()[:12]
    run = OUT / "runs-load" / f"runs-{digest}"
    groups = [statements[i:i + GROUP] for i in range(0, len(statements), GROUP)]
    n = built(run, "rows", groups) if groups else 0
    print(f"{n if n is not None else 'existing'} parts -> {run}", flush=True)
    if not args.send:
        print("dry run: nothing sent", flush=True)
        return
    if groups:
        deliver(run, runs)
    # The texts the answers count, in the local copy alone.
    runs.execute(RUN_TEXT)
    runs.execute("ATTACH ? AS tr", (f"file:{TRANSLATION}?mode=ro",))
    runs.executescript("BEGIN; DELETE FROM run_text; INSERT INTO run_text SELECT first, size, text FROM tr.texts; COMMIT;")
    runs.execute("DETACH tr")
    for folder, paths in sorted(changed.items()):
        upload(sorted(paths), PREFIX + (folder + "/" if folder else ""))
        uploaded(paths)
    # Last, the stamp that moves the publication key, so no answer is kept under it before the rows and
    # objects it describes are in place.
    stamp = f"{run.name}.answers"
    path = OUT / "runs-load" / f"{run.name}.answers.sql"
    path.write_text(f"INSERT INTO imports(stamp, at) VALUES({lit(stamp)}, unixepoch());\n")
    if not atlas.execute("SELECT 1 FROM imports WHERE stamp=?", (stamp,)).fetchone():
        if not send("glyph-atlas-v3", path, stamp):
            sys.exit("the publication stamp did not apply; rerun")
        atlas.executescript("BEGIN;\n" + path.read_text() + "COMMIT;\n")
    print("written forms and run answers loaded", flush=True)


if __name__ == "__main__":
    main()
