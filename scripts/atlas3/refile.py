"""File the runs of glyph-atlas-v3-runs again under the heads a reference load moved (`reference.py`).

A run is filed under its crops' family heads (没有), so a load that moves a head leaves the runs filed under
the old one behind: a search folds what a reader types to the head as it now is. Each load writes the heads
it moves beside its parts, `heads.json` ({character: [old head, new head]}), and `refile_moved` files their
runs once every part has landed:

- each run's crop that a grapheme claim reads stands under the head its claim reads, as the Worker files it
  (`refileRuns`); one written with a character that moved, under that character's new head where the run
  had it under the old one (a 备 under 留 goes to 備, the 留 beside it stays); any other part that is a head
  no longer, under the head it is of now. A crop read otherwise since the last publish waits for the next,
  as it does anyway;
- a run moves from the key it is read under in D1, its count with it (`publish.move_sql`), so two old keys
  that meet in one add up there, and a run the Worker moved meanwhile is left as the Worker left it;
- `run_written` names the character of a crop written apart from its new head, and drops one that is the head;
- the local copy then takes D1's rows (`publish.snapshot`), the answers of the books and shards those runs
  are in are written again and uploaded, and a stamp in glyph-atlas-v3 moves the publication key the runs'
  cached answers are kept under.

The heads and the runs are in two databases, so a load can stop between them, or between two parts of the
runs. The next `reference.py --send` goes on: it reads D1's rows again and sends what still differs. A load
whose runs are filed has the stamp `<load>.refiled` in the local runs copy, and is passed over.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from collections import Counter

import publish
from build import OUT
from run_answers import PREFIX, RUN_TEXT, Answers, pending, uploaded

HEADS = "heads.json"
# Runs read and filed at a time, by their first crop.
BATCH = 100
CROPS = ("first", "second", "third")


def members(run):
    return [run[c] for c in CROPS][:run["size"]]


def targets(run, own, claimed, moved):
    """The head each crop of a run is filed under, as the load leaves the heads; None where the run is not
    one grapheme a crop."""
    parts = publish.graphemes_of(run["graphemes"] or "")
    crops = members(run)
    if len(parts) != len(crops):
        return None
    out = []
    for crop, part in zip(crops, parts, strict=True):
        if crop in claimed:
            head = claimed[crop]
            out.append(moved[head][1] if head in moved else head)
            continue
        old, new = moved.get(own.get(crop), (None, None))
        out.append(new if part == old else moved[part][1] if part in moved else part)
    return out


def filing(rows, own, claimed, held, moved):
    """The statements that file `rows` under their targets, and how many runs move. `own` gives each crop's
    character, `claimed` the head its claim reads, `held` the characters `run_written` holds."""
    statements, moves, written = [], 0, {}
    for run in rows:
        heads = targets(run, own, claimed, moved)
        if heads is None or "".join(heads) == run["graphemes"]:
            continue
        statements += publish.move_sql(run, "".join(heads))
        moves += 1
        for crop, head, part in zip(members(run), heads, publish.graphemes_of(run["graphemes"]), strict=True):
            if head != part:
                written[crop] = head
    for crop, head in sorted(written.items()):
        char = own.get(crop)
        if char is None:
            continue
        if char != head and held.get(crop) != char:
            statements.append(publish.insert("run_written", ("crop", "char"), (crop, char)).replace("INSERT INTO", "INSERT OR REPLACE INTO", 1))
        elif char == head and crop in held:
            statements.append(f"DELETE FROM run_written WHERE crop={crop};")
    return statements, moves


def candidates(atlas, runs, moved):
    """The runs the moved heads can touch, {(first, size)} of the local copy: those filed under a character that
    moved, and those of the crops written with one."""
    found = {(first, size) for first, size, key in runs.execute("SELECT first, size, graphemes FROM runs")
             if key and not moved.keys().isdisjoint(publish.graphemes_of(key))}
    texts = json.dumps(sorted(moved), ensure_ascii=False)
    crops = [crop for (crop,) in atlas.execute(
        "SELECT c.id FROM json_each(?) j JOIN graphemes g ON g.text = j.value JOIN crops c ON c.char = g.id WHERE c.kind < 2", (texts,))]
    for i in range(0, len(crops), 400):
        marks = ",".join(map(str, crops[i:i + 400]))
        for column in CROPS:
            found |= set(runs.execute(f"SELECT first, size FROM runs WHERE {column} IN ({marks})"))
    return found


def characters(atlas, crops):
    """The character each crop is written with in the local copy."""
    return dict(atlas.execute("SELECT c.id, g.text FROM json_each(?) j JOIN crops c ON c.id = j.value JOIN graphemes g ON g.id = c.char",
                              (json.dumps(sorted(crops)),)))


def local_runs():
    path = OUT / "atlas-runs.sqlite"
    if not path.exists():
        sys.exit("the local copy of the runs is missing: the runs a moved head files elsewhere cannot be found")
    return path


#: The rows a filing changes, by the kind of its statements, in the order they are printed.
KINDS = (("runs moved to another key", "UPDATE OR IGNORE runs"), ("run_stats counted up", "INSERT INTO run_stats"),
         ("run_stats counted down", "UPDATE run_stats"), ("run_stats let go", "DELETE FROM run_stats"),
         ("run_written written", "INSERT OR REPLACE INTO run_written"), ("run_written dropped", "DELETE FROM run_written"))


def rehearsed(rows, stats, held, statements):
    """The rows each kind of `statements` changes, run in memory on the rows they name."""
    db = sqlite3.connect(":memory:")
    db.executescript((publish.CF / "migrations-atlas-runs" / "0001_init.sql").read_text()
                     + "CREATE TABLE run_written(crop INTEGER PRIMARY KEY, char TEXT NOT NULL);")
    db.executemany(f"INSERT INTO runs({','.join(publish.RUN_COLS)}) VALUES({','.join('?' * len(publish.RUN_COLS))})",
                   [[r[c] for c in publish.RUN_COLS] for r in rows])
    db.executemany("INSERT INTO run_stats(size, graphemes, n, down) VALUES(?,?,?,?)", stats)
    db.executemany("INSERT INTO run_written VALUES(?,?)", held.items())
    changed = Counter()
    for sql in statements:
        kind = next(label for label, start in KINDS if sql.startswith(start))
        changed[kind] += max(db.execute(sql).rowcount, 0)
    return {label: changed[label] for label, _ in KINDS}


def planned(atlas, moved):
    """What filing the runs of `moved` would change, from the local copies and the claims D1 holds: the rows by
    kind and the keys that lose runs. Writes nothing."""
    runs = sqlite3.connect(f"file:{local_runs()}?mode=ro", uri=True)
    wanted = candidates(atlas, runs, moved)
    runs.row_factory = sqlite3.Row
    # A run a claim has moved since the last publish stands so in D1 alone.
    live = {(r["first"], r["size"]): r for r in publish.query(publish.RUNS_DATABASE, (
        "SELECT DISTINCT r.* FROM run_moves m CROSS JOIN runs r ON r.first = m.first AND r.size = m.size"))}
    wanted |= {key for key, r in live.items() if not moved.keys().isdisjoint(publish.graphemes_of(r["graphemes"] or ""))}
    rows = [live.get(key) or dict(runs.execute("SELECT * FROM runs WHERE first=? AND size=?", key).fetchone()) for key in sorted(wanted)]
    crops = sorted({c for r in rows for c in members(r)})
    own, claimed = characters(atlas, crops), {}
    for i in range(0, len(crops), 400):
        claimed |= publish.claimed_heads(atlas, set(), crops[i:i + 400])
    marks = ",".join(map(str, crops))
    held = dict(tuple(r) for r in runs.execute(f"SELECT crop, char FROM run_written WHERE crop IN ({marks})")) \
        if runs.execute("SELECT 1 FROM sqlite_master WHERE name='run_written'").fetchone() else {}
    statements, _ = filing(rows, own, claimed, held, moved)
    after = {(r["size"], "".join(heads)) for r in rows if (heads := targets(r, own, claimed, moved))}
    keys = {(r["size"], r["graphemes"]) for r in rows}
    stats = [tuple(r) for size, key in sorted(keys | after) for r in runs.execute(
        "SELECT size, graphemes, n, down FROM run_stats WHERE size=? AND graphemes=?", (size, key))]
    runs.close()
    changed = rehearsed(rows, stats, held, statements)
    return changed, sorted({r["graphemes"] for r in rows if (heads := targets(r, own, claimed, moved)) and "".join(heads) != r["graphemes"]})


def refile(atlas, moved, name):
    """File the runs of `moved` in D1 and take them into the local copy; how many runs moved."""
    runs = sqlite3.connect(local_runs())
    runs.row_factory = sqlite3.Row
    publish.take_moves(runs)
    runs.row_factory = None
    wanted = candidates(atlas, runs, moved)
    runs.row_factory = sqlite3.Row
    firsts, count = sorted({first for first, _ in wanted}), 0
    for i in range(0, len(firsts), BATCH):
        marks = ",".join(map(str, firsts[i:i + BATCH]))
        rows = [r for r in publish.query(publish.RUNS_DATABASE, f"SELECT * FROM runs WHERE first IN ({marks})") if (r["first"], r["size"]) in wanted]
        crops = sorted({c for r in rows for c in members(r)})
        if not crops:
            continue
        ids = ",".join(map(str, crops))
        own = {r["id"]: r["char"] for r in publish.query("glyph-atlas-v3", publish.CROP_HEADS.format(ids=ids)) if r["char"]}
        held = {r["crop"]: r["char"] for r in publish.query(publish.RUNS_DATABASE, f"SELECT crop, char FROM run_written WHERE crop IN ({ids})")}
        statements, moves = filing(rows, own, publish.claimed_heads(atlas, set(), crops), held, moved)
        count += moves
        if not statements:
            continue
        body = "\n".join(statements)
        stamp = f"{name}.refile.{hashlib.sha256(body.encode()).hexdigest()[:12]}"
        path = OUT / "reference-load" / name / f"refile-{stamp.rsplit('.', 1)[1]}.run"
        path.write_text(body + f"\nINSERT INTO imports(stamp, at) VALUES({publish.lit(stamp)}, unixepoch());\n")
        if not publish.send(publish.RUNS_DATABASE, path, stamp):
            sys.exit(f"{path.name} did not apply; rerun to finish {name}")
    publish.snapshot(runs, wanted)
    runs.close()
    return count


def answer(atlas, name):
    """Write and upload the answers the filed runs owe, then stamp glyph-atlas-v3 so no cached answer stands."""
    runs = sqlite3.connect(local_runs())
    runs.execute(publish.ANSWERS_OWED)
    runs.execute(RUN_TEXT)
    owed = runs.execute("SELECT kind, a, b FROM answers_owed").fetchall()
    runs.commit()
    if not owed and not pending():
        return
    written = Answers()
    written.books(sorted({a for kind, a, _ in owed if kind == "book"}))
    written.shards({(a, b) for kind, a, b in owed if kind == "shard"})
    written.grids()
    written.close()
    for folder, paths in sorted(pending().items()):
        publish.upload(paths, PREFIX + (folder + "/" if folder else ""))
        uploaded(paths)
    stamp = f"{name}.runs"
    if not atlas.execute("SELECT 1 FROM imports WHERE stamp=?", (stamp,)).fetchone():
        path = OUT / "reference-load" / name / "runs.stamp"
        path.write_text(f"INSERT INTO imports(stamp, at) VALUES({publish.lit(stamp)}, unixepoch());\n")
        if not publish.send("glyph-atlas-v3", path, stamp):
            sys.exit("the publication stamp did not apply; rerun")
        atlas.executescript("BEGIN;\n" + path.read_text() + "COMMIT;\n")
    runs.executemany("DELETE FROM answers_owed WHERE kind=? AND a=? AND b=?", owed)
    runs.commit()
    runs.close()


def refile_moved(atlas):
    """File the runs of every load whose rows have all landed and whose runs are not filed yet."""
    for run in sorted((OUT / "reference-load").glob("reference-*")):
        parts = sorted(run.glob("*.sql")) if run.is_dir() and not run.name.endswith(".building") else []
        if not parts or not (run / HEADS).exists():
            continue
        done = f"{run.name}.refiled"
        with sqlite3.connect(local_runs()) as runs:
            runs.execute("CREATE TABLE IF NOT EXISTS imports(stamp TEXT PRIMARY KEY, at INTEGER NOT NULL)")
            if runs.execute("SELECT 1 FROM imports WHERE stamp=?", (done,)).fetchone():
                continue
        if any(not atlas.execute("SELECT 1 FROM imports WHERE stamp=?", (f"{run.name}.{p.stem}",)).fetchone() for p in parts):
            continue
        moved = {text: tuple(heads) for text, heads in json.loads((run / HEADS).read_text()).items()}
        count = refile(atlas, moved, run.name)
        answer(atlas, run.name)
        with sqlite3.connect(local_runs()) as runs:
            runs.execute("INSERT OR IGNORE INTO imports VALUES(?, unixepoch())", (done,))
        print(f"{run.name}: {count:,} runs filed under the heads it moved", flush=True)
