"""Move the character reference data onto the new layout (decision record 0007).

    ATLAS3_DIR=work/atlas-v3 python3 scripts/atlas3/reference.py          # translate, report, send nothing
    ATLAS3_DIR=work/atlas-v3 python3 scripts/atlas3/reference.py --send   # and load it

Reads the copies `source-<table>.sqlite` (pull.py) and writes two things.

R2 objects under `v3/reference/`, gzipped JSON, each read by key:

- `char/<code point>.json.gz` (code points joined by `-`): a card's `data` and `detail`, its grapheme
  id, its 異体字 edges from both ends, its derived forms with the component pairs their substitutions
  rest on, the graphemes it writes, and the writers of its family head with `shared` worked out.
  `ids` gives the grapheme id of every character the object names that has one. A visual group is
  keyed by its `visual_group` code (`codes`), and its samples name crops by integer id: a sample
  whose crop the mirror or `crops` lacks leaves the list, counted in the report.
- `summary/<shard>.json.gz`: every card's `data` by code point, in the shard of its family head, so
  a family's members are one read.
- `ids/<shard>.json.gz`: each character's ideographic descriptions and regional shapes.
- `names.json.gz`: `[code point, name]` for the cards whose Unicode name is more than
  `…IDEOGRAPH-<hex>`, for a search by name.
- `list/graphemes/<n>.json.gz` and `list/ligatures/<n>.json.gz`: the cards of families of several
  characters, and the ligatures, by code point, LIST_PAGE to an object.

A shard is the first two hex digits of the SHA-256 of its key's code point(s) as text.

D1 rows (`migrations-atlas/0004_reference.sql`) for what a query filters, joins or sorts on:
`component_variants`, `words`, `word_spellings`, `han_components`, `han_component_names` and
`name_order`. Cards name characters by `graphemes` id: a card's character or family head the layout
lacks is numbered as the publisher numbers one, and every grapheme takes its card's family head.
Graphemes are written as `publish.py` writes them, through `char_text`.

Only what differs from the local copies is sent: the rows that differ from `out/atlas.sqlite`, as
statements in parts under `out/reference-load/reference-<generation>-<digest>/`, each confirmed by
its stamp in `imports` and replayed on the local copy, then the objects that differ from
`out/reference/`, which holds what was last uploaded. The generation counts the loads before, so a
load that returns to an earlier copy is sent again. A load that stopped partway is finished first.
Figures go to `out/reference-report.txt`. `--send` also applies migration 0004 (stamp
`migration:0004`) where the local copy lacks it.

`build.py --replace` numbers graphemes afresh: run this again after it.
"""

from __future__ import annotations

import argparse
import fcntl
import gzip
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from collections import defaultdict

from build import HERE, OUT, char_text
from load_history import built, deliver, row_statements, stamped
from publish import BUCKET, CF, R2_REMOTE, applied, lit, send, upload

SCHEMA = CF / "migrations-atlas" / "0004_reference.sql"
PREFIX = "v3/reference/"
LIST_PAGE = 500
# Rows per group of statements; a group is never split across parts.
GROUP = 500
SOURCES = {
    "characters": ("code_point", "character", "name", "data", "detail", "head"),
    "character_variants": ("a", "b", "relation", "source", "detail", "written", "widens"),
    "character_derived": ("a", "forms"),
    "grapheme_writers": ("writer", "grapheme", "own"),
    "component_variants": ("a", "b", "count", "pairs"),
    "words": ("id", "language", "reading", "class"),
    "word_spellings": ("word", "spelling", "source", "locator", "tier", "word_by", "basis", "related", "statement",
                       "documents", "occurrences"),
    "han_ids": ("char", "sequences", "shapes"),
    "han_components": ("component", "tier", "size", "code_point", "n", "direct"),
    "han_component_names": ("query", "component", "total"),
}
# The tables reference.py owns, with their keys, in the order their rows load.
TABLES = {
    "component_variants": (("a", "b", "count"), ("a", "b")),
    "words": (SOURCES["words"], ("id",)),
    "word_spellings": (SOURCES["word_spellings"], ("word", "spelling", "source", "locator")),
    "han_components": (SOURCES["han_components"], ("component", "tier", "size", "code_point")),
    "han_component_names": (SOURCES["han_component_names"], ("query",)),
    "name_order": (("code_point", "occurrence"), ("code_point",)),
}
SAMPLE_LISTS = ("representatives", "anchors")
PLAIN_NAME = re.compile(r"IDEOGRAPH-[0-9A-F]{4,6}$")


