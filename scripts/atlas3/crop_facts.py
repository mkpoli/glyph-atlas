"""Load the crop facts into the new layout (decision record 0008): the model votes as page objects in R2,
and the forms tool's clustering, from the live copies (`pull.py unit_votes form_families form_clusters
form_units`).

    ATLAS3_DIR=work/atlas-v3 python3 scripts/atlas3/crop_facts.py [--send]

Every text id resolves through out/mirror.sqlite, never through a redirect. A row naming a crop the
mirror lacks, or a crop no longer live (retired), is dropped and counted.

- Votes: each collected crop's latest copied reading joins its page object `v3/votes/<build>/<page>.json.gz`
  (votes.py) for the crop's cut, when the image it was read on (`image_sha256`) is that cut's image in
  the crop's bundle; a reading of another image is dropped and counted. An entry a publish has already
  written for the crop's cut is kept.
- Forms: clusters are numbered by the mirror's `codes` (field 'form_cluster', key the cluster's id), so
  a cluster keeps its number across loads. Each cluster's glyphs, by rank, go to an immutable R2 list
  `v3/forms/<lists>/<cluster>.json.gz` (`{family, key, members: [[crop, rank, similarity, image, split]]}`,
  `image` the tile's media key); `lists` is the clustering's revision and the digest of every list, so
  a load whose lists differ writes a new folder. `form_families` and `form_clusters` take the copies'
  rows and the cluster decisions; `form_glyphs` only the glyphs with their own decision and those kept
  outside the clustering. Counts are recomputed from the glyphs kept, and the form each glyph ends with
  is checked against the copy's.

Rows go to D1 as the difference from the local copy (out/atlas.sqlite), as books.py sends them: a
rerun after a fresh copy inserts, updates and removes only what changed. `--send` refuses a database
holding form decisions the local copy lacks, since the Worker writes these tables once the site runs on
them. A dry run (the default) writes the run under `out/crop-facts/<name>-dry/` and sends nothing; the
report, with rows, statement bytes and objects, is `out/crop-facts-report.txt`.
"""

from __future__ import annotations

import argparse
import fcntl
import gzip
import hashlib
import json
import re
import shutil
import sqlite3
import sys
from collections import Counter

import votes
from build import HERE, OUT, PAGE
from history import Report
from load_history import GROUP, SCHEMA, built, deliver, stamped
from publish import applied, lit, query, send, upload

SOURCES = ("unit_votes", "form_families", "form_clusters", "form_units")
IMAGE = re.compile(r"/atlas/media/([0-9a-f]{64})\.webp")
FORMS_PREFIX = "v3/forms/"
# Each table crop_facts.py owns, its columns and key.
TABLES = {
    "form_families": (("code_point", "char", "label", "count", "cluster_count", "forms", "assigned", "rejected",
                       "revision", "lists"), ("code_point",)),
    "form_clusters": (("id", "key", "family", "label", "count", "coherence", "shape_position", "size_position", "form",
                       "issue", "character", "written_family", "decision"), ("id",)),
    "form_glyphs": (("crop", "family", "cluster", "own", "form", "issue", "character", "written_family", "decision"),
                    ("crop",)),
}


def latest(table, key):
    """A copy's rows in key order; a copy by rowid can hold a row twice, and the last copy wins."""
    path = HERE / f"source-{table}.sqlite"
    if not path.exists():
        sys.exit(f"{path.name} is missing; pull.py {table} copies it")
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    cols = [r[1] for r in db.execute(f'PRAGMA table_info("{table}")')]
    where = f' WHERE _rowid IN (SELECT max(_rowid) FROM "{table}" GROUP BY {key})' if "_rowid" in cols else ""
    return db.execute(f'SELECT * FROM "{table}"{where} ORDER BY {key}')


def has(db, table):
    return db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone() is not None


