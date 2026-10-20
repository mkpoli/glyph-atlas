"""Load the crop facts into the new layout (decision record 0008): the model votes as page objects in R2,
and the forms tool's clustering, from the live copies (`pull.py unit_facts form_families form_clusters
form_units`).

    ATLAS3_DIR=work/atlas-v3 python3 scripts/atlas3/crop_facts.py [--send]

Every text id resolves through out/mirror.sqlite, never through a redirect. A row naming a crop the
mirror lacks, or a crop no longer live (retired), is dropped and counted.

- Votes: each collected crop's latest copied reading joins its page object
  `v3/votes/<build>/<page>.json.gz` (votes.py) for the crop's cut, when the image it was read on
  (`image_sha256`) is that cut's image in the crop's bundle; a reading of another image is dropped and
  counted. Entries a publish wrote for the crop's cut stay; those an earlier load wrote are written
  again from this copy.
- Forms: clusters are numbered by the mirror's `codes` (field 'form_cluster', key the cluster's id), so
  a cluster keeps its number across loads. Each cluster's glyphs, by rank, go to the R2 object
  `v3/forms/<n>-<digest>.json.gz` (`{family, key, members: [[crop, rank, similarity, image, split, tone,
  image_size]]}`, `image` the tile's media key), named by its content, so only a changed cluster's list
  is uploaded; each family's first 12 and last 24 glyphs per cluster go to its index object
  `v3/forms/families/<code point>-<digest>.json.gz`. `form_families` and `form_clusters` take the
  copies' rows and the cluster decisions, each of which must be its cluster's latest in the loaded
  `form_decisions`; `form_glyphs` takes the glyphs with their own decision; `crops.cluster` each live
  crop's cluster, null outside the clustering. Counts are recomputed from the glyphs kept, and the form
  each glyph ends with is checked against the copy's.
- An object under `v3/forms/` that no row and no decision names is deleted by a load at least
  KEEP_UNNAMED seconds after a load first found it unnamed, from the manifest `v3/forms/manifest.json.gz`
  (local copy out/forms/manifest.json.gz).

Rows go to D1 as the difference from the local copy (out/atlas.sqlite), as books.py sends them: a
rerun after a fresh copy inserts, updates and removes only what changed. The first load writes about
1.33M `crops` rows (one per clustered glyph), so it waits for the owner's approval with the dry run's
figures. `--send` refuses a database holding form decisions the local copy lacks, sets
`form_state.loading` (no decision is recorded meanwhile) and checks again before the rows go, and
names its run in the mirror's `pending`, so publish.py waits for it. A dry run (the default) writes the
run under `out/crop-facts/<name>-dry/` and sends nothing; the report, with rows, statement bytes and
objects, is `out/crop-facts-report.txt`.
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
import subprocess
import sys
import time
from collections import Counter

import votes
from build import HERE, OUT, PAGE
from history import Report
from load_history import GROUP, SCHEMA, built, stamped
from publish import BUCKET, FACTS_RUN, R2_REMOTE, applied, fits, lit, query, send, upload

SOURCES = ("unit_facts", "form_families", "form_clusters", "form_units")
IMAGE = re.compile(r"/atlas/media/([0-9a-f]{64})\.webp")
FORMS_PREFIX = "v3/forms/"
MANIFEST = "manifest.json.gz"
# A family's index object holds per cluster its first HEAD and last TAIL glyphs.
HEAD, TAIL = 12, 24
# The Worker keeps a forms answer an hour and an object five minutes; an unnamed object outlives both.
KEEP_UNNAMED = 2 * 3600
# Crop ids per `UPDATE crops SET cluster` statement, about 60 KB.
CLUSTER_IDS = 5000
# Each table crop_facts.py owns, its columns and key.
TABLES = {
    "form_families": (("code_point", "char", "label", "count", "cluster_count", "forms", "assigned", "rejected",
                       "revision", "list"), ("code_point",)),
    "form_clusters": (("id", "key", "family", "label", "count", "coherence", "shape_position", "size_position", "list",
                       "form", "issue", "character", "written_family", "decision"), ("id",)),
    "form_glyphs": (("crop", "family", "cluster", "form", "issue", "character", "written_family", "decision"), ("crop",)),
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


def named(body):
    """An object's bytes and the digest that names it."""
    text = votes.compact(body)
    return gzip.compress(text.encode(), mtime=0), hashlib.sha256(text.encode()).hexdigest()[:12]