def source(table):
    path = HERE / f"source-{table}.sqlite"
    if not path.exists():
        sys.exit(f"{path.name} is missing; pull.py {table} copies it")
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    have = {r[1] for r in db.execute(f'PRAGMA table_info("{table}")')}
    if lacking := [c for c in SOURCES[table] if c not in have]:
        sys.exit(f"{path.name} lacks {', '.join(lacking)}; pull.py --fresh {table} copies it again")
    return db


def shard(key):
    return hashlib.sha256(key.encode()).hexdigest()[:2]


def object_key(code_point):
    return code_point.replace(" ", "-")


def code_points(code_point):
    return tuple(int(p.removeprefix("U+"), 16) for p in code_point.split())


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def encode(text):
    return gzip.compress(text.encode(), mtime=0)


def substitution_key(left, right):
    """A substitution's own spelling, as refs._substitution writes it: the side of fewer code points
    first, then the smaller by code point."""
    if len(left) != len(right):
        return (left, right) if len(left) < len(right) else (right, left)
    return (left, right) if left <= right else (right, left)


class Graphemes:
    """Text to `graphemes.id`: the layout's, then the next ids in the order characters are first named,
    as `publish.py` numbers one."""

    def __init__(self, atlas):
        self.known = dict(atlas.execute("SELECT text, id FROM graphemes"))
        self.heads = dict(atlas.execute("SELECT id, head FROM graphemes"))
        self.next = (atlas.execute("SELECT max(id) FROM graphemes").fetchone()[0] or 0) + 1
        self.new = {}  # text -> id

    def __call__(self, text):
        text = char_text(text)
        if not text:
            return None
        if text in self.known:
            return self.known[text]
        if text not in self.new:
            self.new[text] = self.next
            self.next += 1
        return self.new[text]

    def get(self, text):
        text = char_text(text)
        return self.known.get(text, self.new.get(text))


def visual_groups(detail, codes, crop_of, notes):
    """The card's detail with each visual group keyed by its code and each sample naming its crop."""
    if '"visual_analysis"' not in detail or '"groups"' not in detail:
        return detail
    card = json.loads(detail)
    groups = (card.get("visual_analysis") or {}).get("groups") or []
    if not groups:
        return detail
    for group in groups:
        code = codes.get(group.get("id"))
        if code is None:
            notes["visual groups with no visual_group code, kept by their id"] += 1
        else:
            group["code"] = code
            del group["id"]
        for key in SAMPLE_LISTS:
            if key not in group:
                continue
            kept = []
            for entry in group[key] or []:
                crop = crop_of(entry.get("id"))
                if crop is None:
                    notes["visual group samples whose crop is not published, left out"] += 1
                    continue
                kept.append({"crop": crop, **{k: v for k, v in entry.items() if k not in ("id", "image")}})
            group[key] = kept
    return compact(card)


