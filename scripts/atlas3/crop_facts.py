"""Load the crop facts the publisher writes into glyph-atlas-v3: the model votes and the forms tool's
clustering (migration 0006), from the live copies (`pull.py unit_votes form_families form_clusters form_units`).

    ATLAS3_DIR=work/atlas-v3 python3 scripts/atlas3/crop_facts.py [--send]

A dry run (the default) writes the parts and the report under `out/crop-facts/<name>-dry/` and sends
nothing. `--send` applies migration 0006 (stamp `migration:0006`), then the parts, each confirmed by its
stamp in `imports` and replayed on the local copy, as load_history.py does. The parts are named by the
content of the copies, so a rerun on the same copies skips what D1 already holds.

- Every text id resolves through out/mirror.sqlite, never through a redirect. A row naming a crop the
  mirror lacks is dropped and counted, as history.py drops script facts on crops that no longer exist.
- Votes: each collected crop's latest copied row, stored as votes.py shapes it, for the cut the crop has
  now; a crop that is no longer live has none. A crop publish.py has already given votes keeps them.
- Forms: clusters are numbered in key order; a glyph names its cluster by number and its tile by media
  key. A cluster's stored representatives should be its first twelve glyphs by rank, which the Worker
  reads from `form_units` in their place; the report counts any that are not.

The report, with rows and statement bytes per table, is `out/crop-facts-report.txt`.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import re
import shutil
import sqlite3
import sys
from collections import Counter

import votes
from build import HERE, OUT
from history import Report
from load_history import SCHEMA, built, deliver, row_statements, stamped
from publish import applied, insert, lit, send

SOURCES = ("unit_votes", "form_families", "form_clusters", "form_units")
GROUP = 500
IMAGE = re.compile(r"/atlas/media/([0-9a-f]{64})\.webp")
REPRESENTATIVES = 12
VOTE_COLS = ("crop", "cut", "models", "visual", "context")
FAMILY_COLS = ("code_point", "char", "label", "count", "cluster_count", "forms", "assigned", "rejected", "revision")
CLUSTER_COLS = ("id", "key", "family", "label", "count", "coherence", "shape_position", "size_position", "form",
                "decision", "issue")
UNIT_COLS = ("crop", "family", "cluster", "rank", "similarity", "image", "split", "clustered", "cluster_form",
             "cluster_issue", "cluster_character", "cluster_family", "glyph_set", "glyph_form", "glyph_decision",
             "glyph_issue", "glyph_character", "glyph_family", "form", "issue", "issue_character", "written_family")


def latest(table, key):
    """A copy's rows in key order; a copy by rowid can hold a row twice, and the last copy wins."""
    db = sqlite3.connect(f"file:{HERE / f'source-{table}.sqlite'}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    cols = [r[1] for r in db.execute(f'PRAGMA table_info("{table}")')]
    where = f' WHERE _rowid IN (SELECT max(_rowid) FROM "{table}" GROUP BY {key})' if "_rowid" in cols else ""
    return db.execute(f'SELECT * FROM "{table}"{where} ORDER BY {key}')


def migrate(atlas, dry):
    """Migration 0006 on D1 and the local copy, once; a layout built with it already has its tables."""
    if atlas.execute("SELECT 1 FROM sqlite_master WHERE name='votes'").fetchone():
        return
    if dry:
        print("dry run: migration 0006 not applied", flush=True)
        return
    path = OUT / "crop-facts" / "0006.sql"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(stamped((SCHEMA / "0006_crop_facts.sql").read_text(), "migration:0006"))
    if not applied("glyph-atlas-v3", "migration:0006") and not send("glyph-atlas-v3", path, "migration:0006"):
        sys.exit("migration 0006 did not apply; rerun")
    atlas.executescript("BEGIN;\n" + path.read_text() + "COMMIT;\n")
    print("migration 0006 applied", flush=True)


def model_codes(mirror, atlas, engines, dry):
    """Each engines list's code in field 'models', numbered by the mirror as publish.py numbers codes, and
    the statements adding those glyph-atlas-v3 lacks. A dry run numbers them without saving."""
    codes, statements = {}, []
    top = mirror.execute("SELECT max(code) FROM codes WHERE field='models'").fetchone()[0] or 0
    for key in sorted(engines):
        row = mirror.execute("SELECT code FROM codes WHERE field='models' AND key=?", (key,)).fetchone()
        if row:
            code = row[0]
        else:
            top += 1
            code = top
            mirror.execute("INSERT INTO codes(field, key, code) VALUES('models', ?, ?)", (key, code))
        codes[key] = code
        if not atlas.execute("SELECT 1 FROM codes WHERE field='models' AND code=?", (code,)).fetchone():
            statements.append(f"INSERT OR IGNORE INTO codes(field, code, key) VALUES('models', {code}, {lit(key)});")
    if dry:
        mirror.rollback()
    else:
        mirror.commit()
    return codes, statements


class Facts:
    def __init__(self, mirror, atlas):
        self.report, self.mirror, self.atlas = Report(), mirror, atlas
        self.ids = dict(mirror.execute("SELECT text, id FROM ids"))
        self.live = dict(atlas.execute("SELECT id, cut FROM crops"))
        self.rows, self.bytes = {}, Counter()

    def votes(self, dry):
        self.report.table("unit_votes", 0)
        kept, engines = [], set()
        for r in latest("unit_votes", "id"):
            if r["origin"] != "local":
                continue
            self.report.tables["unit_votes"]["in"] += 1
            crop = self.ids.get(r["id"])
            if crop is None:
                self.report.drop("unit_votes", "crop the mirror lacks")
                continue
            if crop not in self.live:
                self.report.drop("unit_votes", "crop no longer live")
                continue
            key, visual = votes.visual(r["visual"])
            context = votes.context(r["context"])
            if visual is None and context is None:
                self.report.drop("unit_votes", "no reading")
                continue
            if key:
                engines.add(key)
            kept.append((crop, self.live[crop], key, visual, context))
        codes, head = model_codes(self.mirror, self.atlas, engines, dry)
        self.rows["votes"] = [(crop, cut, codes.get(key), visual, context) for crop, cut, key, visual, context in kept]
        self.rows["codes"] = head
        self.report.tables["unit_votes"]["out"] = len(kept)
        self.report.note(f"votes: {sum(v[3] is not None for v in kept)} with a visual reading, "
                         f"{sum(v[4] is not None for v in kept)} with a context reading, {len(codes)} model sets")

    def forms(self):
        families = [tuple(r[c] for c in FAMILY_COLS) for r in latest("form_families", "code_point")]
        self.report.table("form_families", len(families))
        self.report.tables["form_families"]["out"] = len(families)
        self.rows["form_families"] = families

        clusters = list(latest("form_clusters", "id"))
        number = {r["id"]: n for n, r in enumerate(clusters, 1)}
        self.report.table("form_clusters", len(clusters))
        self.report.tables["form_clusters"]["out"] = len(clusters)
        self.rows["form_clusters"] = [(number[r["id"]], r["id"], *(r[c] for c in CLUSTER_COLS[2:])) for r in clusters]

        self.report.table("form_units", 0)
        values, ranked, outside, retired, untiled = [], {}, 0, 0, 0
        for r in latest("form_units", "id"):
            self.report.tables["form_units"]["in"] += 1
            if r["clustered"]:
                ranked.setdefault(r["cluster"], []).append((r["rank"], r["id"], r["image"]))
            crop = self.ids.get(r["id"])
            if crop is None:
                self.report.drop("form_units", "crop the mirror lacks")
                continue
            cluster = number.get(r["cluster"])
            if cluster is None and r["clustered"]:
                self.report.drop("form_units", "clustered in a cluster form_clusters lacks")
                continue
            image = None
            if r["image"] is not None:
                found = IMAGE.fullmatch(r["image"])
                if not found:
                    self.report.drop("form_units", "tile not a media key")
                    continue
                image = found[1]
            retired += crop not in self.live
            outside += cluster is None
            untiled += image is None
            d = {**dict(r), "crop": crop, "cluster": cluster, "image": image}
            values.append(tuple(d[c] for c in UNIT_COLS))
        self.report.tables["form_units"]["out"] = len(values)
        self.report.note(f"form_units: {retired} glyphs on crops no longer live, {untiled} without a tile, "
                         f"{outside} kept for a decision whose cluster is not in the clustering")
        self.rows["form_units"] = values

        # The Worker lists a cluster's first twelve glyphs by rank in place of `representatives`.
        differ = [r["id"] for r in clusters
                  if [(e["id"], e.get("image")) for e in json.loads(r["representatives"] or "[]")]
                  != [(i, image) for _rank, i, image in sorted(ranked.get(r["id"], []))[:REPRESENTATIVES]]]
        self.report.note(f"form_clusters: {len(differ)} clusters whose representatives are not their first "
                         f"{REPRESENTATIVES} glyphs by rank", differ)

    def groups(self):
        """Statements in groups, a row's statements in one group: the model codes, then the votes (kept
        where publish.py wrote newer ones), then the forms tables."""
        if self.rows["codes"]:
            yield self.rows["codes"]
        tables = (("votes", VOTE_COLS, ["crop"]), ("form_families", FAMILY_COLS, ["code_point"]),
                  ("form_clusters", CLUSTER_COLS, ["id"]), ("form_units", UNIT_COLS, ["crop"]))
        for table, cols, keys in tables:
            batch = []
            for r in self.rows[table]:
                if table == "votes":
                    st = [insert("votes", cols, r).replace("INSERT INTO", "INSERT OR IGNORE INTO", 1)]
                    if len(st[0].encode()) > 90_000:
                        sys.exit(f"the votes of crop {r[0]} need a statement over D1's limit")
                else:
                    st = row_statements(table, list(cols), keys, r)
                self.bytes[table] += sum(len(s.encode()) + 1 for s in st)
                batch += st
                if len(batch) >= GROUP:
                    yield batch
                    batch = []
            if batch:
                yield batch


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--send", action="store_true", help="apply migration 0006 and send the parts")
    args = parser.parse_args()
    dry = not args.send
    lock = open(OUT / "mirror.lock", "w")  # noqa: SIM115 -- held until the process exits
    fcntl.flock(lock, fcntl.LOCK_EX)
    atlas = sqlite3.connect(OUT / "atlas.sqlite")
    mirror = sqlite3.connect(OUT / "mirror.sqlite")
    migrate(atlas, dry)

    digest = hashlib.sha256()
    for table in SOURCES:
        with open(HERE / f"source-{table}.sqlite", "rb") as f:
            digest.update(hashlib.file_digest(f, "sha256").digest())
    name = "facts-" + digest.hexdigest()[:12]
    run = OUT / "crop-facts" / (name + "-dry" if dry else name)
    if dry:
        shutil.rmtree(run, ignore_errors=True)
    if not run.exists():
        facts = Facts(mirror, atlas)
        facts.votes(dry)
        facts.forms()
        n = built(run, "rows", facts.groups())
        lines = facts.report.lines()[:-2]
        lines += [f"{table}: {len(facts.rows[table])} rows, {facts.bytes[table]} bytes of statements"
                  for table in ("votes", "form_families", "form_clusters", "form_units")]
        lines += [f"{n} parts", ""]
        (run / "report.txt").write_text("\n".join(lines))
    text = (run / "report.txt").read_text() if (run / "report.txt").exists() else "the report of this run was not written\n"
    (OUT / "crop-facts-report.txt").write_text(text)
    print(text, flush=True)
    if dry:
        print(f"dry run: nothing sent; parts in {run}", flush=True)
        return
    deliver(run, atlas)
    print(f"crop facts loaded from {name}", flush=True)


if __name__ == "__main__":
    main()
