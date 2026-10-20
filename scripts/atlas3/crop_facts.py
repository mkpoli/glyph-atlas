"""Load the crop facts into the new layout (decision record 0008): the forms tool's clustering, and at
the cutover the model votes as page objects in R2.

    ATLAS3_DIR=work/atlas-v3 python3 scripts/atlas3/crop_facts.py [--clusters DIR] [--votes] [--send]

Every text id resolves through out/mirror.sqlite, never through a redirect. A glyph naming a crop the
mirror lacks, or a crop no longer live (retired), is dropped and counted.

- Forms: the clustering is `work/forms/current` (`clusters.json`, `units.parquet`, `neighbours.json`,
  `embeddings.npy`; `--clusters` names another). Clusters are numbered by the mirror's `codes` (field
  'form_cluster', key the cluster's id), so a cluster keeps its number across loads. Each cluster's
  glyphs, by rank, go to the R2 object `v3/forms/<n>-<digest>.json.gz` (`{family, key, members: [[crop,
  rank, similarity, image, split, tone, image_size]]}`, `image` the tile's media key, `split` the group
  of each "Split into k" for k = 2..8, `tone` and `image_size` from the crop's page bundle), named by
  its content, so only a changed cluster's list is uploaded; each family's first 12 and last 24 glyphs
  per cluster go to its index object `v3/forms/families/<code point>-<digest>.json.gz`.
- Tiles: each clustered glyph whose pixels are on disk (`--corpus-root`) has a 240-pixel tile, keyed as
  the site's media; a key the image map (`out/media/`) lacks is rendered, packed into `packs/<sha>.bin`
  and uploaded, then merged into the map (media_map.py), before any list names it. A dry run counts them.
- Decisions come from glyph-atlas-v3's own `form_decisions` (the local copy): each cluster's latest
  cluster decision by its key, whichever clustering it was made on, and each crop's latest glyph or
  inherit decision; an inherit decision leaves the glyph to its cluster. A glyph carries its latest
  cluster decision (read from the glyphs the decision listed, or the list a site decision names) when
  that is not its cluster's and its cluster has not been decided since; `carried` (migration 0014)
  names it. `form_families` and `form_clusters` take the clustering's rows with the cluster decisions,
  `form_glyphs` the glyphs with their own or a carried decision, and `crops.cluster` each live crop's
  cluster, null outside the clustering. Counts are recomputed from the glyphs kept.
- An object under `v3/forms/` that no row and no decision names is deleted by a load at least
  KEEP_UNNAMED seconds after a load first found it unnamed, from the manifest `v3/forms/manifest.json.gz`
  (local copy out/forms/manifest.json.gz).
- Votes (`--votes`, the cutover load only): each collected crop's latest reading in the copy
  `source-unit_facts.sqlite` (`pull.py unit_facts`) joins its page object `v3/votes/<build>/<page>.json.gz`
  (votes.py) for the crop's cut, when the image it was read on (`image_sha256`) is that cut's image in
  the crop's bundle; a reading of another image is dropped and counted. Entries a publish wrote for the
  crop's cut stay; those an earlier load wrote are written again from this copy.

Rows go to D1 as the difference from the local copy (out/atlas.sqlite), as books.py sends them: a
rerun after a new clustering inserts, updates and removes only what changed. A load onto an empty layout
writes about 1.33M `crops` rows (one per clustered glyph), so it waits for the owner's approval with the
dry run's figures; `--defer-crops` leaves those updates to `fill.py --send`, which fills cluster,
family and priority in one pass, keeps form decisions paused and records a durable handoff (finish the
fill before publishing or loading facts again). `--send` refuses a database holding form decisions the local copy lacks, sets
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
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import media_map
import votes
from build import HERE, OUT, PAGE, ROOT
from history import Report
from load_history import GROUP, SCHEMA, built, stamped
from publish import BUCKET, FACTS_RUN, R2_REMOTE, applied, fits, lit, query, send, upload

FORMS_PREFIX = "v3/forms/"
MANIFEST = "manifest.json.gz"
# A family's index object holds per cluster its first HEAD and last TAIL glyphs.
HEAD, TAIL = 12, 24
# The Worker keeps a forms answer an hour and an object five minutes; an unnamed object outlives both.
KEEP_UNNAMED = 2 * 3600
# Crop ids per `UPDATE crops SET cluster` statement, about 60 KB.
CLUSTER_IDS = 5000
# A tile's longest edge, the "Split into k" choices each glyph carries its group for, and a pack's size.
TILE_EDGE = 240
SPLITS = range(2, 9)
PACK_BYTES = 32 * 1024**2
# Each table crop_facts.py owns, its columns and key.
TABLES = {
    "form_families": (("code_point", "char", "label", "count", "cluster_count", "forms", "assigned", "rejected",
                       "revision", "list"), ("code_point",)),
    "form_clusters": (("id", "key", "family", "label", "count", "coherence", "shape_position", "size_position", "list",
                       "form", "issue", "character", "written_family", "decision"), ("id",)),
    "form_glyphs": (("crop", "family", "cluster", "form", "issue", "character", "written_family", "decision", "carried"),
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


def carries(atlas):
    """Whether the layout's `form_glyphs` has `carried` (migration 0014)."""
    return "carried" in {r[1] for r in atlas.execute("PRAGMA table_info(form_glyphs)")}


