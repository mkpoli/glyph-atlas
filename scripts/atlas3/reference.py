"""Load the character reference data into glyph-atlas-v3 (decision record 0007).

    ATLAS3_DIR=work/atlas-v3 python3 scripts/atlas3/reference.py [--send] [--replace]

Reads the copies `source-<table>.sqlite` (pull.py), writes the tables of
`migrations-atlas/0004_reference.sql` to `out/reference.sqlite` and SQL parts under
`out/reference-load/<name>/`, named by the content of `out/reference.sqlite`, with a per-table report in
`out/reference-report.txt`. Without `--send` nothing is sent and no local copy changes.

- Cards, variants, derived forms and writers name characters by `graphemes.id`. A character the layout
  does not have yet gets the next id, as the publisher numbers one, with its card's family head; its
  row in `graphemes` goes in the same load, before anything names it.
- The crops a card's visual groups show move from text ids in the card to integer ids in
  `visual_samples`, resolved through `out/mirror.sqlite` and never through a redirect. A crop the
  mirror lacks leaves its group's list, counted in the report.
- `representations` is written by form claims and never changes: its rows go in with INSERT OR IGNORE
  and no load clears them.

With `--send`, migration 0004 is applied first (stamp `migration:0004`) on D1 and the local copy, then
each part, confirmed by its stamp in `imports` and replayed on the local copy. A load of another
translation must be cleared first (`--replace`).
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import sqlite3
import sys

from build import HERE, OUT, char_text
from load_history import built, deliver, row_statements, stamped
from publish import CF, applied, send

SCHEMA = CF / "migrations-atlas" / "0004_reference.sql"
# Tables in the order their rows load; `graphemes` holds only the characters this load numbers.
TABLES = ("graphemes", "characters", "visual_samples", "character_variants", "character_derived", "grapheme_writers",
          "component_variants", "words", "word_spellings", "representations", "han_ids", "han_components",
          "han_component_names")
# Copied as they are, columns in the migration's order.
VERBATIM = ("component_variants", "words", "word_spellings", "representations", "han_ids", "han_components",
            "han_component_names")
CLEARED = tuple(t for t in TABLES if t not in ("graphemes", "representations"))
GROUP = 500
SAMPLE_LISTS = ((0, "representatives"), (1, "anchors"))


def source(table):
    db = sqlite3.connect(f"file:{HERE / f'source-{table}.sqlite'}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    return db


class Graphemes:
    """Text to `graphemes.id`: the layout's, then the next ids in the order characters are first named."""

    def __init__(self, atlas):
        self.known = dict(atlas.execute("SELECT text, id FROM graphemes"))
        self.next = (atlas.execute("SELECT max(id) FROM graphemes").fetchone()[0] or 0) + 1
        self.new = {}  # text -> [id, head id]

    def __call__(self, text):
        if text in self.known:
            return self.known[text]
        if text not in self.new:
            self.new[text] = [self.next, None]
            self.next += 1
        return self.new[text][0]

    def head(self, text, head):
        """A new character takes its card's family head; the layout's keep theirs."""
        gid = self(text)
        if text in self.new and head:
            self.new[text][1] = self(head)
        return gid


def samples(detail, grapheme, mirror, out):
    """The card with its visual groups' crop text ids taken out; their integer ids go to `out`.
    Returns the detail and how many crops the mirror lacks."""
    if '"visual_analysis"' not in detail or '"groups"' not in detail:
        return detail, 0
    card = json.loads(detail)
    groups = (card.get("visual_analysis") or {}).get("groups") or []
    if not groups:
        return detail, 0
    lost = 0
    for grp, group in enumerate(groups):
        for role, key in SAMPLE_LISTS:
            kept = []
            for entry in group.get(key) or []:
                found = mirror.execute("SELECT id FROM ids WHERE text=?", (entry.get("id"),)).fetchone()
                if found is None:
                    lost += 1
                    continue
                out.append((grapheme, grp, role, len(kept), found[0]))
                kept.append({k: v for k, v in entry.items() if k not in ("id", "image")})
            if key in group:
                group[key] = kept
    return json.dumps(card, ensure_ascii=False, separators=(",", ":")), lost


