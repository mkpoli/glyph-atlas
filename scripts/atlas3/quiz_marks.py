"""Write Quick review's computed marks into glyph-atlas-v3: the suspects (`suspects`: the classifier's,
and those a crop's neighbours give), the shape order (`shapes`) and the agreed crops (`agreed`), each
crop named by its integer id (`migrations-atlas/0003_history.sql`, `0022_agreed.sql`,
`0023_suspect_basis.sql`).

    ATLAS3_DIR=work/atlas-v3 python3 scripts/atlas3/quiz_marks.py \\
        [--suspects hosted.json corpus.json ...] [--shapes <dataset>/quiz-shapes.json]
        [--agreed <similar>/agreed-marks.json] [--forms <similar>/agreed-marks.json] [--send]

- `--suspects`: marks files in precedence order, from `atlas review catalogue-suspects` over the
  catalogues the site serves, `atlas review corpus-suspects` for the corpus glyphs, or `atlas review
  suspects <dataset>` for one published dataset. A crop an earlier file scored keeps that file's
  answer, marked or not: a file lists what it scored in `scored_ids`, else only its marks count as
  scored. So the hosted catalogue's marks go first and the corpus glyphs' last.
- `--shapes`: the `quiz-shapes.json` of the dataset the published catalogue was exported from.
- `--agreed`: the `agreed-marks.json` of `atlas review agreed-marks`; its `agreed` crops are loaded,
  but for one whose cut the local copy holds differently.
- `--forms`: the same file; its `flagged` crops become suspects of basis `form`, each reading as the
  character its neighbours carry. A crop cut again since, or one the classifier marks, is left out.

Each set of marks given is replaced whole; one not given is left as it is: the classifier's suspects
and the neighbours' are two sets in one table. Every text id resolves through
out/mirror.sqlite, which is only read; a mark on a crop the mirror lacks is left out with a count.

Only what differs from the local copy (out/atlas.sqlite) is sent, in parts named by their content and
confirmed by their stamps in `imports`, then replayed on the local copy, as books.py sends them. Every
run first replays the parts of earlier runs whose stamps D1 holds and the local copy lacks; a dry run (the
default) keeps none of them, writes the parts and the figures to `out/quiz-marks-report.txt`, and sends
nothing.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import sqlite3
import sys
from pathlib import Path

from build import OUT
from load_history import GROUP, built, deliver, row_statements
from publish import applied, lit, query

# Each set of marks this writes: its table, columns, key, and the rows of the table that are its own.
SUSPECT = ("crop", "p", "reads_as", "label", "box", "basis")
TABLES = {"suspects": ("suspects", SUSPECT, ("crop",), "basis IS NULL"),
          "shapes": ("shapes", ("crop", "shape_order"), ("crop",), None),
          "agreed": ("agreed", ("crop", "label"), ("crop",), None),
          "forms": ("suspects", SUSPECT, ("crop",), "basis = 'form'")}
RUNS = OUT / "quiz-marks"


def suspects(files):
    """Each text id's mark from marks files in precedence order, checked as the Worker reads them."""
    marks, scored = {}, set()
    for path in files:
        document = json.loads(Path(path).read_text())
        for identity, mark in document["suspects"].items():
            if identity in scored:
                continue
            p, reads = mark.get("p"), mark.get("reads_as")
            if not isinstance(p, (int, float)) or not math.isfinite(p) or not 0 <= p <= 1:
                sys.exit(f"{path}: {identity}: a suspect's probability must be a number from 0 to 1")
            if reads is not None and not isinstance(reads, str):
                sys.exit(f"{path}: {identity}: reads_as must be text or null")
            label, box = mark.get("label"), mark.get("box")
            if not isinstance(label, str) or not label:
                sys.exit(f"{path}: {identity}: a suspect names the label it was judged under")
            if box is not None and not (isinstance(box, dict) and sorted(box) == ["h", "w", "x", "y"]
                                        and all(isinstance(v, (int, float)) and not isinstance(v, bool)
                                                and math.isfinite(v) for v in box.values())):
                sys.exit(f"{path}: {identity}: box must be x, y, w and h, or null")
            marks[identity] = (p, reads, label, None if box is None else json.dumps(box, sort_keys=True), None)
        scored |= set(document.get("scored_ids") or document["suspects"])
    return marks


def shapes(path):
    """Each text id's shape order from a dataset's quiz-shapes.json."""
    orders = json.loads(Path(path).read_text())["orders"]
    for identity, order in orders.items():
        if not isinstance(order, int) or isinstance(order, bool) or order < 0:
            sys.exit(f"{path}: {identity}: shape order must be a non-negative integer")
    return {identity: (order,) for identity, order in orders.items()}