def migrate(atlas, build):
    """Migrations 0006 and 0014 on D1 and the local copy, once each; 0006 with the build id the Worker
    finds the vote objects by. A layout built with them already has them."""
    for number, name, present, extra in (
            ("0006", "0006_crop_facts.sql", lambda: has(atlas, "form_families"),
             f"\nINSERT OR IGNORE INTO codes(field, code, key) VALUES('build', 0, {lit(build)});\n"),
            ("0014", "0014_form_carried.sql", lambda: carries(atlas), "")):
        if present():
            continue
        path = OUT / "crop-facts" / f"{number}.sql"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(stamped((SCHEMA / name).read_text() + extra, f"migration:{number}"))
        if not applied("glyph-atlas-v3", f"migration:{number}") and not send("glyph-atlas-v3", path, f"migration:{number}"):
            sys.exit(f"migration {number} did not apply; rerun")
        atlas.executescript("BEGIN;\n" + path.read_text() + "COMMIT;\n")
        print(f"migration {number} applied", flush=True)


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


def clustering(directory):
    """The clustering in `directory`, read as the forms tool reads it."""
    os.environ["ATLAS_FORM_CLUSTERS"] = str(Path(directory).resolve())
    from glyph_atlas import forms

    data = forms.clusters()
    if data["revision"] is None:
        sys.exit(f"{directory} holds no clustering; run `atlas forms cluster` first")
    return forms, data


def cluster_decisions(atlas):
    """Each cluster key's latest cluster decision in the loaded form_decisions:
    (form, issue, character, written family, decision id, revision)."""
    latest = {}
    for key, *row in atlas.execute("SELECT cluster, form, issue, character, written_family, id, revision FROM form_decisions "
                                   "WHERE kind='cluster' AND cluster IS NOT NULL ORDER BY at, seq"):
        latest[key] = tuple(row)
    return latest


def glyph_decisions(atlas):
    """Each crop's latest glyph decision in the loaded form_decisions: (family, form, issue, character,
    written family, decision id); an inherit decision after it leaves the crop none."""
    latest = {}
    for crop, kind, *row in atlas.execute(
            "SELECT c.value, d.kind, d.family, d.form, d.issue, d.character, d.written_family, d.id "
            "FROM form_decisions d, json_each(d.crops) c WHERE d.kind IN ('glyph', 'inherit') ORDER BY d.at, d.seq"):
        if kind == "glyph":
            latest[crop] = tuple(row)
        else:
            latest.pop(crop, None)
    return latest