def translate(atlas, mirror, path):
    """The D1 tables in `path` and the objects as {path under v3/reference/: gzipped JSON}; also the
    graphemes to insert, the heads to change and the report's notes."""
    path.unlink(missing_ok=True)
    out = sqlite3.connect(path)
    out.executescript(SCHEMA.read_text())
    notes = defaultdict(int, {"visual groups with no visual_group code, kept by their id": 0,
                              "visual group samples whose crop is not published, left out": 0})
    g = Graphemes(atlas)

    codes = dict(atlas.execute("SELECT key, code FROM codes WHERE field='visual_group'"))

    def crop_of(text_id):
        found = mirror.execute("SELECT id FROM ids WHERE text=?", (text_id,)).fetchone()
        if found is None or not atlas.execute("SELECT 1 FROM crops WHERE id=?", (found[0],)).fetchone():
            return None
        return found[0]

    # Each card's light facts, by code point; its data text is kept once, for the summaries and lists.
    cards, heads = [], {}
    rows = source("characters").execute("SELECT code_point, character, name, data, head FROM characters")
    for code_point, character, name, data_text, head in sorted(rows, key=lambda r: code_points(r[0])):
        char = char_text(character)
        data = json.loads(data_text)
        family = data.get("grapheme") or {}
        gid = g(char)
        if head:
            heads[gid] = g(head)
        cards.append((code_point, char, name, data_text, family.get("code_point") or code_point, family.get("char") or char,
                      len(family.get("members") or []) > 1, data.get("ligature") is not None,
                      int(data.get("occurrence_count") or 0)))

    edges = defaultdict(list)
    for a, b, relation, src, detail, written, widens in source("character_variants").execute(
            "SELECT a, b, relation, source, detail, written, widens FROM character_variants ORDER BY a, b, relation, source"):
        a, b = char_text(a), char_text(b)
        edges[a].append([b, relation, src, detail, written, widens])
        edges[b].append([a, relation, src, detail, written, widens])
    derived = {char_text(a): forms for a, forms in source("character_derived").execute("SELECT a, forms FROM character_derived")}
    pairs = {(a, b): [count, json.loads(p)] for a, b, count, p in
             source("component_variants").execute("SELECT a, b, count, pairs FROM component_variants")}
    writes, writers = defaultdict(list), defaultdict(list)
    for writer, grapheme, own in source("grapheme_writers").execute("SELECT writer, grapheme, own FROM grapheme_writers"):
        writes[char_text(writer)].append((char_text(grapheme), own))
    for writer in writes:
        writes[writer].sort()
        for grapheme, own in writes[writer]:
            writers[grapheme].append([writer, int(bool(own) or len(writes[writer]) > 1)])
    for grapheme in writers:
        writers[grapheme].sort()

    objects, by_code = {}, {c[0]: c for c in cards}
    for code_point, data_text, detail in source("characters").execute("SELECT code_point, data, detail FROM characters"):
        _, char, _name, _data, _family, head, *_ = by_code[code_point]
        forms_text = derived.get(char, "[]")
        forms = json.loads(forms_text)
        subs = sorted({substitution_key(was, became) for _other, routes in forms for route in routes for was, became in route})
        named = {char, head, *(e[0] for e in edges.get(char, [])), *(w for w, _ in writes.get(char, [])),
                 *(w for w, _ in writers.get(head, [])), *(other for other, _ in forms)}
        ids = {c: i for c in sorted(named) if (i := g.get(c)) is not None}
        objects[f"char/{object_key(code_point)}.json.gz"] = encode(
            f'{{"grapheme":{g.get(char)},"data":{data_text},"detail":{visual_groups(detail, codes, crop_of, notes)},'
            f'"variants":{compact(edges.get(char, []))},"derived":{forms_text},'
            f'"substitutions":{compact([[a, b, *pairs[(a, b)]] for a, b in subs if (a, b) in pairs])},'
            f'"writes":{compact([[w, own] for w, own in writes.get(char, [])])},'
            f'"writers":{compact(writers.get(head, []))},"ids":{compact(ids)}}}')
    del derived

    summaries = defaultdict(list)
    for c in cards:
        summaries[shard(c[4])].append(c)
    for key, members in summaries.items():
        objects[f"summary/{key}.json.gz"] = encode("{" + ",".join(f"{compact(c[0])}:{c[3]}" for c in sorted(members)) + "}")
    structures = defaultdict(dict)
    for char, sequences, shapes in source("han_ids").execute("SELECT char, sequences, shapes FROM han_ids"):
        structures[shard(char)][char] = [json.loads(sequences), json.loads(shapes) if shapes else None]
    for key, chars in structures.items():
        objects[f"ids/{key}.json.gz"] = encode(compact(dict(sorted(chars.items()))))
    objects["names.json.gz"] = encode(compact([[c[0], c[2]] for c in cards if c[2] and not PLAIN_NAME.search(c[2])]))
    lists = {"graphemes": [c[3] for c in cards if c[6]], "ligatures": [c[3] for c in cards if c[7]]}
    for list_name, items in lists.items():
        for n in range(0, max(len(items), 1), LIST_PAGE):
            objects[f"list/{list_name}/{n // LIST_PAGE}.json.gz"] = encode(
                f'{{"total":{len(items)},"offset":{n},"items":[{",".join(items[n:n + LIST_PAGE])}]}}')

    out.executemany("INSERT INTO component_variants VALUES(?,?,?)", [(a, b, v[0]) for (a, b), v in pairs.items()])
    for table in ("words", "word_spellings", "han_component_names"):
        cols = SOURCES[table]
        out.executemany(f'INSERT INTO "{table}" VALUES({",".join("?" * len(cols))})',
                        source(table).execute(f'SELECT {",".join(cols)} FROM "{table}"'))
    out.executemany("INSERT INTO han_components VALUES(?,?,?,?,?,?)",
                    ((c, t, s, int(cp.removeprefix("U+"), 16), n, d) for c, t, s, cp, n, d in
                     source("han_components").execute("SELECT component, tier, size, code_point, n, direct FROM han_components")))
    out.executemany("INSERT INTO name_order VALUES(?,?)", [(code_points(c[0])[0], c[8]) for c in cards if " " not in c[0] and c[8] > 0])
    out.commit()

    changed = {gid: head for gid, head in heads.items() if gid in g.heads and g.heads[gid] != head}
    notes["graphemes of the layout whose family head the cards change"] = len(changed)
    notes["graphemes the layout lacks, numbered by this load"] = len(g.new)
    return out, objects, g.new, {gid: heads.get(gid) for gid in g.new.values()}, changed, notes