def migrate(atlas, build):
    """Migration 0006 on D1 and the local copy, once, with the build id the Worker finds the vote
    objects by; a layout built with it already has both."""
    if has(atlas, "form_families"):
        return
    path = OUT / "crop-facts" / "0006.sql"
    path.parent.mkdir(parents=True, exist_ok=True)
    sql = (SCHEMA / "0006_crop_facts.sql").read_text()
    sql += f"\nINSERT OR IGNORE INTO codes(field, code, key) VALUES('build', 0, {lit(build)});\n"
    path.write_text(stamped(sql, "migration:0006"))
    if not applied("glyph-atlas-v3", "migration:0006") and not send("glyph-atlas-v3", path, "migration:0006"):
        sys.exit("migration 0006 did not apply; rerun")
    atlas.executescript("BEGIN;\n" + path.read_text() + "COMMIT;\n")
    print("migration 0006 applied", flush=True)


def numbered(mirror, field, key):
    """The mirror's code for a key, numbered after the highest when new, as publish.py numbers codes."""
    row = mirror.execute("SELECT code FROM codes WHERE field=? AND key=?", (field, key)).fetchone()
    if row:
        return row[0]
    code = (mirror.execute("SELECT max(code) FROM codes WHERE field=?", (field,)).fetchone()[0] or 0) + 1
    mirror.execute("INSERT INTO codes(field, key, code) VALUES(?, ?, ?)", (field, key, code))
    return code


def applies(own, cluster):
    """What a glyph ends with: (form, issue, character, written family), from its own row, else its cluster."""
    if own is not None:
        return own
    if cluster is None:
        return (None, None, None, None)
    form, issue, character, family = cluster
    return (form, None if issue == "mixed" else issue, character, family)