def cluster_decisions_by_crop(atlas):
    """Each crop's latest cluster decision by the glyphs it was made on, and every cluster decision's place
    in the order they were made and what its glyphs take from it: ((form, issue, character, written
    family), family). A decision the history loaded lists its glyphs in `crops`; one made on the site
    names the list it was made on, whose glyphs are read from the local copy of that list (out/forms/)."""
    latest, order, says, missing = {}, {}, {}, []
    # A layout before migration 0006 has no `list`: every decision there came with the history.
    listed_by = "list" if "list" in {r[1] for r in atlas.execute("PRAGMA table_info(form_decisions)")} else "NULL"
    for n, (identity, family, form, issue, character, written, crops, listed) in enumerate(atlas.execute(
            f"SELECT id, family, form, issue, character, written_family, crops, {listed_by} FROM form_decisions "
            "WHERE kind='cluster' ORDER BY at, seq")):
        order[identity] = n
        says[identity] = (applies(None, (form, issue, character, written)), family)
        members = json.loads(crops or "[]")
        if not members and listed:
            path = OUT / "forms" / f"{listed}.json.gz"
            if not path.exists():
                missing.append(listed)
                continue
            members = [m[0] for m in json.loads(gzip.decompress(path.read_bytes()))["members"]]
        for crop in members:
            latest[crop] = identity
    if missing:
        sys.exit(f"{len(missing)} cluster decisions name a list out/forms/ lacks, so their glyphs are unknown: {missing[:5]}")
    return latest, order, says


def tile_keys(corpus_root, wanted):
    """Each glyph's tile key, for the glyphs of `wanted` whose pixels are on disk, keyed as the site's
    media (a key names the source file, its stamp and the box); and the cache that renders them."""
    from glyph_atlas import form_clusters
    from glyph_atlas.review.media import MediaCache

    media = MediaCache(corpus_root=corpus_root)
    pixels = form_clusters.Pixels(corpus_root)
    found = {g["id"]: at for g in form_clusters.glyphs(corpus_root, wanted) if (at := pixels(g)) is not None}
    keys = {}
    # File by file, so each scan is stat'ed and decoded once.
    for identity, (path, box) in sorted(found.items(), key=lambda item: str(item[1][0])):
        keys[identity] = media.local(path, list(box) if box else None, edge=TILE_EDGE).rsplit("/", 1)[-1].removesuffix(".webp")
    return keys, media


def mapped(keys):
    """The keys the image map (out/media/) already places."""
    shards, held = {}, set()
    for key in keys:
        prefix = key[:3]
        if prefix not in shards:
            shards[prefix] = media_map.read_shard(prefix)["keys"]
        if key[3:] in shards[prefix]:
            held.add(key)
    return held


def pack(media, keys, folder, workers):
    """Render `keys` and pack them into content-named files under `folder`; the image map's rows and the
    keys that did not render."""
    folder.mkdir(parents=True, exist_ok=True)

    def render(key):
        try:
            return key, media.materialize(key)
        except (OSError, ValueError):
            return key, None

    rows, pending, failed, handle = [], [], set(), None

    def seal():
        handle.close()
        opened = folder / "open.bin"
        with opened.open("rb") as f:
            sha = hashlib.file_digest(f, "sha256").hexdigest()
        opened.rename(folder / f"{sha}.bin")
        rows.extend((key, f"packs/{sha}.bin", offset, size, media_map.WEBP) for key, offset, size in pending)
        pending.clear()

    with ThreadPoolExecutor(workers) as pool:
        for n, (key, path) in enumerate(pool.map(render, sorted(keys)), 1):
            if path is None:
                failed.add(key)
                continue
            if handle is None:
                handle = (folder / "open.bin").open("wb")
            body = path.read_bytes()
            pending.append((key, handle.tell(), len(body)))
            handle.write(body)
            if handle.tell() >= PACK_BYTES:
                seal()
                handle = None
            if n % 20000 == 0:
                print(f"tiles: {n} of {len(keys)} rendered", flush=True)
    if handle is not None:
        seal()
    return rows, failed


