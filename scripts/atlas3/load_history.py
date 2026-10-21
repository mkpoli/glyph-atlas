"""Load the translated history (`out/history.sqlite`, from history.py) into glyph-atlas-v3.

    ATLAS3_DIR=work/atlas-v3 python3 scripts/atlas3/load_history.py [--dry-run]

Decision record 0006. Every step writes SQL parts under `out/history-load/<name>/`, named by the
content of `out/history.sqlite`, so a rerun on the same translation skips what D1 already holds; each
part is confirmed by its stamp in `imports` and replayed on the local copy. The rows sent are the
difference between the translation and the local copy: a translation of a later copy (history.py keeps
the ids an earlier load gave) sends its new rows, its changed rows (a DELETE and the INSERT) and the
DELETE of rows the source no longer has, and nothing already loaded. Mark events are the load's own
(no `legacy`) and stay out of the difference.

1. Each migration in MIGRATIONS (stamps `migration:<number>`), on D1 and the local copy, once;
   the mirror's `codes` take the two mark states 0003 numbers, so the publisher never numbers them
   differently. 0008 holds form anchors. 0009 gives events what an undo restores; 0010 the labels each showed and named, and
   the corpus rows and flags; 0013 keeps the character before a forms decision.
2. The rows of every history table that differ from the local copy.
3. Marks: a pending crop with a mark takes the mark as its state, one revision past its own, with an
   event at the round that marked it. A mark on a crop that is not pending is left out, as the old
   site showed it only on pending crops; a crop already in its mark's state was set by an earlier load.
4. Cuts: `out/history-cuts.sqlite`, a catalogue for `publish.py`: the records of the crops with an earlier
   box, naming their cuts, and `cut_facts`, when each crop's current cut was recorded and its other encodings.

Run `answers.py` afterwards: marks change the counts by state.
"""

from __future__ import annotations

import argparse
import fcntl
import gzip
import hashlib
import json
import shutil
import sqlite3
import sys

from build import HERE, OUT
from publish import CF, STATEMENT_BYTES, applied, lit, send, write_parts

SCHEMA = CF / "migrations-atlas"
# Tables in the order their rows load: who and which request first, then what names them.
TABLES = ("actors", "submissions", "events", "seen", "skips", "favourites", "actor_stats", "suspects", "shapes",
          "assertions", "assertion_actions", "assertion_evidence", "assertion_premises", "current_claims",
          "ledger_submissions", "rejections", "representations", "forms", "form_decisions", "corpus_follow", "form_bases", "written_forms", "corpus_rows",
          "corpus_flags")
# Each migration the translated rows need, in order, and a query that finds it in the local copy.
MIGRATIONS = (("0003", "0003_history.sql", "SELECT 1 FROM sqlite_master WHERE name='actors'"),
              ("0008", "0008_representations.sql", "SELECT 1 FROM sqlite_master WHERE name='representations'"),
              ("0009", "0009_undo.sql", "SELECT 1 FROM pragma_table_info('events') WHERE name='reverses'"),
              ("0010", "0010_people.sql", "SELECT 1 FROM sqlite_master WHERE name='corpus_rows'"),
              ("0013", "0013_forms.sql", "SELECT 1 FROM sqlite_master WHERE name='form_bases'"))
GROUP = 500