class Facts:
    def __init__(self, mirror, atlas):
        self.report, self.mirror, self.atlas = Report(), mirror, atlas
        self.ids = dict(mirror.execute("SELECT text, id FROM ids"))
        self.live = dict(atlas.execute("SELECT id, cut FROM crops"))
        self.revs = dict(atlas.execute("SELECT id, rev FROM pages"))
        self.head, self.rows, self.objects, self.images = [], {}, {}, {}

    def image(self, crop):
        """The image hash of the crop's current cut, from its page bundle."""
        pid = crop // PAGE
        if pid not in self.images:
            path = OUT / "pages" / f"{pid}-{self.revs.get(pid)}.json.gz"
            self.images[pid] = {e["slot"]: e.get("image_sha256") for e in json.loads(gzip.decompress(path.read_bytes()))["crops"]
                                if not e.get("retired")} if path.exists() else {}
        return self.images[pid].get(crop % PAGE)

    def votes(self):
        report = self.report
        report.table("unit_votes", 0)
        fresh = {}
        for r in latest("unit_votes", "id"):
            if r["origin"] != "local":
                continue
            report.tables["unit_votes"]["in"] += 1
            crop = self.ids.get(r["id"])
            if crop is None:
                report.drop("unit_votes", "crop the mirror lacks")
                continue
            if crop not in self.live:
                report.drop("unit_votes", "crop no longer live (retired)")
                continue
            key, stored = votes.visual(r["visual"])
            guessed = votes.context(r["context"])
            if stored is None and guessed is None:
                report.drop("unit_votes", "no reading")
                continue
            current = self.image(crop)
            if not r["image_sha256"] or not current:
                report.drop("unit_votes", "no image hash to match against the crop's cut")
                continue
            if r["image_sha256"] != current:
                report.drop("unit_votes", "read on another image than the crop's cut (re-cut since)")
                continue
            # Held as text until its page is written, which keeps half a million readings small.
            fresh.setdefault(crop // PAGE, {})[crop % PAGE] = (
                self.live[crop], key, None if stored is None else votes.compact(stored),
                None if guessed is None else votes.compact(guessed))
        kept = Counter()
        for pid in sorted(fresh):
            slots = votes.read(OUT / "votes", pid)
            for slot, (cut, key, stored, guessed) in sorted(fresh[pid].items()):
                if slots.get(slot, {}).get("cut") == cut:
                    kept["publish"] += 1
                    continue
                code = None
                if key:
                    code = numbered(self.mirror, "models", key)
                    if (code, key) not in self.head and not self.atlas.execute(
                            "SELECT 1 FROM codes WHERE field='models' AND code=?", (code,)).fetchone():
                        self.head.append((code, key))
                slots[slot] = votes.entry(cut, code, stored and json.loads(stored), guessed and json.loads(guessed))
                kept["written"] += 1
                kept["visual"] += stored is not None
                kept["context"] += guessed is not None
            data = votes.encode(slots)
            path = OUT / "votes" / f"{pid}.json.gz"
            if not path.exists() or path.read_bytes() != data:
                self.objects[f"votes/{pid}.json.gz"] = data
        report.tables["unit_votes"]["out"] = kept["written"] + kept["publish"]
        report.note(f"votes: {kept['written']} readings written ({kept['visual']} visual, {kept['context']} context) on "
                    f"{len(fresh)} pages, {kept['publish']} left as a publish wrote them; {len(self.head)} new model sets")

    def forms(self):
        report = self.report
        families = {r["code_point"]: dict(r) for r in latest("form_families", "code_point")}
        clusters = {r["id"]: dict(r) for r in latest("form_clusters", "id")}
        report.table("form_families", len(families))
        report.table("form_clusters", len(clusters))
        report.table("form_units", 0)
        members, outside, settled, said = {}, [], {}, {}
        for r in latest("form_units", "id"):
            report.tables["form_units"]["in"] += 1
            crop = self.ids.get(r["id"])
            reason = ("crop the mirror lacks" if crop is None else "crop no longer live (retired)" if crop not in self.live
                      else "clustered in a cluster form_clusters lacks" if r["clustered"] and r["cluster"] not in clusters
                      else "tile not a media key" if r["image"] is not None and not IMAGE.fullmatch(r["image"]) else None)
            if reason:
                report.drop("form_units", reason)
                continue
            own = (r["glyph_form"], r["glyph_issue"], r["glyph_character"], r["glyph_family"]) if r["glyph_set"] else None
            settled[crop] = (r["form"], r["issue"], r["issue_character"], r["written_family"])
            if r["clustered"]:
                image = IMAGE.fullmatch(r["image"])[1] if r["image"] else None
                members.setdefault(r["cluster"], []).append(
                    (r["rank"], crop, r["similarity"], image, r["split"], own, r["glyph_decision"]))
                said.setdefault(r["cluster"], Counter())[(r["cluster_character"], r["cluster_family"])] += 1
            else:
                # Outside the clustering: it keeps what it ends with, its own decision's or its former cluster's.
                outside.append((crop, r["family"], None, int(own is not None), *settled[crop],
                                r["glyph_decision"] if own else None))

        disagree = sorted(k for k, c in said.items() if len(c) > 1)
        report.note(f"form_clusters: {len(disagree)} clusters whose glyphs name differing cluster characters or families "
                    "(the most common is kept)", disagree)
        cluster_rows, lists, glyphs, wrong, counts = [], {}, [], [], Counter()
        for key in sorted(clusters):
            if key not in members:
                report.drop("form_clusters", "no glyph left")
                continue
            c, ms = clusters[key], sorted(members[key])
            n = numbered(self.mirror, "form_cluster", key)
            character, written = said[key].most_common(1)[0][0]
            decided = (c["form"], c["issue"], character, written)
            cluster_rows.append((n, key, c["family"], c["label"], len(ms), c["coherence"], c["shape_position"],
                                 c["size_position"], *decided, c["decision"]))
            lists[n] = {"family": c["family"], "key": key,
                        "members": [[crop, rank, sim, image, split] for rank, crop, sim, image, split, _own, _d in ms]}
            for _rank, crop, _sim, _image, _split, own, decision in ms:
                got = applies(own, decided)
                if got != settled[crop]:
                    wrong.append(crop)
                counts[c["family"], "count"] += 1
                counts[c["family"], "assigned"] += got[0] is not None
                counts[c["family"], "rejected"] += got[1] is not None
                if own is not None:
                    glyphs.append((crop, c["family"], n, 1, *own, decision))
        report.note(f"form_units: {len(wrong)} glyphs whose form, issue, character or family differs from the copy's "
                    "when worked out from their own row and their cluster", wrong)

        encoded, digest = {}, hashlib.sha256()
        for n in sorted(lists):
            encoded[n] = gzip.compress(votes.compact(lists[n]).encode(), mtime=0)
            digest.update(f"{n}:".encode() + hashlib.sha256(encoded[n]).digest())
        revisions = {f["revision"] for f in families.values()}
        if len(revisions) > 1:
            sys.exit(f"the copied families name {len(revisions)} clustering revisions; copy form_families again")
        self.folder = f"{next(iter(revisions), 'none')}-{digest.hexdigest()[:12]}"
        clusters_of = Counter(r[2] for r in cluster_rows)
        family_rows, changed = [], 0
        for cp in sorted(families):
            f = families[cp]
            if not clusters_of[cp]:
                report.drop("form_families", "no cluster left")
                continue
            row = (cp, f["char"], f["label"], counts[cp, "count"], clusters_of[cp], f["forms"], counts[cp, "assigned"],
                   counts[cp, "rejected"], f["revision"], self.folder)
            changed += (row[3], row[4], row[6], row[7]) != (f["count"], f["cluster_count"], f["assigned"], f["rejected"])
            family_rows.append(row)
        report.note(f"form_families: {changed} families whose counts changed with the glyphs left out")
        report.tables["form_families"]["out"] = len(family_rows)
        report.tables["form_clusters"]["out"] = len(cluster_rows)
        report.tables["form_units"]["out"] = sum(len(m["members"]) for m in lists.values()) + len(outside)
        report.note(f"form_glyphs: {len(glyphs)} glyphs with their own decision, {len(outside)} outside the clustering; "
                    f"{len(lists)} cluster lists in {self.folder}")
        self.rows = {"form_families": family_rows, "form_clusters": cluster_rows, "form_glyphs": glyphs + outside}
        for n, data in encoded.items():
            if not (OUT / "forms" / self.folder / f"{n}.json.gz").exists():
                self.objects[f"forms/{self.folder}/{n}.json.gz"] = data

    def groups(self):
        """The statements that turn the local copy's tables into these, in groups; and per table the
        statements and their bytes."""
        groups, written = [], {}
        tables = [("codes", [f"INSERT OR IGNORE INTO codes(field, code, key) VALUES('models', {c}, {lit(k)});"
                             for c, k in self.head])]
        for table, (cols, key) in TABLES.items():
            names = ",".join(cols)
            held = {r[0]: r for r in self.atlas.execute(f"SELECT {names} FROM {table}")} if has(self.atlas, table) else {}
            new = {r[0]: tuple(r) for r in self.rows[table]}
            out = [f"DELETE FROM {table} WHERE {key[0]} = {lit(k)};" for k in sorted(held) if k not in new]
            out += [f"INSERT INTO {table}({names}) VALUES({','.join(map(lit, new[k]))}) ON CONFLICT({key[0]}) DO UPDATE SET "
                    + ",".join(f"{c}=excluded.{c}" for c in cols[1:]) + ";" for k in sorted(new) if held.get(k) != new[k]]
            tables.append((table, out))
        for table, out in tables:
            groups += [out[i:i + GROUP] for i in range(0, len(out), GROUP)]
            written[table] = (len(out), sum(len(s.encode()) + 1 for s in out))
        return groups, written