def has(db, table):
    return db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone() is not None


def remove_row(table, cols, key, r):
    return f'DELETE FROM "{table}" WHERE {" AND ".join(f'"{c}" = {lit(r[cols.index(c)])}' for c in key)};'


def statements(ref, atlas, new, new_heads, changed):
    """Groups of statements that turn the local copy into the translation; a row's statements stay in
    one group. Also, per table, the rows each writes: statements plus their index entries."""
    groups, written = [], {}

    def add(table, rows, indexes):
        for i in range(0, len(rows), GROUP):
            groups.append([s for r in rows[i:i + GROUP] for s in r])
        written[table] = sum(len(r) for r in rows) * (1 + indexes)

    add("graphemes", [[f"INSERT INTO graphemes(id, text, head) VALUES({gid}, {lit(text)}, {lit(new_heads[gid])});"]
                      for text, gid in new.items()]
        + [[f"UPDATE graphemes SET head = {head} WHERE id = {gid};"] for gid, head in sorted(changed.items())], 1)
    for table, (cols, key) in TABLES.items():
        names = ",".join(f'"{c}"' for c in cols)
        k = [cols.index(c) for c in key]
        held = {tuple(r[i] for i in k): r for r in atlas.execute(f"SELECT {names} FROM {table}")} if has(atlas, table) else {}
        wanted = {tuple(r[i] for i in k): r for r in ref.execute(f"SELECT {names} FROM {table}")}
        rows = [[remove_row(table, cols, key, r)] for kk, r in sorted(held.items()) if kk not in wanted]
        for kk, r in sorted(wanted.items()):
            if held.get(kk) != r:
                rows.append(([remove_row(table, cols, key, r)] if kk in held else [])
                            + row_statements(table, list(cols), list(key), r))
        indexes = ref.execute("SELECT count(*) FROM pragma_index_list(?) WHERE origin <> 'pk'", (table,)).fetchone()[0]
        add(table, rows, indexes)
    return groups, written


def loads(atlas):
    """The names of the reference loads the local copy holds a part of."""
    return {s.split(".")[0] for (s,) in atlas.execute("SELECT stamp FROM imports WHERE stamp LIKE 'reference-%'")}


def finish(atlas):
    """Deliver every load the local copy holds only part of."""
    started = loads(atlas)
    for run in sorted((OUT / "reference-load").glob("reference-*")):
        if run.is_dir() and run.name in started and any(
                not atlas.execute("SELECT 1 FROM imports WHERE stamp=?", (f"{run.name}.{p.stem}",)).fetchone()
                for p in run.glob("*.sql")):
            print(f"finishing {run.name}", flush=True)
            deliver(run, atlas)


def remove(paths):
    """Objects to delete under PREFIX; a resend is harmless."""
    names = "\n".join(paths) + "\n"
    for _ in range(4):
        done = subprocess.run(["rclone", "delete", f"{R2_REMOTE}:{BUCKET}/{PREFIX}", "--files-from-raw", "-"],
                              input=names, text=True, check=False)
        if done.returncode == 0:
            return
    sys.exit(f"deleting from {PREFIX} failed; rerun to finish")


def differing(objects):
    """The objects whose bytes differ from the local copy's, as {path: bytes}, and the local copy's
    objects the translation lacks."""
    held = OUT / "reference"
    changed = {}
    for rel, data in objects.items():
        local = held / rel
        if not local.exists() or local.read_bytes() != data:
            changed[rel] = data
    gone = sorted(str(p.relative_to(held)) for p in held.rglob("*.json.gz") if str(p.relative_to(held)) not in objects) \
        if held.exists() else []
    return changed, gone


def ship(changed, gone):
    """Upload the changed objects folder by folder, delete the gone ones, then record both locally."""
    stage = OUT / "reference-send"
    shutil.rmtree(stage, ignore_errors=True)
    folders = defaultdict(list)
    for rel, data in changed.items():
        path = stage / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        folders[os.path.dirname(rel)].append(path)
    for folder, files in sorted(folders.items()):
        upload(sorted(files), PREFIX + (folder + "/" if folder else ""))
    if gone:
        remove(gone)
    held = OUT / "reference"
    for rel, data in changed.items():
        (held / rel).parent.mkdir(parents=True, exist_ok=True)
        (held / rel).write_bytes(data)
    for rel in gone:
        (held / rel).unlink()
    shutil.rmtree(stage)