def latest_decisions(atlas):
    """Each cluster key's latest cluster decision in the loaded form_decisions."""
    if not has(atlas, "form_decisions"):
        return {}
    return dict(atlas.execute("SELECT cluster, id FROM form_decisions WHERE kind='cluster' AND cluster IS NOT NULL "
                              "ORDER BY at, seq"))


class Facts:
    def __init__(self, mirror, atlas, build):
        self.report, self.mirror, self.atlas, self.build = Report(), mirror, atlas, build
        self.ids = dict(mirror.execute("SELECT text, id FROM ids"))
        self.live = dict(atlas.execute("SELECT id, cut FROM crops"))
        self.clustered = dict(atlas.execute("SELECT id, cluster FROM crops WHERE cluster IS NOT NULL"))
        self.revs = dict(atlas.execute("SELECT id, rev FROM pages"))
        self.head, self.rows, self.objects, self.images, self.looks, self.cluster_of = [], {}, {}, {}, {}, {}

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
        report.table("unit_facts", 0)
        fresh = {}
        for r in latest("unit_facts", "id"):
            crop = self.ids.get(r["id"])
            if crop is not None and (r["tone"] is not None or r["image_size"] is not None):
                # A tile's paper colour and size, for the forms lists.
                self.looks[crop] = (r["tone"], r["image_size"] and json.loads(r["image_size"]))
            if r["origin"] != "local":
                continue
            report.tables["unit_facts"]["in"] += 1
            if crop is None:
                report.drop("unit_facts", "crop the mirror lacks")
                continue
            if crop not in self.live:
                report.drop("unit_facts", "crop no longer live (retired)")
                continue
            key, stored = votes.visual(r["visual"])
            guessed = votes.context(r["context"])
            if stored is None and guessed is None:
                report.drop("unit_facts", "no reading")
                continue
            current = self.image(crop)
            if not r["image_sha256"] or not current:
                report.drop("unit_facts", "no image hash to match against the crop's cut")
                continue
            if r["image_sha256"] != current:
                report.drop("unit_facts", "read on another image than the crop's cut (re-cut since)")
                continue
            # Held as text until its page is written, which keeps half a million readings small.
            fresh.setdefault(crop // PAGE, {})[crop % PAGE] = (
                self.live[crop], key, None if stored is None else votes.compact(stored),
                None if guessed is None else votes.compact(guessed))
        kept = Counter()
        held = {int(p.name.split(".")[0]) for p in (OUT / "votes").glob("*.json.gz")}
        for pid in sorted(set(fresh) | held):
            slots = votes.read(OUT / "votes", pid)
            # An earlier load's entries give way to this copy's; a publish's stay while their cut is the crop's.
            for slot in [s for s, e in slots.items() if e.get("loaded")]:
                del slots[slot]
                kept["unloaded"] += 1
            for slot, (cut, key, stored, guessed) in sorted(fresh.get(pid, {}).items()):
                if slots.get(slot, {}).get("cut") == cut:
                    kept["publish"] += 1
                    continue
                code = None
                if key:
                    code = numbered(self.mirror, "models", key)
                    if (code, key) not in self.head and not self.atlas.execute(
                            "SELECT 1 FROM codes WHERE field='models' AND code=?", (code,)).fetchone():
                        self.head.append((code, key))
                slots[slot] = votes.entry(cut, code, stored and json.loads(stored), guessed and json.loads(guessed), loaded=True)
                kept["written"] += 1
                kept["visual"] += stored is not None
                kept["context"] += guessed is not None
            data = votes.encode(slots)
            path = OUT / "votes" / f"{pid}.json.gz"
            if not path.exists() or path.read_bytes() != data:
                self.objects[f"votes/{pid}.json.gz"] = data
        report.tables["unit_facts"]["out"] = kept["written"] + kept["publish"]
        report.note(f"votes: {kept['written']} readings written ({kept['visual']} visual, {kept['context']} context) on "
                    f"{len(fresh)} pages, {kept['publish']} left as a publish wrote them, {kept['unloaded']} an earlier "
                    f"load wrote replaced; {len(self.head)} new model sets")

    def forms(self):
        report = self.report
        families = {r["code_point"]: dict(r) for r in latest("form_families", "code_point")}
        clusters = {r["id"]: dict(r) for r in latest("form_clusters", "id")}
        report.table("form_families", len(families))
        report.table("form_clusters", len(clusters))
        report.table("form_units", 0)
        # A cluster decision is the latest of its cluster in the loaded history, or the load stops.
        decided_last = latest_decisions(self.atlas)
        stale = [f"{k}: {c['decision']} (latest {decided_last.get(k)})" for k, c in sorted(clusters.items())
                 if c["decision"] is not None and decided_last.get(k) != c["decision"]]
        if stale:
            sys.exit(f"{len(stale)} clusters name a decision that is not their latest cluster decision in the loaded "
                     f"form_decisions; load the history from the same copy first: {stale[:5]}")
        members, outside, settled, said = {}, [], {}, {}
        undecided = Counter()
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
            elif own is not None:
                outside.append((crop, r["family"], None, *own, r["glyph_decision"]))
            else:
                # Outside the clustering with no decision of its own: nothing to keep.
                undecided["glyphs"] += 1
                undecided["with a form"] += any(v is not None for v in settled[crop])
                report.drop("form_units", "outside the clustering, no decision of its own")

        disagree = sorted(k for k, c in said.items() if len(c) > 1)
        report.note(f"form_clusters: {len(disagree)} clusters whose glyphs name differing cluster characters or families "
                    "(the most common is kept)", disagree)
        report.note(f"form_units: {undecided['glyphs']} glyphs outside the clustering with no decision of their own, "
                    f"{undecided['with a form']} of them with a form from a former cluster, not kept")
        cluster_rows, lists, glyphs, wrong, counts = [], {}, [], [], Counter()
        for key in sorted(clusters):
            if key not in members:
                report.drop("form_clusters", "no glyph left")
                continue
            c, ms = clusters[key], sorted(members[key])
            n = numbered(self.mirror, "form_cluster", key)
            character, written = said[key].most_common(1)[0][0]
            decided = (c["form"], c["issue"], character, written)
            body = {"family": c["family"], "key": key,
                    "members": [[crop, rank, sim, image, split, *self.looks.get(crop, (None, None))]
                                for rank, crop, sim, image, split, _own, _d in ms]}
            data, digest = named(body)
            lists[n] = (f"{n}-{digest}", data, body["members"])
            cluster_rows.append((n, key, c["family"], c["label"], len(ms), c["coherence"], c["shape_position"],
                                 c["size_position"], lists[n][0], *decided, c["decision"]))
            for _rank, crop, _sim, _image, _split, own, decision in ms:
                self.cluster_of[crop] = n
                got = applies(own, decided)
                if got != settled[crop]:
                    wrong.append(crop)
                counts[c["family"], "count"] += 1
                counts[c["family"], "assigned"] += got[0] is not None
                counts[c["family"], "rejected"] += got[1] is not None
                if own is not None:
                    glyphs.append((crop, c["family"], n, *own, decision))
        report.note(f"form_units: {len(wrong)} glyphs whose form, issue, character or family differs from the copy's "
                    "when worked out from their own row and their cluster", wrong)

        revisions = {f["revision"] for f in families.values()}
        if len(revisions) > 1:
            sys.exit(f"the copied families name {len(revisions)} clustering revisions; copy form_families again")
        of_family = {}
        for row in cluster_rows:
            of_family.setdefault(row[2], []).append(row[0])
        family_rows, indexes, changed = [], {}, 0
        for cp in sorted(families):
            f = families[cp]
            if cp not in of_family:
                report.drop("form_families", "no cluster left")
                continue
            # Each cluster's first and last glyphs, for the family view's representatives and least typical.
            index = {"family": cp, "clusters": {}}
            for n in of_family[cp]:
                name, _data, ms = lists[n]
                look = [[m[0], m[1], m[3], m[5], m[6]] for m in ms]
                index["clusters"][str(n)] = {"list": name, "count": len(ms), "head": look[:HEAD],
                                             "tail": look[max(HEAD, len(look) - TAIL):]}
            data, digest = named(index)
            indexes[cp] = (f"{cp}-{digest}", data)
            row = (cp, f["char"], f["label"], counts[cp, "count"], len(of_family[cp]), f["forms"], counts[cp, "assigned"],
                   counts[cp, "rejected"], f["revision"], indexes[cp][0])
            changed += (row[3], row[4], row[6], row[7]) != (f["count"], f["cluster_count"], f["assigned"], f["rejected"])
            family_rows.append(row)
        report.note(f"form_families: {changed} families whose counts changed with the glyphs left out")
        report.tables["form_families"]["out"] = len(family_rows)
        report.tables["form_clusters"]["out"] = len(cluster_rows)
        report.tables["form_units"]["out"] = sum(len(ms) for _n, _d, ms in lists.values()) + len(outside)
        report.note(f"form_glyphs: {len(glyphs)} clustered glyphs and {len(outside)} outside the clustering with their "
                    f"own decision; {len(lists)} cluster lists, {len(indexes)} family indexes")
        self.rows = {"form_families": family_rows, "form_clusters": cluster_rows, "form_glyphs": glyphs + outside}
        for name, data, _ms in lists.values():
            if not (OUT / "forms" / f"{name}.json.gz").exists():
                self.objects[f"forms/{name}.json.gz"] = data
        for name, data in indexes.values():
            if not (OUT / "forms" / "families" / f"{name}.json.gz").exists():
                self.objects[f"forms/families/{name}.json.gz"] = data

    def groups(self):
        """The statements that turn the local copy's tables into these, in groups; per table the statements,
        their bytes and the rows they change."""
        groups, written = [], {}
        tables = [("codes", [f"INSERT OR IGNORE INTO codes(field, code, key) VALUES('models', {c}, {lit(k)});"
                             for c, k in self.head], len(self.head))]
        for table, (cols, key) in TABLES.items():
            names = ",".join(cols)
            held = {r[0]: r for r in self.atlas.execute(f"SELECT {names} FROM {table}")} if has(self.atlas, table) else {}
            new = {r[0]: tuple(r) for r in self.rows[table]}
            out = [f"DELETE FROM {table} WHERE {key[0]} = {lit(k)};" for k in sorted(held) if k not in new]
            out += [f"INSERT INTO {table}({names}) VALUES({','.join(map(lit, new[k]))}) ON CONFLICT({key[0]}) DO UPDATE SET "
                    + ",".join(f"{c}=excluded.{c}" for c in cols[1:]) + ";" for k in sorted(new) if held.get(k) != new[k]]
            tables.append((table, out, len(out)))
        # Each live crop's cluster, null outside the clustering.
        moves = {}
        for crop, n in self.cluster_of.items():
            if self.clustered.get(crop) != n:
                moves.setdefault(n, []).append(crop)
        cleared = sorted(crop for crop in self.clustered if crop not in self.cluster_of)
        out = []
        for n, crops in [(None, cleared)] + sorted(moves.items()):
            crops = sorted(crops)
            out += [f"UPDATE crops SET cluster = {lit(n)} WHERE id IN ({','.join(map(str, crops[i:i + CLUSTER_IDS]))});"
                    for i in range(0, len(crops), CLUSTER_IDS)]
        tables.append(("crops.cluster", out, len(cleared) + sum(map(len, moves.values()))))
        for table, out, rows in tables:
            groups += [out[i:i + GROUP] for i in range(0, len(out), GROUP)]
            written[table] = (len(out), sum(len(s.encode()) + 1 for s in out), rows)
        return groups, written