def guard(atlas):
    """Refuse a database whose form tables the Worker has written since the local copy was made."""
    if not has(atlas, "form_decisions"):
        return
    local = atlas.execute("SELECT max(seq) FROM form_decisions").fetchone()[0]
    remote = query("glyph-atlas-v3", "SELECT max(seq) AS seq FROM form_decisions")[0]["seq"]
    if remote != local:
        sys.exit(f"glyph-atlas-v3 holds form decisions up to {remote}, the local copy up to {local}: the site has decided "
                 "forms since, and this load would undo them")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--send", action="store_true", help="apply migration 0006, upload the objects and send the rows")
    args = parser.parse_args()
    dry = not args.send
    lock = open(OUT / "mirror.lock", "w")  # noqa: SIM115 -- held until the process exits
    fcntl.flock(lock, fcntl.LOCK_EX)
    atlas = sqlite3.connect(OUT / "atlas.sqlite")
    mirror = sqlite3.connect(OUT / "mirror.sqlite")
    if mirror.execute("SELECT 1 FROM sqlite_master WHERE name='pending'").fetchone() and \
            mirror.execute("SELECT 1 FROM pending").fetchone():
        sys.exit("a publish is unfinished; finish it with publish.py first")
    build = mirror.execute("SELECT value FROM meta WHERE key='build'").fetchone()[0]
    if not dry:
        if not applied("glyph-atlas-v3", f"build:{build}"):
            sys.exit(f"glyph-atlas-v3 was not loaded from build {build}; the local copy does not describe it")
        guard(atlas)
        migrate(atlas, build)

    facts = Facts(mirror, atlas)
    facts.votes()
    facts.forms()
    groups, written = facts.groups()
    digest = hashlib.sha256()
    for g in groups:
        digest.update("\n".join(g).encode())
    for name in sorted(facts.objects):
        digest.update(name.encode() + hashlib.sha256(facts.objects[name]).digest())
    name = "facts-" + digest.hexdigest()[:12]
    run = OUT / "crop-facts" / (name + "-dry" if dry else name)
    if dry:
        shutil.rmtree(run, ignore_errors=True)
        mirror.rollback()
    else:
        # The numbers the rows name are kept before anything is sent.
        mirror.commit()
    n = built(run, "rows", groups, facts.objects)
    lines = facts.report.lines()[:-2]
    lines += [f"{table}: {k} statements, {b} bytes" for table, (k, b) in written.items()]
    for kind in ("votes", "forms"):
        objects = [v for k, v in facts.objects.items() if k.startswith(kind + "/")]
        lines.append(f"{kind} objects to upload: {len(objects)}, {sum(map(len, objects))} bytes")
    lines += [f"{n if n is not None else 'existing'} parts", ""]
    text = "\n".join(lines)
    (run / "report.txt").write_text(text)
    (OUT / "crop-facts-report.txt").write_text(text)
    print(text, flush=True)
    if dry:
        print(f"dry run: nothing sent; run in {run}", flush=True)
        return
    deliver_objects(run, build)
    deliver(run, atlas)
    for kind in ("votes", "forms"):
        for path in sorted((run / kind).rglob("*.json.gz")) if (run / kind).exists() else []:
            target = OUT / path.relative_to(run)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
    print(f"crop facts loaded from {run.name}", flush=True)


def journal(run):
    path = run / "journal"
    return path.read_text().split("\n") if path.exists() else []


def deliver_objects(run, build):
    """The lists before the rows that name their folder, and the vote objects; each step once."""
    for folder in sorted((run / "forms").iterdir()) if (run / "forms").exists() else []:
        if f"uploaded forms/{folder.name}" not in journal(run):
            upload(sorted(folder.iterdir()), f"{FORMS_PREFIX}{folder.name}/")
            with (run / "journal").open("a") as f:
                f.write(f"uploaded forms/{folder.name}\n")
    if (run / "votes").exists() and "uploaded votes" not in journal(run):
        upload(sorted((run / "votes").iterdir()), votes.prefix(build))
        with (run / "journal").open("a") as f:
            f.write("uploaded votes\n")


if __name__ == "__main__":
    main()