def put_packs(files):
    """Packs to R2 under `packs/`, where the image map names them; a pack is named by its content, so a
    resend writes the same bytes again."""
    if not files:
        return
    folder = files[0].parent
    names = "\n".join(f.name for f in files) + "\n"
    for attempt in range(4):
        done = subprocess.run(["rclone", "copy", str(folder), f"{R2_REMOTE}:{BUCKET}/packs", "--files-from-raw", "-",
                               "--transfers", "4", "--s3-no-check-bucket", "--header-upload",
                               "Content-Type: application/octet-stream"], input=names, text=True, check=False)
        listed = subprocess.run(["rclone", "lsf", f"{R2_REMOTE}:{BUCKET}/packs", "--files-from-raw", "-"],
                                input=names, text=True, capture_output=True, check=False)
        if done.returncode == 0 and set(listed.stdout.split()) >= {f.name for f in files}:
            return
        time.sleep(10 * (attempt + 1))
    sys.exit("the tile packs did not upload; rerun")


class Facts:
    def __init__(self, mirror, atlas, build):
        self.report, self.mirror, self.atlas, self.build = Report(), mirror, atlas, build
        self.ids = dict(mirror.execute("SELECT text, id FROM ids"))
        self.live = dict(atlas.execute("SELECT id, cut FROM crops"))
        self.clustered = dict(atlas.execute("SELECT id, cluster FROM crops WHERE cluster IS NOT NULL"))
        self.revs = dict(atlas.execute("SELECT id, rev FROM pages"))
        self.head, self.rows, self.objects, self.entries, self.cluster_of = [], {}, {}, {}, {}

    def entry(self, crop):
        """The crop's entry in its page bundle: (image hash of its current cut, tone, image size)."""
        pid = crop // PAGE
        if pid not in self.entries:
            path = OUT / "pages" / f"{pid}-{self.revs.get(pid)}.json.gz"
            self.entries[pid] = {e["slot"]: (e.get("image_sha256"), e.get("tone"), e.get("image_size"))
                                 for e in json.loads(gzip.decompress(path.read_bytes()))["crops"]
                                 if not e.get("retired")} if path.exists() else {}
        return self.entries[pid].get(crop % PAGE, (None, None, None))

    def votes(self):
        report = self.report
        report.table("unit_facts", 0)
        fresh = {}
        for r in latest("unit_facts", "id"):
            if r["origin"] != "local":
                continue
            report.tables["unit_facts"]["in"] += 1
            crop = self.ids.get(r["id"])
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
            current = self.entry(crop)[0]
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

    def glyphs(self, data):
        """The clustering's glyphs by crop: {identity: crop} for those kept, the others dropped and counted."""
        report = self.report
        report.table("form_units", len(data["units"]))
        kept = {}
        for identity in data["units"]:
            crop = self.ids.get(identity)
            if crop is None:
                report.drop("form_units", "crop the mirror lacks")
            elif crop not in self.live:
                report.drop("form_units", "crop no longer live (retired)")
            else:
                kept[identity] = crop
        return kept

    def tiles(self, kept, corpus_root, send, workers):
        """Each kept glyph's tile key, with the tiles the image map lacks rendered, uploaded and mapped
        first when sending; a dry run counts them."""
        keys, media = tile_keys(corpus_root, set(kept))
        held = mapped(set(keys.values()))
        wanted = set(keys.values()) - held
        failed = set()
        if send and wanted:
            folder = OUT / "crop-facts" / "tiles"
            shutil.rmtree(folder, ignore_errors=True)
            rows, failed = pack(media, wanted, folder, workers)
            # The packs first, so no key the map gains names a pack R2 lacks.
            put_packs(sorted(folder.glob("*.bin")))
            media_map.merge(rows)
            shutil.rmtree(folder)
        if send:
            media_map.send(upload)
        self.report.note(f"tiles: {len(keys)} glyphs with pixels on disk, {len(kept) - len(keys)} without (no tile); "
                         f"{len(held)} tiles the image map holds, {len(wanted)} {'rendered' if send else 'to render'}"
                         f"{f', {len(failed)} of them failed (no tile)' if failed else ''}")
        return {identity: key for identity, key in keys.items() if key not in failed}

    def forms(self, directory, corpus_root, send, workers):
        report = self.report
        if not has(self.atlas, "form_decisions"):
            sys.exit("the local copy has no form_decisions; load the history (load_history.py) first")
        forms, data = clustering(directory)
        from glyph_atlas.review.forms import _form_entry, shape_runs

        kept = self.glyphs(data)
        tiles = self.tiles(kept, corpus_root, send, workers)
        decided_last, glyph_last = cluster_decisions(self.atlas), glyph_decisions(self.atlas)
        by_crop, order, says = cluster_decisions_by_crop(self.atlas)

        def carried_by(crop, decision):
            """The cluster decision a glyph carries: its latest cluster decision, when that is not its
            cluster's and its cluster has not been decided since."""
            mark = by_crop.get(crop)
            if mark is None or mark == decision or (decision is not None and order[decision] > order[mark]):
                return None
            return mark
        report.table("form_families", len(data["families"]))
        report.table("form_clusters", sum(len(f["clusters"]) for f in data["families"].values()))
        cluster_rows, lists, glyphs, counts, of_family, families = [], {}, [], Counter(), {}, {}
        carried, older, carrying = set(), 0, Counter()
        for cp, family in sorted(data["families"].items()):
            clusters = family["clusters"]
            near = data["neighbours"].get(cp, {})
            shape = shape_runs(near, clusters) if near.get("order") else [c["id"] for c in clusters]
            for size_position, c in enumerate(clusters):
                key = c["id"]
                ms = sorted((data["units"][i][3], kept[i], data["units"][i][2], i) for i in data["members"][key] if i in kept)
                if not ms:
                    report.drop("form_clusters", "no glyph left")
                    continue
                groups = {k: {i: g for g, ids in enumerate(forms.split(key, k)) for i in ids} for k in SPLITS}
                n = numbered(self.mirror, "form_cluster", key)
                *decided, decision, revision = decided_last.get(key, (None,) * 6)
                if decision is not None:
                    carried.add(key)
                    older += revision != data["revision"]
                body = {"family": cp, "key": key, "members": [
                    [crop, rank, sim, tiles.get(i), "".join(str(groups[k][i]) for k in SPLITS), *self.entry(crop)[1:]]
                    for rank, crop, sim, i in ms]}
                blob, digest = named(body)
                lists[n] = (f"{n}-{digest}", blob, body["members"])
                cluster_rows.append((n, key, cp, c["label"], len(ms), c["coherence"], shape.index(key), size_position,
                                     lists[n][0], *decided, decision))
                of_family.setdefault(cp, []).append(n)
                for _rank, crop, _sim, _i in ms:
                    self.cluster_of[crop] = n
                    mine, mark = glyph_last.get(crop), carried_by(crop, decision)
                    own = mine[1:5] if mine else None
                    # A carried decision is the glyph's cluster form; its own decision still comes first.
                    got = applies(own or (says[mark][0] if mark else None), tuple(decided))
                    counts[cp, "count"] += 1
                    counts[cp, "assigned"] += got[0] is not None
                    counts[cp, "rejected"] += got[1] is not None
                    carrying["clustered"] += mark is not None
                    if mine:
                        glyphs.append((crop, cp, n, *own, mine[5], mark))
                    elif mark:
                        glyphs.append((crop, cp, n, *says[mark][0], mark, mark))
            families[cp] = family
        stray = sorted(k for k in decided_last if k not in carried)
        report.note(f"form_decisions: {len(carried)} clusters take their latest cluster decision by key, {older} of them "
                    f"made on an earlier clustering; {len(stray)} cluster keys with decisions name no cluster of this one "
                    "with glyphs left", stray)
        # Glyphs outside the clustering with their own decision, or carrying a cluster decision, keep it;
        # one on a retired crop is dropped.
        outside, gone = [], 0
        for crop in sorted(glyph_last.keys() | by_crop.keys()):
            if crop in self.cluster_of:
                continue
            if crop not in self.live:
                gone += 1
                continue
            mine, mark = glyph_last.get(crop), carried_by(crop, None)
            carrying["outside"] += mark is not None
            if mine:
                family, *own, decision = mine
                outside.append((crop, family, None, *own, decision, mark))
            else:
                outside.append((crop, says[mark][1], None, *says[mark][0], mark, mark))
        report.note(f"form_glyphs: {len(glyphs)} clustered glyphs and {len(outside)} outside the clustering with their own "
                    f"or a carried decision; {carrying['clustered']} clustered and {carrying['outside']} outside carry a "
                    f"cluster decision their cluster has not had since; {gone} decisions on crops no longer live left out")

        family_rows, indexes = [], {}
        for cp in sorted(families):
            f = families[cp]
            if cp not in of_family:
                report.drop("form_families", "no cluster left")
                continue
            # Each cluster's first and last glyphs, for the family view's representatives and least typical.
            index = {"family": cp, "clusters": {}}
            for n in of_family[cp]:
                name, _blob, ms = lists[n]
                look = [[m[0], m[1], m[3], m[5], m[6]] for m in ms]
                index["clusters"][str(n)] = {"list": name, "count": len(ms), "head": look[:HEAD],
                                             "tail": look[max(HEAD, len(look) - TAIL):]}
            blob, digest = named(index)
            indexes[cp] = (f"{cp}-{digest}", blob)
            entries = json.dumps([_form_entry(char) for char in forms.family_members(cp)], ensure_ascii=False)
            family_rows.append((cp, f["char"], f["label"], counts[cp, "count"], len(of_family[cp]), entries,
                                counts[cp, "assigned"], counts[cp, "rejected"], data["revision"], indexes[cp][0]))
        report.tables["form_families"]["out"] = len(family_rows)
        report.tables["form_clusters"]["out"] = len(cluster_rows)
        report.tables["form_units"]["out"] = sum(len(ms) for _n, _d, ms in lists.values())
        report.note(f"forms: clustering {data['revision']}; {len(lists)} cluster lists, {len(indexes)} family indexes")
        self.rows = {"form_families": family_rows, "form_clusters": cluster_rows, "form_glyphs": glyphs + outside}
        for name, blob, _ms in lists.values():
            if not (OUT / "forms" / f"{name}.json.gz").exists():
                self.objects[f"forms/{name}.json.gz"] = blob
        for name, blob in indexes.values():
            if not (OUT / "forms" / "families" / f"{name}.json.gz").exists():
                self.objects[f"forms/families/{name}.json.gz"] = blob

    def groups(self, defer_crops=False):
        """The statements that turn the local copy's tables into these, in groups; per table the statements,
        their bytes and the rows they change."""
        groups, written = [], {}
        tables = [("codes", [f"INSERT OR IGNORE INTO codes(field, code, key) VALUES('models', {c}, {lit(k)});"
                             for c, k in self.head], len(self.head))]
        for table, (cols, key) in TABLES.items():
            names = ",".join(cols)
            present = {r[1] for r in self.atlas.execute(f"PRAGMA table_info({table})")}
            # A dry run before migration 0014 reads `carried` as empty.
            read = ",".join(c if c in present else "NULL" for c in cols)
            held = {r[0]: r for r in self.atlas.execute(f"SELECT {read} FROM {table}")} if present else {}
            new = {r[0]: tuple(r) for r in self.rows[table]}
            out = [f"DELETE FROM {table} WHERE {key[0]} = {lit(k)};" for k in sorted(held) if k not in new]
            out += [f"INSERT INTO {table}({names}) VALUES({','.join(map(lit, new[k]))}) ON CONFLICT({key[0]}) DO UPDATE SET "
                    + ",".join(f"{c}=excluded.{c}" for c in cols[1:]) + ";" for k in sorted(new) if held.get(k) != new[k]]
            tables.append((table, out, len(out)))
        # Each live crop's cluster, null outside the clustering.
        moves = {}
        for crop, n in ([] if defer_crops else self.cluster_of.items()):
            if self.clustered.get(crop) != n:
                moves.setdefault(n, []).append(crop)
        cleared = [] if defer_crops else sorted(crop for crop in self.clustered if crop not in self.cluster_of)
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
    if (run / "end.sql").exists():
        # Each attempt sets `loading` under its own stamp: a rerun after a refused attempt sets it again.
        attempt = len(list(run.glob("release-*.sql")))
        begin = run / f"begin-{attempt:04}.sql"
        if not begin.exists():
            begin.write_text(stamped("UPDATE form_state SET loading = 1 WHERE id = 0;", f"{run.name}.{begin.stem}"))
        apply(run, atlas, begin)
        if problem := guard(atlas):
            # The rows stay unsent: decisions open again, and the lists already up are noted for cleaning.
            release = run / f"release-{attempt:04}.sql"
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
    if (run / "defer-crops").exists():
        mirror.execute("INSERT OR IGNORE INTO pending VALUES(?)", ("fill/source/" + run.name,))
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
    # The local copies go first: after a failed delete any of these may be gone from R2, so a load that
    # names one again uploads it; the manifest keeps them until the delete succeeds.
    for name in gone:
        (OUT / "forms" / name).unlink(missing_ok=True)
    for folder in ("", "families/"):
        remove([g[len(folder):] for g in gone if g.startswith(folder) and "/" not in g[len(folder):]], FORMS_PREFIX + folder)
    for name in gone:
        del manifest[name]
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
    parser.add_argument("--clusters", type=Path, default=ROOT / "work/forms/current", help="the clustering to load")
    parser.add_argument("--corpus-root", type=Path, default=ROOT / "work", help="corpus root holding the glyphs' pixels")
    parser.add_argument("--workers", type=int, default=8, help="threads rendering tiles")
    parser.add_argument("--votes", action="store_true", help="also load the model votes from source-unit_facts.sqlite "
                        "(the cutover load)")
    parser.add_argument("--send", action="store_true", help="apply migrations 0006 and 0014, upload the objects and send the rows")
    parser.add_argument("--defer-crops", action="store_true", help="leave cluster updates to fill.py with family and priority")
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
    if any(p.startswith("fill/") for p in pending):
        sys.exit("the crop fill is unfinished; finish it with fill.py --send first")
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
        if mirror.execute("SELECT 1 FROM pending WHERE run LIKE 'fill/%'").fetchone():
            print("crop facts loaded; finish the deferred crop fill with fill.py --send", flush=True)
            return
        if problem := guard(atlas):
            sys.exit(problem)
        migrate(atlas, build)

    facts = Facts(mirror, atlas, build)
    if args.votes:
        facts.votes()
    facts.forms(args.clusters, args.corpus_root, not dry, args.workers)
    groups, written = facts.groups(args.defer_crops)
    digest = hashlib.sha256(b"defer-crops" if args.defer_crops else b"")
    for g in groups:
        digest.update("\n".join(g).encode())
    for name in sorted(facts.objects):
        digest.update(name.encode() + hashlib.sha256(facts.objects[name]).digest())
    name = "facts-" + digest.hexdigest()[:12]
    run = OUT / "crop-facts" / (name + "-dry" if dry else name)
    if args.defer_crops and not dry and not groups and not facts.objects and atlas.execute(
            "SELECT 1 FROM imports WHERE stamp=?", (f"{name}.end",)).fetchone():
        print("the identical deferred load already completed; nothing to send", flush=True)
        return
    # Every statement fits D1 before the numbers the rows name are kept.
    fits(groups)
    extra = dict(facts.objects)
    if args.defer_crops:
        extra["defer-crops"] = "fill.py owns the remaining crop updates\n"
    if groups or args.defer_crops:
        extra["end.sql"] = stamped(f"UPDATE form_state SET lists = {lit(run.name)}, loading = {int(args.defer_crops)} WHERE id = 0;",
                                   f"{run.name}.end")
    if dry:
        shutil.rmtree(run, ignore_errors=True)
        mirror.rollback()
    elif groups or facts.objects or args.defer_crops:
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