def guard(atlas):
    """Refuse a database whose form tables the Worker has written since the local copy was made."""
    if not has(atlas, "form_decisions"):
        return
    local = atlas.execute("SELECT max(seq) FROM form_decisions").fetchone()[0]
    remote = query("glyph-atlas-v3", "SELECT max(seq) AS seq FROM form_decisions")[0]["seq"]
    if remote != local:
        return (f"glyph-atlas-v3 holds form decisions up to {remote}, the local copy up to {local}: the site has decided "
                "forms since, and this load would undo them")
    return None


def remove(names, prefix):
    """Delete objects under a prefix through rclone's S3 interface; a name already gone is no error."""
    if not names:
        return
    done = subprocess.run(["rclone", "delete", f"{R2_REMOTE}:{BUCKET}/{prefix}", "--files-from-raw", "-",
                           "--s3-no-check-bucket"], input="\n".join(names) + "\n", text=True, check=False)
    if done.returncode != 0:
        sys.exit(f"deleting under {prefix} failed; rerun to finish")


def journal(run):
    path = run / "journal"
    return path.read_text().split("\n") if path.exists() else []


def mark(run, line):
    with (run / "journal").open("a") as f:
        f.write(line + "\n")


def apply(run, atlas, part):
    """One part to D1, then to the local copy."""
    stamp = f"{run.name}.{part.stem}"
    if atlas.execute("SELECT 1 FROM imports WHERE stamp=?", (stamp,)).fetchone():
        return
    if not send("glyph-atlas-v3", part, stamp):
        sys.exit(f"{part.name} did not apply; rerun to finish")
    atlas.executescript("BEGIN;\n" + part.read_text() + "COMMIT;\n")