def agreed(path):
    """Each agreed text id's label and the cut it was judged on, from an agreed-marks.json."""
    marks = json.loads(Path(path).read_text())["agreed"]
    for identity, mark in marks.items():
        label, cut = (mark.get("label"), mark.get("cut")) if isinstance(mark, dict) else (None, None)
        if not isinstance(label, str) or not label or not isinstance(cut, int) or isinstance(cut, bool) or cut < 0:
            sys.exit(f"{path}: {identity}: an agreed crop names the label and the cut it was judged under")
    return {identity: (mark["label"], mark["cut"]) for identity, mark in marks.items()}


def forms(path):
    """Each flagged text id's mark and the cut it was judged on, from an agreed-marks.json: it reads as
    the character its neighbours carry, none of which carry its own."""
    marks = json.loads(Path(path).read_text())["flagged"]
    for identity, mark in marks.items():
        label, form, cut = (mark.get("label"), mark.get("form"), mark.get("cut")) if isinstance(mark, dict) else (None,) * 3
        if (not isinstance(label, str) or not label or not isinstance(form, str) or not form or form == label
                or not isinstance(cut, int) or isinstance(cut, bool) or cut < 0):
            sys.exit(f"{path}: {identity}: a flagged crop names its label, another form and the cut it was judged under")
    return {identity: (0.0, mark["form"], mark["label"], None, "form", mark["cut"]) for identity, mark in marks.items()}


def by_crop(marks, ids, table, left):
    """The marks keyed by integer crop id; one on a crop the mirror lacks is counted and left out."""
    rows, reason = {}, f"{table} on crops the mirror lacks, left out"
    for identity, values in marks.items():
        crop = ids.get(identity)
        if crop is None:
            left[reason] = left.get(reason, 0) + 1
            continue
        rows[crop] = (crop, *values)
    return rows


def has(db, table):
    return db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone() is not None


def statements(wanted, atlas):
    """Groups of statements that turn the local copy's tables into `wanted` ({table: {crop: row}}); a
    row's statements stay in one group. Also, per table, the rows removed, added, changed and written."""
    groups, changes = [], {}
    for name, new in wanted.items():
        table, cols, key, own = TABLES[name]
        names = ",".join(cols)
        held = {r[0]: r for r in atlas.execute(f"SELECT {names} FROM {table}" + (f" WHERE {own}" if own else ""))}
        # A row another set has taken since is that set's: only this set's own rows are removed.
        rows = [[f"DELETE FROM {table} WHERE crop = {lit(c)}{f' AND {own}' if own else ''};"] for c in sorted(held) if c not in new]
        removed = len(rows)
        # Each row is removed before it is written, so its statements apply again without a conflict.
        rows += [[f"DELETE FROM {table} WHERE crop = {lit(c)};", *row_statements(table, list(cols), list(key), r)]
                 for c, r in sorted(new.items()) if held.get(c) != r]
        added = sum(c not in held for c in new)
        for i in range(0, len(rows), GROUP):
            groups.append([s for r in rows[i:i + GROUP] for s in r])
        changes[name] = {"rows": len(new), "removed": removed, "added": added, "changed": len(rows) - removed - added,
                          "written": sum(len(r) for r in rows) - added}
    return groups, changes


def figures(changes, left):
    kinds = ("rows", "removed", "added", "changed", "written")
    lines = ["table\t" + "\t".join(kinds)]
    lines += [f"{table}\t" + "\t".join(str(c[k]) for k in kinds) for table, c in changes.items()]
    lines.append(f"rows written: {sum(c['written'] for c in changes.values())} (a removed row 1, a new row 1, "
                 "a changed row its DELETE and its INSERT)")
    lines += [f"{n} {reason}" for reason, n in sorted(left.items())]
    return "\n".join(lines) + "\n"