def translate(atlas, mirror, path):
    """The copies as the 0004 tables in `path`; returns the report's counts."""
    path.unlink(missing_ok=True)
    out = sqlite3.connect(path)
    out.executescript(SCHEMA.read_text())
    # The layout's own definition, so the report counts its index rows.
    out.executescript("CREATE TABLE graphemes(id INTEGER PRIMARY KEY, text TEXT NOT NULL UNIQUE, head INTEGER);"
                      "CREATE INDEX grapheme_head ON graphemes(head);")
    g = Graphemes(atlas)
    heads = dict(atlas.execute("SELECT id, head FROM graphemes"))
    notes = {"crops in visual groups the mirror lacks": 0, "characters whose head in the layout differs from the card's": 0}

    cards, shown = [], []
    for r in source("characters").execute("SELECT code_point, character, name, data, detail, head FROM characters ORDER BY code_point"):
        head = char_text(r["head"])
        gid = g.head(r["character"], head)
        if gid in heads and head in g.known and heads[gid] != g.known[head]:
            notes["characters whose head in the layout differs from the card's"] += 1
        detail, lost = samples(r["detail"], gid, mirror, shown)
        notes["crops in visual groups the mirror lacks"] += lost
        cards.append((gid, r["code_point"], r["name"], r["data"], detail))
    out.executemany("INSERT INTO characters VALUES(?,?,?,?,?)", cards)
    out.executemany("INSERT INTO visual_samples VALUES(?,?,?,?,?)", shown)
    del cards

    out.executemany("INSERT INTO character_variants VALUES(?,?,?,?,?,?,?)",
                    [(g(r["a"]), g(r["b"]), r["relation"], r["source"], r["detail"], r["written"], r["widens"])
                     for r in source("character_variants").execute("SELECT * FROM character_variants ORDER BY a, b, relation, source")])
    out.executemany("INSERT INTO character_derived VALUES(?,?)",
                    [(g(r["a"]), r["forms"]) for r in source("character_derived").execute("SELECT a, forms FROM character_derived ORDER BY a")])
    out.executemany("INSERT INTO grapheme_writers VALUES(?,?,?)",
                    [(g(r["writer"]), g(r["grapheme"]), r["own"])
                     for r in source("grapheme_writers").execute("SELECT * FROM grapheme_writers ORDER BY writer, grapheme")])
    for table in VERBATIM:
        cols = [c[1] for c in out.execute(f'PRAGMA table_info("{table}")')]
        names = ",".join(f'"{c}"' for c in cols)
        out.executemany(f'INSERT INTO "{table}"({names}) VALUES({",".join("?" * len(cols))})',
                        source(table).execute(f'SELECT {names} FROM "{table}" ORDER BY {names}'))
    out.executemany("INSERT INTO graphemes VALUES(?,?,?)", [(i, text, head) for text, (i, head) in g.new.items()])
    out.commit()
    return notes


def digest(ref):
    """The load's name: its rows, less the characters it numbers, which a rerun after the load finds
    numbered with the same ids."""
    h = hashlib.sha256()
    for table in TABLES[1:]:
        for r in ref.execute(f'SELECT * FROM "{table}"'):
            h.update(repr(r).encode())
    return h.hexdigest()[:12]


def rows(ref):
    """Statements for every row, in groups; a row's statements stay in one group. Returns the groups
    and per table its statements and index rows."""
    groups, counts = [], {}
    for table in TABLES:
        info = list(ref.execute(f'PRAGMA table_info("{table}")'))
        cols = [r[1] for r in info]
        keys = [r[1] for r in sorted(info, key=lambda r: r[5]) if r[5]]
        names = ",".join(f'"{c}"' for c in cols)
        indexes = ref.execute("SELECT count(*) FROM pragma_index_list(?) WHERE origin <> 'pk'", (table,)).fetchone()[0]
        batch, statements, inserted = [], 0, 0
        for r in ref.execute(f'SELECT {names} FROM "{table}"'):
            made = row_statements(table, cols, keys, r)
            if table == "representations":
                made = [s.replace('INSERT INTO "representations"', 'INSERT OR IGNORE INTO "representations"', 1) for s in made]
            batch += made
            statements += len(made)
            inserted += 1
            if len(batch) >= GROUP:
                groups.append(batch)
                batch = []
        if batch:
            groups.append(batch)
        counts[table] = (inserted, statements, inserted * indexes)
    return groups, counts