def copy_out(run, files):
    for path in files:
        target = OUT / path.relative_to(run)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


def deliver(run, atlas, mirror, build):
    """Finish a load; each step runs once. The lists first, since the rows name them; `loading` set and the
    decisions checked again; the rows; the vote objects once D1 holds the model codes they name; then the
    lists nothing names any more are cleaned up."""
    for kind, prefix in (("forms/families", FORMS_PREFIX + "families/"), ("forms", FORMS_PREFIX)):
        files = sorted((run / kind).glob("*.json.gz"))
        if files and f"uploaded {kind}" not in journal(run):
            upload(files, prefix)
            copy_out(run, files)
            mark(run, f"uploaded {kind}")
    if (run / "begin.sql").exists():
        apply(run, atlas, run / "begin.sql")
        if problem := guard(atlas):
            # The rows stay unsent: decisions open again, and the lists already up are noted for cleaning.
            release = run / f"release-{len(list(run.glob('release-*.sql'))):04}.sql"
            release.write_text(stamped("UPDATE form_state SET loading = 0 WHERE id = 0;", f"{run.name}.{release.stem}"))
            apply(run, atlas, release)
            clean(run, atlas)
            mirror.execute("DELETE FROM pending WHERE run=?", (FACTS_RUN + run.name,))
            mirror.commit()
            sys.exit(problem)
        for part in sorted(run.glob("rows-*.sql")):
            apply(run, atlas, part)
        apply(run, atlas, run / "end.sql")
    files = sorted((run / "votes").glob("*.json.gz"))
    if files and "uploaded votes" not in journal(run):
        upload(files, votes.prefix(build))
        copy_out(run, files)
        mark(run, "uploaded votes")
    clean(run, atlas)
    mirror.execute("DELETE FROM pending WHERE run=?", (FACTS_RUN + run.name,))
    mirror.commit()