def report(ref, written, objects, changed, gone, notes, parts):
    """D1 rows and bytes on disk (tables with their indexes) and rows written; R2 objects and bytes by kind."""
    sizes = dict(ref.execute("SELECT coalesce(m.tbl_name, d.name), sum(d.pgsize) FROM dbstat d "
                             "LEFT JOIN sqlite_master m ON m.name = d.name GROUP BY 1"))
    lines = [f"{'D1 table':<22}{'rows':>11}{'bytes':>14}{'rows written':>14}"]
    total = [0, 0, 0]
    for table in ("graphemes", *TABLES):
        # Graphemes: the rows this load inserts or changes.
        n = ref.execute(f"SELECT count(*) FROM {table}").fetchone()[0] if table != "graphemes" else written.get(table, 0) // 2
        size = sizes.get(table, 0)
        lines.append(f"{table:<22}{n:>11,}{size:>14,}{written.get(table, 0):>14,}")
        for i, v in enumerate((n, size, written.get(table, 0))):
            total[i] += v
    lines.append(f"{'total':<22}{total[0]:>11,}{total[1]:>14,}{total[2]:>14,}")
    lines.append(f"{parts} parts; each also writes its stamp in imports (2 rows)")
    lines.append("")
    lines.append(f"{'R2 objects':<22}{'objects':>11}{'bytes':>14}{'largest':>12}{'to send':>10}{'bytes':>14}")
    kinds = defaultdict(list)
    for rel in objects:
        kinds[rel.split("/")[0] if "/" in rel else rel].append(rel)
    sent = {rel: len(data) for rel, data in changed.items()}
    whole = {rel: len(data) for rel, data in objects.items()}
    for kind, rels in sorted(kinds.items()):
        sizes_ = [whole[r] for r in rels]
        to_send = [sent[r] for r in rels if r in sent]
        lines.append(f"{kind:<22}{len(rels):>11,}{sum(sizes_):>14,}{max(sizes_):>12,}{len(to_send):>10,}{sum(to_send):>14,}")
    lines.append(f"{'total':<22}{len(objects):>11,}{sum(whole.values()):>14,}{'':>12}{len(sent):>10,}{sum(sent.values()):>14,}")
    lines.append(f"objects to delete: {len(gone):,}")
    lines += [f"{k}: {v:,}" for k, v in sorted(notes.items())]
    return "\n".join(lines) + "\n"


def migrate(atlas):
    """Migration 0004 on D1 and the local copy, once; a layout built with it already has its tables."""
    if has(atlas, "name_order"):
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
    parser.add_argument("--send", action="store_true", help="load the rows into glyph-atlas-v3 and upload the objects")
    args = parser.parse_args()
    lock = open(OUT / "mirror.lock", "w")  # noqa: SIM115 -- held until the process exits
    fcntl.flock(lock, fcntl.LOCK_EX)
    mirror = sqlite3.connect(f"file:{OUT / 'mirror.sqlite'}?mode=ro", uri=True)
    if has(mirror, "pending") and mirror.execute("SELECT 1 FROM pending").fetchone():
        sys.exit("a publish is unfinished; finish it with publish.py first")
    atlas = sqlite3.connect(OUT / "atlas.sqlite") if args.send else \
        sqlite3.connect(f"file:{OUT / 'atlas.sqlite'}?mode=ro", uri=True)
    if args.send:
        build = mirror.execute("SELECT value FROM meta WHERE key='build'").fetchone()[0]
        if not applied("glyph-atlas-v3", f"build:{build}"):
            sys.exit(f"glyph-atlas-v3 was not loaded from build {build}; the local copy does not describe it")
        migrate(atlas)
        finish(atlas)

    ref, objects, new, new_heads, changed_heads, notes = translate(atlas, mirror, OUT / "reference.sqlite")
    groups, written = statements(ref, atlas, new, new_heads, changed_heads)
    changed, gone = differing(objects)
    run = None
    if groups:
        digest = hashlib.sha256("\n".join(s for g in groups for s in g).encode()).hexdigest()[:12]
        run = OUT / "reference-load" / f"reference-{len(loads(atlas)) + 1}-{digest}"
        built(run, "rows", groups)
    parts = len(list(run.glob("*.sql"))) if run else 0
    text = report(ref, written, objects, changed, gone, notes, parts)
    (OUT / "reference-report.txt").write_text(text)
    print(text + (f"{parts} parts -> {run}" if run else "the local copy already holds these rows"), flush=True)
    if not args.send:
        print("dry run: nothing sent", flush=True)
        return
    if run:
        deliver(run, atlas)
    if changed or gone:
        ship(changed, gone)
    print("reference data loaded", flush=True)


if __name__ == "__main__":
    main()