def source(table):
    db = sqlite3.connect(f"file:{HERE / f'source-{table}.sqlite'}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    return db


def deliver(run, atlas):
    """Each part of the run to D1, then to the local copy, in order."""
    for part in sorted(run.glob("*.sql")):
        stamp = f"{run.name}.{part.stem}"
        if atlas.execute("SELECT 1 FROM imports WHERE stamp=?", (stamp,)).fetchone():
            continue
        if not send("glyph-atlas-v3", part, stamp):
            sys.exit(f"{part.name} did not apply; rerun to finish")
        atlas.executescript("BEGIN;\n" + part.read_text() + "COMMIT;\n")


def stamped(sql, stamp):
    return sql + f"\nINSERT INTO imports(stamp, at) VALUES({lit(stamp)}, unixepoch());\n"


def migrate(history, atlas, mirror, dry):
    """Each migration in MIGRATIONS on D1 and the local copy, once; a layout built with one already has it."""
    # The mirror numbers new codes after the highest it holds; the two mark states must be the
    # migration's numbers before anything else claims them.
    taken = dict(mirror.execute("SELECT key, code FROM codes WHERE field='state'"))
    for key, code in history.execute("SELECT key, code FROM codes WHERE field='state'"):
        if taken.get(key, code) != code or (key not in taken and code in taken.values()):
            sys.exit(f"the mirror numbers states {taken}; migration 0003 numbers {key} {code}")
    if dry:
        print("dry run: the migrations and the mirror's mark states not written", flush=True)
        return
    mirror.executemany("INSERT OR IGNORE INTO codes(field, key, code) VALUES(?, ?, ?)",
                       history.execute("SELECT field, key, code FROM codes WHERE field='state'").fetchall())
    mirror.commit()
    for number, file, present in MIGRATIONS:
        if atlas.execute(present).fetchone():
            continue
        stamp = f"migration:{number}"
        path = OUT / "history-load" / f"{number}.sql"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(stamped((SCHEMA / file).read_text(), stamp))
        if not applied("glyph-atlas-v3", stamp) and not send("glyph-atlas-v3", path, stamp):
            sys.exit(f"migration {number} did not apply; rerun")
        atlas.executescript("BEGIN;\n" + path.read_text() + "COMMIT;\n")
        print(f"migration {number} applied", flush=True)


# D1 refuses a statement over 100 KB. A row past STATEMENT_BYTES goes in with its long text columns
# empty, and each is then appended in pieces of CHUNK characters (at most four bytes each), in the same
# part, so D1 holds the value whole.
LONG_BYTES, CHUNK = 10_000, 20_000


def row_statements(table, cols, keys, r):
    names = ",".join(f'"{c}"' for c in cols)
    insert = f'INSERT INTO "{table}"({names}) VALUES({",".join(lit(v) for v in r)});'
    if len(insert.encode()) <= STATEMENT_BYTES:
        return [insert]
    long = {i for i, v in enumerate(r) if isinstance(v, str) and len(lit(v).encode()) > LONG_BYTES}
    statements = [f'INSERT INTO "{table}"({names}) VALUES({",".join("\'\'" if i in long else lit(v) for i, v in enumerate(r))});']
    where = " AND ".join(f'"{c}" = {lit(r[cols.index(c)])}' for c in keys)
    for i in sorted(long):
        for start in range(0, len(r[i]), CHUNK):
            statements.append(f'UPDATE "{table}" SET "{cols[i]}" = "{cols[i]}" || {lit(r[i][start:start + CHUNK])} WHERE {where};')
    if max(len(st.encode()) for st in statements) > STATEMENT_BYTES:
        sys.exit(f"a row of {table} needs a statement over {STATEMENT_BYTES} bytes even in pieces")
    return statements


def changes(history, atlas):
    """Statements in groups that turn the local copy's history rows into the translation's, and the count
    of new, changed and gone rows per table; a row's statements stay in one group. Rows gone are deleted
    first, last table first, so a new row never meets a gone one on a unique column. Events the load
    wrote for marks (no `legacy`) are left out of the comparison."""
    groups, gone, counts = [], [], {}
    for table in TABLES:
        info = list(history.execute(f'PRAGMA table_info("{table}")'))
        cols = [r[1] for r in info]
        keys = [r[1] for r in sorted(info, key=lambda r: r[5]) if r[5]] or cols
        at = [cols.index(k) for k in keys]
        names = ",".join(f'"{c}"' for c in cols)
        where = " WHERE legacy IS NOT NULL" if table == "events" else ""
        try:
            old = {tuple(r[i] for i in at): r for r in atlas.execute(f'SELECT {names} FROM "{table}"{where}')}
        except sqlite3.OperationalError:
            old = {}  # a dry run before the migrations: the table is not there yet

        def remove(key, table=table, keys=keys):
            return f'DELETE FROM "{table}" WHERE {" AND ".join(f"\"{k}\" IS {lit(v)}" for k, v in zip(keys, key))};'

        n, batch = {"new": 0, "changed": 0, "gone": 0}, []
        for r in history.execute(f'SELECT {names} FROM "{table}" ORDER BY rowid' if table not in
                                 ("seen", "skips", "favourites", "assertion_evidence", "assertion_premises", "current_claims",
                                  "representations", "corpus_flags")
                                 else f'SELECT {names} FROM "{table}"'):
            key = tuple(r[i] for i in at)
            was = old.pop(key, None)
            if was == r:
                continue
            n["changed" if was is not None else "new"] += 1
            batch += ([remove(key)] if was is not None else []) + row_statements(table, cols, keys, r)
            if len(batch) >= GROUP:
                groups.append(batch)
                batch = []
        if batch:
            groups.append(batch)
        n["gone"] = len(old)
        gone.append([remove(key) for key in old])
        counts[table] = n
    deletes = [g for table in reversed(gone) for g in (table[i:i + GROUP] for i in range(0, len(table), GROUP))]
    return deletes + groups, counts


def marks(history, atlas):
    """Each pending marked crop takes its mark as state, one revision past its own, with the event of
    the latest round that showed it unflagged; of rounds at one time, the highest submission id, as
    history.py picks it."""
    kinds = dict(history.execute("SELECT key, code FROM codes WHERE field='kind'"))
    states = dict(history.execute("SELECT key, code FROM codes WHERE field='state'"))
    pending = dict(atlas.execute("SELECT key, code FROM codes WHERE field='state'"))["pending"]
    statements, left = [], 0
    for m in json.loads((OUT / "history-marks.json").read_text()):
        crop = atlas.execute("SELECT state, revision, cut FROM crops WHERE id=?", (m["crop"],)).fetchone()
        if crop is not None and crop[0] == states[m["state"]]:
            continue  # set by an earlier load
        if crop is None or crop[0] != pending:
            left += 1
            continue
        state, revision, cut = states[m["state"]], crop[1], crop[2]
        shown = history.execute("SELECT s.submission, s.at, b.actor FROM seen s JOIN submissions b ON b.id = s.submission "
                                "WHERE s.crop = ? AND s.cut = ? AND b.undone = 0 ORDER BY s.at DESC, s.submission DESC LIMIT 1",
                                (m["crop"], cut)).fetchone()
        if shown is None:
            sys.exit(f"crop {m['crop']} is marked {m['state']} with no round that showed it at its cut {cut}")
        statements.append(
            f"INSERT INTO events(crop, revision, cut, gen, actor, submission, at, kind, state) VALUES("
            f"{m['crop']}, {revision + 1}, {cut}, 0, {shown[2]}, {shown[0]}, {shown[1]}, {kinds[m['state']]}, {state});")
        statements.append(f"UPDATE crops SET state = {state}, revision = {revision + 1} WHERE id = {m['crop']} AND revision = {revision};")
    return [statements[i:i + GROUP] for i in range(0, len(statements), GROUP)], left


def cuts_catalogue():
    """The cuts history-cuts.json names, as a catalogue for publish.py: a `units` record naming its earlier
    cuts for each collected crop with one, and `cut_facts`, when each crop's current cut was recorded and
    the other encodings of it, for every crop the old site recorded versions of."""
    cuts = json.loads((OUT / "history-cuts.json").read_text())
    shards = {}

    def place(image):
        key = image.rsplit("/", 1)[-1].split(".")[0]
        if key[:3] not in shards:
            path = OUT / "media" / f"{key[:3]}.json.gz"
            shards[key[:3]] = json.loads(gzip.decompress(path.read_bytes())) if path.exists() else {"objects": [], "keys": {}}
        s = shards[key[:3]]
        found = s["keys"].get(key[3:])
        return [s["objects"][found[0]], found[1], found[2]] if found else None

    path = OUT / "history-cuts.sqlite"
    path.unlink(missing_ok=True)
    out = sqlite3.connect(path)
    units = source("units")
    cols = [r[1] for r in units.execute("PRAGMA table_info(units)") if r[1] != "_rowid"]
    out.execute(f"CREATE TABLE units({','.join(cols)})")
    out.execute("CREATE TABLE cut_facts(id TEXT PRIMARY KEY, cut_at TEXT, encodings TEXT NOT NULL)")
    out.executemany("INSERT INTO cut_facts VALUES(?,?,?)",
                    [(text, v.get("cut_at"), json.dumps(v["encodings"], ensure_ascii=False)) for text, v in sorted(cuts.items())])
    earlier = {text: v["cuts"] for text, v in cuts.items() if v["cuts"]}
    missing = []
    for text, versions in earlier.items():
        r = units.execute("SELECT * FROM units WHERE id=? ORDER BY _rowid DESC LIMIT 1", (text,)).fetchone()
        if r is None:
            missing.append(text)
            continue
        data = json.loads(r["data"])
        data["cuts"] = [{"cut": v["cut"], "box": v["box"], "image_sha256": v["pixels"], "image": v["image"],
                         **({"at": v["at"]} if v.get("at") else {}),
                         **({"image_place": place(v["image"])} if v.get("image") and place(v["image"]) else {})}
                        for v in versions]
        out.execute(f"INSERT INTO units VALUES({','.join('?' * len(cols))})",
                    [json.dumps(data, ensure_ascii=False) if c == "data" else r[c] for c in cols])
    out.commit()
    if missing:
        sys.exit(f"{len(missing)} crops with an earlier box have no live record: {missing[:5]}")
    print(f"{len(earlier)} crops with an earlier box, {len(cuts)} with recorded versions -> {path}; "
          "publish it with publish.py", flush=True)


def built(final, label, groups, extra=None, *, manifest=False):
    """Write a run's parts under a temporary name and rename the folder once complete, so a folder
    that exists holds every part; stamps name the final folder. None when it was already built."""
    if final.exists():
        return None
    tmp = final.with_name(final.name + ".building")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    n = write_parts(tmp, label, groups)
    for part in tmp.glob("*.sql"):
        part.write_text(part.read_text().replace(f"'{tmp.name}.{label}.", f"'{final.name}.{label}-"))
    for file, content in (extra or {}).items():
        (tmp / file).parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            (tmp / file).write_bytes(content)
        else:
            (tmp / file).write_text(content)
    if manifest:
        hashes = {part.name: hashlib.sha256(part.read_bytes()).hexdigest() for part in sorted(tmp.glob("*.sql"))}
        (tmp / "manifest.json").write_text(json.dumps(hashes, sort_keys=True))
    tmp.rename(final)
    return n


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="write the parts and the catalogue, send nothing")
    args = parser.parse_args()
    lock = open(OUT / "mirror.lock", "w")  # noqa: SIM115 -- held until the process exits
    fcntl.flock(lock, fcntl.LOCK_EX)
    history = sqlite3.connect(f"file:{OUT / 'history.sqlite'}?mode=ro", uri=True)
    atlas = sqlite3.connect(OUT / "atlas.sqlite")
    mirror = sqlite3.connect(OUT / "mirror.sqlite")
    if (OUT / "history-report.txt").read_text().rstrip().endswith("rows stop the move"):
        sys.exit("the translation stopped the move; run history.py until it exits 0")

    migrate(history, atlas, mirror, args.dry_run)
    name = "history-" + hashlib.sha256((OUT / "history.sqlite").read_bytes()).hexdigest()[:12]
    run = OUT / "history-load" / name
    # The ids history.py kept and gave are those of the loads it saw; another load since would clash.
    base = {r[0] for r in history.execute("SELECT stamp FROM base")}
    since = {r[0] for r in atlas.execute("SELECT stamp FROM imports WHERE stamp LIKE 'history-%'")
             if not r[0].startswith((f"{name}.", f"{name}-marks."))}
    if since != base:
        sys.exit(f"the local copy holds history loads the translation did not see ({sorted(since ^ base)[:3]}); "
                 "run history.py again")
    if not run.exists():
        groups, counts = changes(history, atlas)
        figures = "".join(f"{t}: {c['new']} new, {c['changed']} changed, {c['gone']} gone\n" for t, c in counts.items())
        built(run, "rows", groups, {"figures.txt": figures})
        print(f"{figures}{len(groups)} groups of statements -> {run}", flush=True)
    else:
        print(f"{run} was built before:\n{(run / 'figures.txt').read_text()}", end="", flush=True)
    if args.dry_run:
        cuts_catalogue()
        print("dry run: nothing sent", flush=True)
        return
    deliver(run, atlas)
    # The marks are written once, from the crops as they stand before any is set; a rerun finishes
    # the same parts.
    marked = OUT / "history-load" / f"{name}-marks"
    if not marked.exists():
        groups, left = marks(history, atlas)
        built(marked, "marks", groups, {"left": str(left)})
    deliver(marked, atlas)
    set_ = sum(p.read_text().count("UPDATE crops") for p in marked.glob("*.sql"))
    print(f"history loaded; {set_} marks set, {(marked / 'left').read_text()} left on crops no longer pending", flush=True)
    cuts_catalogue()


if __name__ == "__main__":
    main()