def catch_up(atlas, keep):
    """Replay on the local copy the parts of earlier runs that D1 holds and the local copy lacks, as a
    run stopped between the two leaves them, so the difference is taken from what D1 holds. Unless
    `keep`, they stay in an open transaction the caller rolls back."""
    local = {r[0] for r in atlas.execute("SELECT stamp FROM imports WHERE stamp LIKE 'quiz-marks-%'")}
    parts = []
    for r in query("glyph-atlas-v3", "SELECT stamp FROM imports WHERE stamp LIKE 'quiz-marks-%' ORDER BY at, stamp"):
        if r["stamp"] in local:
            continue
        run, part = r["stamp"].split(".", 1)
        path = RUNS / run / f"{part}.sql"
        if not path.exists():
            sys.exit(f"glyph-atlas-v3 holds {r['stamp']}, whose part {path} is gone; the local copy cannot follow it")
        parts.append((r["stamp"], path))
    if not keep:
        if parts:
            atlas.executescript("SAVEPOINT catch_up;\n" + "".join(path.read_text() for _, path in parts))
            print(f"dry run: {len(parts)} parts D1 holds and the local copy lacks, replayed for the figures only", flush=True)
        return
    for stamp, path in parts:
        atlas.executescript("BEGIN;\n" + path.read_text() + "COMMIT;\n")
        print(f"replayed {stamp} from D1's imports", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--suspects", type=Path, nargs="+", help="quiz-suspects.json files, in precedence order")
    parser.add_argument("--shapes", type=Path, help="the published dataset's quiz-shapes.json")
    parser.add_argument("--agreed", type=Path, help="agreed-marks.json of `atlas review agreed-marks`")
    parser.add_argument("--forms", type=Path, help="agreed-marks.json, for its flagged crops")
    parser.add_argument("--send", action="store_true", help="load the parts into glyph-atlas-v3")
    args = parser.parse_args()
    if not args.suspects and not args.shapes and not args.agreed and not args.forms:
        parser.error("name --suspects, --shapes, --agreed or --forms")
    lock = open(OUT / "mirror.lock", "w")  # noqa: SIM115 -- held until the process exits
    fcntl.flock(lock, fcntl.LOCK_EX)
    mirror = sqlite3.connect(f"file:{OUT / 'mirror.sqlite'}?mode=ro", uri=True)
    if has(mirror, "pending") and mirror.execute("SELECT 1 FROM pending").fetchone():
        sys.exit("a publish is unfinished; finish it with publish.py first")
    atlas = sqlite3.connect(OUT / "atlas.sqlite")
    if not has(atlas, "suspects"):
        sys.exit("the local copy has no suspects table; load the history (load_history.py) first")
    if args.send:
        build = mirror.execute("SELECT value FROM meta WHERE key='build'").fetchone()[0]
        if not applied("glyph-atlas-v3", f"build:{build}"):
            sys.exit(f"glyph-atlas-v3 was not loaded from build {build}; the local copy does not describe it")
    if args.agreed and not has(atlas, "agreed"):
        sys.exit("the local copy has no agreed table; apply migrations-atlas/0022_agreed.sql to it and to glyph-atlas-v3 first")
    if "basis" not in {r[1] for r in atlas.execute("PRAGMA table_info(suspects)")}:
        sys.exit("the local copy's suspects have no basis; apply migrations-atlas/0023_suspect_basis.sql to it and to glyph-atlas-v3 first")
    catch_up(atlas, keep=args.send)
    ids = dict(mirror.execute("SELECT text, id FROM ids"))
    left, wanted = {}, {}
    if args.suspects:
        wanted["suspects"] = by_crop(suspects(args.suspects), ids, "suspects", left)
    if args.shapes:
        wanted["shapes"] = by_crop(shapes(args.shapes), ids, "shapes", left)
    if args.agreed:
        # A crop repaired since it was judged is no longer the image the mark was given on.
        cuts = dict(atlas.execute("SELECT id, cut FROM crops WHERE kind = 0"))
        rows = by_crop(agreed(args.agreed), ids, "agreed", left)
        wanted["agreed"] = {crop: (crop, label) for crop, (_, label, cut) in rows.items() if cuts.get(crop) == cut}
        if len(rows) > len(wanted["agreed"]):
            left["agreed on crops cut again since, left out"] = len(rows) - len(wanted["agreed"])
    if args.forms:
        cuts = dict(atlas.execute("SELECT id, cut FROM crops WHERE kind = 0"))
        # The classifier's mark on a crop stands: the ones being loaded now, else the ones held.
        taken = set(wanted["suspects"]) if args.suspects else {r[0] for r in atlas.execute("SELECT crop FROM suspects WHERE basis IS NULL")}
        rows = by_crop(forms(args.forms), ids, "forms", left)
        wanted["forms"] = {}
        for crop, (_, *mark, cut) in rows.items():
            reason = "cut again since" if cuts.get(crop) != cut else "the classifier marks" if crop in taken else None
            if reason:
                left[f"forms on crops {reason}, left out"] = left.get(f"forms on crops {reason}, left out", 0) + 1
            else:
                wanted["forms"][crop] = (crop, *mark)
    groups, changes = statements(wanted, atlas)
    report = figures(changes, left)
    (OUT / "quiz-marks-report.txt").write_text(report)
    print(report, end="", flush=True)
    if not groups:
        atlas.rollback()
        print("the local copy already holds these marks; nothing to send", flush=True)
        return
    # The name covers the runs already applied as well as the statements, so the same difference taken
    # again after other runs moved the tables gets a new run; an interrupted run keeps its name.
    history = sorted(r[0] for r in atlas.execute("SELECT stamp FROM imports WHERE stamp LIKE 'quiz-marks-%'"))
    digest = hashlib.sha256("\n".join([*history, "", *(s for g in groups for s in g)]).encode()).hexdigest()[:12]
    run = RUNS / f"quiz-marks-{digest}"
    n = built(run, "rows", groups)
    print(f"{n if n is not None else 'existing'} parts -> {run}", flush=True)
    if not args.send:
        atlas.rollback()
        print("dry run: nothing sent", flush=True)
        return
    deliver(run, atlas)
    print("quiz marks loaded", flush=True)


if __name__ == "__main__":
    main()