def report(ref, counts, notes, parts):
    """Per table rows, bytes on disk (table and its indexes), D1 rows written (statements plus index
    entries)."""
    sizes = dict(ref.execute("SELECT coalesce(m.tbl_name, d.name), sum(d.pgsize) FROM dbstat d "
                             "LEFT JOIN sqlite_master m ON m.name = d.name GROUP BY 1"))
    lines = [f"{'table':<22}{'rows':>11}{'bytes':>15}{'statements':>12}{'index rows':>12}{'rows written':>14}"]
    total = [0, 0, 0, 0, 0]
    for table in TABLES:
        n, statements, index_rows = counts[table]
        size = sizes.get(table, 0)
        lines.append(f"{table:<22}{n:>11,}{size:>15,}{statements:>12,}{index_rows:>12,}{statements + index_rows:>14,}")
        for i, v in enumerate((n, size, statements, index_rows, statements + index_rows)):
            total[i] += v
    lines.append(f"{'total':<22}{total[0]:>11,}{total[1]:>15,}{total[2]:>12,}{total[3]:>12,}{total[4]:>14,}")
    lines.append(f"{parts} parts; each also writes its stamp in imports (2 rows)")
    lines += [f"{k}: {v:,}" for k, v in notes.items()]
    return "\n".join(lines) + "\n"


def migrate(atlas):
    """Migration 0004 on D1 and the local copy, once; a layout built with it already has its tables."""
    if atlas.execute("SELECT 1 FROM sqlite_master WHERE name='visual_samples'").fetchone():
        return
    path = OUT / "reference-load" / "0004.sql"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(stamped(SCHEMA.read_text(), "migration:0004"))
    if not applied("glyph-atlas-v3", "migration:0004") and not send("glyph-atlas-v3", path, "migration:0004"):
        sys.exit("migration 0004 did not apply; rerun")
    atlas.executescript("BEGIN;\n" + path.read_text() + "COMMIT;\n")
    print("migration 0004 applied", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--send", action="store_true", help="load the parts into glyph-atlas-v3 and the local copy")
    parser.add_argument("--replace", action="store_true", help="clear the rows of another translation's load first")
    args = parser.parse_args()
    lock = open(OUT / "mirror.lock", "w")  # noqa: SIM115 -- held until the process exits
    fcntl.flock(lock, fcntl.LOCK_EX)
    atlas = sqlite3.connect(OUT / "atlas.sqlite") if args.send else \
        sqlite3.connect(f"file:{OUT / 'atlas.sqlite'}?mode=ro", uri=True)
    mirror = sqlite3.connect(f"file:{OUT / 'mirror.sqlite'}?mode=ro", uri=True)

    path = OUT / "reference.sqlite"
    notes = translate(atlas, mirror, path)
    ref = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    name = "reference-" + digest(ref)
    run = OUT / "reference-load" / name
    groups, counts = rows(ref)
    n = built(run, "rows", groups)
    parts = n if n is not None else len(list(run.glob("*.sql")))
    text = report(ref, counts, notes, parts)
    (OUT / "reference-report.txt").write_text(text)
    print(text + f"{parts} parts -> {run}", flush=True)
    if not args.send:
        print("dry run: nothing sent", flush=True)
        return

    migrate(atlas)
    others = [r[0] for r in atlas.execute("SELECT stamp FROM imports WHERE stamp LIKE 'reference-%' AND stamp NOT LIKE ?",
                                          (f"{name}%",))]
    if others and not args.replace:
        sys.exit(f"another translation is loaded ({others[0]}); --replace clears its rows first")
    if others:
        clear = run.parent / f"{name}-clear"
        built(clear, "clear", [[f'DELETE FROM "{t}";' for t in reversed(CLEARED)]])
        deliver(clear, atlas)
    deliver(run, atlas)
    print("reference data loaded", flush=True)


if __name__ == "__main__":
    main()