def clean(run, atlas):
    """Note each object under v3/forms/ in the manifest; one no row and no decision names is deleted once
    it has been unnamed KEEP_UNNAMED seconds."""
    local = OUT / "forms" / MANIFEST
    manifest = json.loads(gzip.decompress(local.read_bytes())) if local.exists() else {}
    before = dict(manifest)
    for path in (run / "forms").rglob("*.json.gz") if (run / "forms").exists() else []:
        manifest.setdefault(str(path.relative_to(run / "forms")), None)
    names = {f"{r[0]}.json.gz" for r in atlas.execute("SELECT list FROM form_clusters")}
    names |= {f"families/{r[0]}.json.gz" for r in atlas.execute("SELECT list FROM form_families")}
    names |= {f"{r['list']}.json.gz" for r in query("glyph-atlas-v3", "SELECT DISTINCT list FROM form_decisions WHERE list IS NOT NULL")}
    now, gone = int(time.time()), []
    for name, since in manifest.items():
        if name in names:
            manifest[name] = None
        elif since is None:
            manifest[name] = now
        elif now - since >= KEEP_UNNAMED:
            gone.append(name)
    for folder in ("", "families/"):
        remove([g[len(folder):] for g in gone if g.startswith(folder) and "/" not in g[len(folder):]], FORMS_PREFIX + folder)
    for name in gone:
        del manifest[name]
        (OUT / "forms" / name).unlink(missing_ok=True)
    if manifest != before:
        path = run / "manifest" / MANIFEST
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(gzip.compress(votes.compact(manifest).encode(), mtime=0))
        upload([path], FORMS_PREFIX)
        local.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, local)
    if gone:
        print(f"{len(gone)} objects under {FORMS_PREFIX} no row or decision names deleted", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--send", action="store_true", help="apply migration 0006, upload the objects and send the rows")
    args = parser.parse_args()
    dry = not args.send
    lock = open(OUT / "mirror.lock", "w")  # noqa: SIM115 -- held until the process exits
    fcntl.flock(lock, fcntl.LOCK_EX)
    atlas = sqlite3.connect(OUT / "atlas.sqlite")
    mirror = sqlite3.connect(OUT / "mirror.sqlite")
    mirror.execute("CREATE TABLE IF NOT EXISTS pending(run TEXT PRIMARY KEY)")
    mirror.commit()
    build = mirror.execute("SELECT value FROM meta WHERE key='build'").fetchone()[0]
    pending = [r[0] for r in mirror.execute("SELECT run FROM pending ORDER BY run")]
    if any(not p.startswith(FACTS_RUN) for p in pending):
        sys.exit("a publish is unfinished; finish it with publish.py first")
    if pending and dry:
        sys.exit(f"the load {pending[0].removeprefix(FACTS_RUN)} is unfinished; finish it with --send first")
    if not dry:
        if not applied("glyph-atlas-v3", f"build:{build}"):
            sys.exit(f"glyph-atlas-v3 was not loaded from build {build}; the local copy does not describe it")
        # A load stopped part way is finished first; one stopped before its folder was written starts again.
        for name in pending:
            run = OUT / "crop-facts" / name.removeprefix(FACTS_RUN)
            if run.exists():
                print(f"finishing {run.name}", flush=True)
                deliver(run, atlas, mirror, build)
            else:
                mirror.execute("DELETE FROM pending WHERE run=?", (name,))
                mirror.commit()
        if problem := guard(atlas):
            sys.exit(problem)
        migrate(atlas, build)

    facts = Facts(mirror, atlas, build)
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
    # Every statement fits D1 before the numbers the rows name are kept.
    fits(groups)
    extra = dict(facts.objects)
    if groups:
        extra["begin.sql"] = stamped("UPDATE form_state SET loading = 1 WHERE id = 0;", f"{run.name}.begin")
        extra["end.sql"] = stamped(f"UPDATE form_state SET lists = {lit(run.name)}, loading = 0 WHERE id = 0;",
                                   f"{run.name}.end")
    if dry:
        shutil.rmtree(run, ignore_errors=True)
        mirror.rollback()
    elif groups or facts.objects:
        # The numbers the rows name, and that this load is pending, are kept before anything is sent.
        mirror.execute("INSERT OR IGNORE INTO pending VALUES(?)", (FACTS_RUN + run.name,))
        mirror.commit()
    n = built(run, "rows", groups, extra)
    lines = facts.report.lines()[:-2]
    lines += [f"{table}: {k} statements, {b} bytes, {rows} rows changed" for table, (k, b, rows) in written.items()]
    for kind in ("votes/", "forms/families/", "forms/"):
        objects = [v for k, v in facts.objects.items() if k.startswith(kind) and (kind != "forms/" or k.count("/") == 1)]
        lines.append(f"{kind} objects to upload: {len(objects)}, {sum(map(len, objects))} bytes")
    lines += [f"{n if n is not None else 'existing'} parts", ""]
    text = "\n".join(lines)
    (run / "report.txt").write_text(text)
    (OUT / "crop-facts-report.txt").write_text(text)
    print(text, flush=True)
    if dry:
        print(f"dry run: nothing sent; run in {run}", flush=True)
        return
    deliver(run, atlas, mirror, build)
    print(f"crop facts loaded from {run.name}", flush=True)


if __name__ == "__main__":
    main()
