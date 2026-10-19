"""Publish a catalogue in today's export format into the new layout (decision record 0005).

    python3 publish.py <catalogue.sqlite> [--dry-run]

The catalogue is what export_cloudflare.py / export_cloudflare_corpus.py write: `units`, and when
present `corpus_units`, `media`, `unit_ngrams`, `pages`, `document_dating`.

- Ids come from out/mirror.sqlite. A new text id gets a page and slot once; the allocation and a
  record of the pending publish are saved in one transaction before anything is sent.
- Only what differs from the local copy (out/atlas.sqlite, out/atlas-runs.sqlite) is sent, and a
  field the catalogue does not carry keeps its published value. Review-owned columns change only
  when the catalogue's revision is at least the published one, and in D1 only while the crop's
  revision is unchanged; the outcome is read back.
- A publish that would move a known crop to another page, change a known crop's box or image, or
  add a box overlapping a published crop on its page (IoU >= 0.5) is refused with a report: those
  need the slot matcher.
- Order: page bundles to R2, then the rows to glyph-atlas-v3 in parts that never split a page (its
  crops and its revision go together), then the runs to glyph-atlas-v3-runs. Each part counts as
  applied when its stamp is in that database's `imports`, and is then replayed on the local copy.
- A publish stopped after allocating is finished by the next run before anything else.

One publish runs at a time (a lock on out/mirror.lock).
"""
import fcntl
import gzip
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

from build import CORPUS_PAGE, PAGE, char_text, shuffle

ROOT = Path(__file__).resolve().parents[2]
# Copies of the live tables and the build output live outside the repository.
HERE = Path(os.environ.get("ATLAS3_DIR", ROOT / "work/atlas-v3"))
OUT = HERE / "out"
CF = ROOT / "apps/cloudflare"
BUNX = shutil.which("bunx") or "bunx"
# The rclone remote that reaches the site's R2 account.
R2_REMOTE = os.environ.get("ATLAS3_R2_REMOTE", "r2")
BUCKET = "glyph-atlas"
BUNDLE_PREFIX = "v3/pages/"
CROP_COLS = ("id", "kind", "book", "char", "form", "cluster", "visual_group", "production", "category", "style",
             "state", "quiz", "year", "cut", "revision", "flags", "shuffle")
# Columns a review owns.
REVIEW_OWNED = ("char", "state", "style", "quiz", "kind", "flags")
# What a crop takes when it is new and the catalogue does not say.
DEFAULTS = {"form": None, "cluster": None, "category": None, "year": None, "cut": 0, "flags": 0}
SUSPECT = 1
BUNDLE_KEYS = ("label", "script", "line_id", "box", "crop_box", "context_box", "image_sha256", "image",
               "context_image", "repair", "gate", "text", "page_number", "source", "licence", "holder",
               "attribution", "rights_url", "tone", "image_size")
RUN_KEY = ("size", "graphemes", "hand", "shuffle", "first")
# D1 takes an imported file whole; parts stay under the size the publication scripts use.
PART_BYTES = 40 * 1024 ** 2
KEEP = object()


def lit(v):
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return str(int(v))
    if isinstance(v, (int, float)):
        return str(v)
    return "'" + str(v).replace("'", "''") + "'"


def insert(table, cols, values):
    return f"INSERT INTO {table}({','.join(cols)}) VALUES({','.join(map(lit, values))});"


def iou(a, b):
    if not a or not b:
        return 0.0
    x = max(0, min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"]))
    y = max(0, min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"]))
    inter = x * y
    union = a["w"] * a["h"] + b["w"] * b["h"] - inter
    return inter / union if union > 0 else 0.0


def wrangler(*args, capture=False):
    return subprocess.run([BUNX, "wrangler", *args], cwd=CF, capture_output=capture, text=True, check=False)


def query(database, sql):
    for _ in range(5):
        out = wrangler("d1", "execute", database, "--remote", "--json", "--command", sql, capture=True)
        try:
            return json.loads(out.stdout)[0]["results"]
        except (json.JSONDecodeError, KeyError, IndexError, TypeError):
            time.sleep(15)
    sys.exit(f"could not read {database}; rerun to finish")


def applied(database, stamp):
    return query(database, f"SELECT count(*) AS n FROM imports WHERE stamp={lit(stamp)}")[0]["n"] == 1


def send(database, file, stamp):
    """Import one SQL file; D1 applies it whole or not at all, and wrangler can report an applied file
    as failed, so it counts as applied when its stamp is there."""
    for _ in range(4):
        if applied(database, stamp):
            return True
        wrangler("d1", "execute", database, "--remote", "--yes", "--file", str(file))
        time.sleep(5)
    return applied(database, stamp)


def upload(files):
    """Bundles to R2 through rclone's S3 interface; a bundle name is never reused, so a resend is harmless."""
    if not files:
        return
    folder = files[0].parent
    names = "\n".join(f.name for f in files) + "\n"
    for attempt in range(4):
        done = subprocess.run(["rclone", "copy", str(folder), f"{R2_REMOTE}:{BUCKET}/{BUNDLE_PREFIX}", "--files-from-raw", "-",
                               "--transfers", "16", "--s3-no-check-bucket", "--header-upload", "Content-Type: application/json",
                               "--header-upload", "Content-Encoding: gzip"], input=names, text=True, check=False)
        listed = subprocess.run(["rclone", "lsf", f"{R2_REMOTE}:{BUCKET}/{BUNDLE_PREFIX}", "--files-from-raw", "-"],
                                input=names, text=True, capture_output=True, check=False)
        if done.returncode == 0 and set(listed.stdout.split()) >= {f.name for f in files}:
            return
        time.sleep(10 * (attempt + 1))
    sys.exit("bundle upload failed; rerun to finish")


class Mirror:
    def __init__(self):
        self.ids = sqlite3.connect(OUT / "mirror.sqlite")
        self.ids.executescript("""
          CREATE TABLE IF NOT EXISTS book_years(key TEXT PRIMARY KEY, year INTEGER) WITHOUT ROWID;
          CREATE TABLE IF NOT EXISTS run_vertical(first INTEGER, size INTEGER, vertical INTEGER, PRIMARY KEY(first, size)) WITHOUT ROWID;
          CREATE TABLE IF NOT EXISTS pending(run TEXT PRIMARY KEY);
        """)
        self.atlas = sqlite3.connect(OUT / "atlas.sqlite")
        self.atlas.row_factory = sqlite3.Row
        self.runs = sqlite3.connect(OUT / "atlas-runs.sqlite")
        self.runs.row_factory = sqlite3.Row
        for db in (self.atlas, self.runs):
            db.execute("CREATE TABLE IF NOT EXISTS imports(stamp TEXT PRIMARY KEY, at INTEGER NOT NULL)")
        self.runs.execute("CREATE INDEX IF NOT EXISTS run_first_all ON runs(first)")
        self.atlas.commit()
        self.runs.commit()
        self.head, self.runs_sql = [], []
        self.page_sql = defaultdict(list)
        self.touched_pages = defaultdict(dict)
        self.guarded = []

    def has(self, table, key, value):
        return self.atlas.execute(f"SELECT 1 FROM {table} WHERE {key}=?", (value,)).fetchone() is not None

    def emit(self, table, cols, values, page=None):
        (self.page_sql[page] if page else self.head).append(insert(table, cols, values))
        self.atlas.execute(f"INSERT INTO {table}({','.join(cols)}) VALUES({','.join('?' * len(cols))})", values)

    def code(self, field, key):
        if key is None or key == "":
            return None
        row = self.ids.execute("SELECT code FROM codes WHERE field=? AND key=?", (field, key)).fetchone()
        if row:
            code = row[0]
        else:
            code = (self.ids.execute("SELECT max(code) FROM codes WHERE field=?", (field,)).fetchone()[0] or 0) + 1
            self.ids.execute("INSERT INTO codes VALUES(?,?,?)", (field, key, code))
        if not self.atlas.execute("SELECT 1 FROM codes WHERE field=? AND code=?", (field, code)).fetchone():
            self.emit("codes", ("field", "code", "key"), (field, code, key))
        return code

    def book(self, key):
        key = key or "unknown"
        row = self.ids.execute("SELECT id FROM books WHERE key=?", (key,)).fetchone()
        if row:
            bid = row[0]
        else:
            bid = (self.ids.execute("SELECT max(id) FROM books").fetchone()[0] or 0) + 1
            self.ids.execute("INSERT INTO books VALUES(?,?)", (key, bid))
        if not self.has("books", "id", bid):
            self.emit("books", ("id", "key"), (bid, key))
        return bid

    def grapheme(self, text):
        text = char_text(text)
        if not text:
            return None
        row = self.atlas.execute("SELECT id FROM graphemes WHERE text=?", (text,)).fetchone()
        if row:
            return row[0]
        gid = (self.atlas.execute("SELECT max(id) FROM graphemes").fetchone()[0] or 0) + 1
        self.emit("graphemes", ("id", "text", "head"), (gid, text, None))
        return gid

    def production(self, key):
        key = key or "unknown"
        row = self.atlas.execute("SELECT id FROM productions WHERE key=?", (key,)).fetchone()
        if row:
            return row[0]
        parent = self.production(key.rsplit("/", 1)[0]) if "/" in key else None
        pid = (self.atlas.execute("SELECT max(id) FROM productions").fetchone()[0] or 0) + 1
        self.emit("productions", ("id", "key", "parent"), (pid, key, parent))
        return pid

    def page(self, key, document):
        row = self.ids.execute("SELECT id, book FROM pages WHERE key=?", (key,)).fetchone()
        if row:
            pid, bid = row
        else:
            pid = (self.ids.execute("SELECT max(id) FROM pages").fetchone()[0] or 0) + 1
            bid = self.book(document)
            self.ids.execute("INSERT INTO pages VALUES(?,?,?,0)", (key, pid, bid))
        if not self.has("pages", "id", pid):
            self.emit("pages", ("id", "key", "book", "rev", "next_slot"), (pid, key, bid, 0, 0), page=pid)
        return pid

    def known(self, text_id):
        row = self.ids.execute("SELECT id FROM ids WHERE text=?", (text_id,)).fetchone()
        return row[0] if row else None

    def allocate(self, text_id, page_key, document):
        pid = self.page(page_key, document)
        slot = self.ids.execute("SELECT next_slot FROM pages WHERE id=?", (pid,)).fetchone()[0]
        self.ids.execute("UPDATE pages SET next_slot=next_slot+1 WHERE id=?", (pid,))
        cid = pid * PAGE + slot
        self.ids.execute("INSERT INTO ids VALUES(?,?)", (text_id, cid))
        return cid

    def page_key(self, pid):
        return self.ids.execute("SELECT key FROM pages WHERE id=?", (pid,)).fetchone()[0]

    def crop(self, given, bundle, catalogue_revision, review_columns, suspect=None):
        """`given` holds the columns the catalogue carries; the rest keep their value (or DEFAULTS when
        new). review_columns: whether the catalogue speaks for the review-owned columns."""
        cid, pid = given["id"], given["id"] // PAGE
        old = self.atlas.execute("SELECT * FROM crops WHERE id=?", (cid,)).fetchone()
        row = {c: (old[c] if old else DEFAULTS.get(c)) for c in CROP_COLS}
        row.update({c: v for c, v in given.items() if v is not KEEP})
        if suspect is not None:
            row["flags"] = (row["flags"] & ~SUSPECT) | (SUSPECT if suspect else 0)
        changed = []
        if old is None:
            self.emit("crops", CROP_COLS, tuple(row[c] for c in CROP_COLS), page=pid)
        else:
            speaks = review_columns and catalogue_revision >= old["revision"]
            changed = [c for c in CROP_COLS[1:] if c not in ("revision", "shuffle") and old[c] != row[c]
                       and (c not in REVIEW_OWNED or speaks)]
            own = [c for c in changed if c not in REVIEW_OWNED]
            reviewed = [c for c in changed if c in REVIEW_OWNED]
            if own:
                self.page_sql[pid].append(f"UPDATE crops SET {','.join(f'{c}={lit(row[c])}' for c in own)} WHERE id={cid};")
            if speaks and catalogue_revision != old["revision"]:
                reviewed.append("revision")
                changed.append("revision")
                row["revision"] = catalogue_revision
            if reviewed:
                # Only while nobody has reviewed the crop since the revision the mirror holds.
                self.page_sql[pid].append(f"UPDATE crops SET {','.join(f'{c}={lit(row[c])}' for c in reviewed)} "
                                          f"WHERE id={cid} AND revision={old['revision']};")
                self.guarded.append(cid)
            if changed:
                self.atlas.execute(f"UPDATE crops SET {','.join(f'{c}=?' for c in changed)} WHERE id=?",
                                   [row[c] for c in changed] + [cid])
        # A field the catalogue does not carry keeps what the bundle had.
        bundle = {k: v for k, v in bundle.items() if v is not None}
        self.touched_pages[pid][cid % PAGE] = (bundle, old is None or bool(changed))

    def retire(self, cid, text_id, mirror_revision):
        pid = cid // PAGE
        self.page_sql[pid].append(f"DELETE FROM crops WHERE id={cid} AND revision={mirror_revision};")
        self.atlas.execute("DELETE FROM crops WHERE id=?", (cid,))
        self.guarded.append(cid)
        self.touched_pages[pid][cid % PAGE] = ({"id": text_id, "retired": True}, True)


def write_parts(run, name, groups):
    """Groups of statements in files of at most PART_BYTES (a group larger than that is a part of its
    own); a group is never split. Each file carries its own stamp and is applied in order."""
    parts, current, size = [], [], 0
    for group in groups:
        n = sum(len(s.encode()) + 1 for s in group)
        if current and size + n > PART_BYTES:
            parts.append(current)
            current, size = [], 0
        current += group
        size += n
    if current:
        parts.append(current)
    for k, part in enumerate(parts):
        stamp = f"{run.name}.{name}.{k:04}"
        (run / f"{name}-{k:04}.sql").write_text(
            "\n".join(part) + f"\nINSERT INTO imports(stamp, at) VALUES({lit(stamp)}, unixepoch());\n")
    return len(parts)


def journal(run):
    path = run / "journal"
    return path.read_text().split("\n") if path.exists() else []


def mark(run, line):
    with (run / "journal").open("a") as f:
        f.write(line + "\n")


def deliver(m, run):
    """Finish a publish whose ids are allocated. Every step can run again: bundles to R2, then each
    part to D1, then the same part to the local copy, in order; then the guarded rows are read back."""
    if "uploaded" not in journal(run):
        upload(sorted((run / "pages").iterdir()))
        mark(run, "uploaded")
    for database, name, local in (("glyph-atlas-v3", "atlas", m.atlas), ("glyph-atlas-v3-runs", "runs", m.runs)):
        for part in sorted(run.glob(f"{name}-*.sql")):
            stamp = f"{run.name}.{name}.{part.stem.rsplit('-', 1)[1]}"
            if local.execute("SELECT 1 FROM imports WHERE stamp=?", (stamp,)).fetchone():
                continue
            if not send(database, part, stamp):
                sys.exit(f"{part.name} did not apply; rerun to finish {run.name}")
            local.executescript("BEGIN;\n" + part.read_text() + "COMMIT;\n")
        if name == "atlas" and "read back" not in journal(run):
            # A guarded change D1 refused (the crop was reviewed meanwhile) leaves D1's row; take it.
            guarded = json.loads((run / "guarded.json").read_text())
            for i in range(0, len(guarded), 200):
                chunk = guarded[i:i + 200]
                found = {r["id"]: r for r in query(database, f"SELECT * FROM crops WHERE id IN ({','.join(map(str, chunk))})")}
                for cid in chunk:
                    if cid in found:
                        r = found[cid]
                        m.atlas.execute(f"INSERT OR REPLACE INTO crops({','.join(CROP_COLS)}) VALUES({','.join('?' * len(CROP_COLS))})",
                                        [r[c] for c in CROP_COLS])
                    else:
                        m.atlas.execute("DELETE FROM crops WHERE id=?", (cid,))
            m.atlas.commit()
            mark(run, "read back")
    for path in (run / "pages").iterdir():
        shutil.copy2(path, OUT / "pages" / path.name)
    m.ids.execute("DELETE FROM pending WHERE run=?", (run.name,))
    m.ids.commit()
    mark(run, "done")


def finish_unfinished(m):
    for (name,) in m.ids.execute("SELECT run FROM pending ORDER BY run").fetchall():
        print(f"finishing {name}", flush=True)
        deliver(m, OUT / "publish" / name)


def main():
    catalogue, dry = Path(sys.argv[1]), "--dry-run" in sys.argv
    (OUT / "publish").mkdir(parents=True, exist_ok=True)
    lock = open(OUT / "mirror.lock", "w")  # noqa: SIM115 -- held until the process exits
    fcntl.flock(lock, fcntl.LOCK_EX)
    m = Mirror()
    if not dry:
        finish_unfinished(m)
    elif m.ids.execute("SELECT 1 FROM pending").fetchone():
        sys.exit("an earlier publish is unfinished; run a publish (not a dry run) to finish it first")
    src = sqlite3.connect(f"file:{catalogue}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    tables = {r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    # Witness years: the catalogue's own dating rows, else the dating already published (dates reach
    # D1 apart from catalogues). A book with neither keeps its crops' years.
    years = dict(m.ids.execute("SELECT key, year FROM book_years"))
    if "document_dating" in tables:
        dated = {r["document"]: r["start"] for r in src.execute("SELECT document, start FROM document_dating WHERE axis='witness'")}
        years.update(dated)
        m.ids.executemany("INSERT OR REPLACE INTO book_years VALUES(?,?)", dated.items())
    media = {}
    if "media" in tables:
        media = {r["key"]: [r["object"], r["offset"], r["size"]] for r in src.execute("SELECT key, object, offset, size FROM media")}

    def place(data, field):
        key = (data.get(field) or "").rsplit("/", 1)[-1].split(".")[0]
        return media.get(key)

    bundles = {}

    def bundle_of(pid):
        if pid not in bundles:
            p = m.atlas.execute("SELECT rev FROM pages WHERE id=?", (pid,)).fetchone()
            if not p or p["rev"] == 0:
                bundles[pid] = {"crops": []}
            else:
                path = OUT / "pages" / f"{pid}-{p['rev']}.json.gz"
                if not path.exists():
                    sys.exit(f"page {pid} has revision {p['rev']} but no bundle {path.name}; refusing to rebuild it from nothing")
                bundles[pid] = json.loads(gzip.decompress(path.read_bytes()))
        return bundles[pid]

    def entry(cid):
        return next((e for e in bundle_of(cid // PAGE)["crops"] if e["slot"] == cid % PAGE), {})

    # Identity checks a publish without the slot matcher can make.
    columns = "id, origin, character, visual_group, production, category, state, revision, quiz, document, style, data"
    units = list(src.execute(f"SELECT {columns} FROM units WHERE origin IN ('local', 'retired') ORDER BY id")) if "units" in tables else []
    overlay = {r["id"]: r for r in src.execute(f"SELECT {columns} FROM units WHERE origin='corpus'")} if "units" in tables else {}
    retiring = {r["id"] for r in units if r["origin"] == "retired"}
    refused = []
    for r in units:
        data = json.loads(r["data"])
        page_key = data.get("page_id") or f"book:{r['document']}"
        cid = m.known(r["id"])
        if cid is not None:
            published = entry(cid)
            current = m.atlas.execute("SELECT revision FROM crops WHERE id=?", (cid,)).fetchone()
            if r["origin"] == "retired":
                if current and r["revision"] < current["revision"]:
                    refused.append(f"{r['id']}: retired at revision {r['revision']}, reviewed since (revision {current['revision']})")
            elif m.page_key(cid // PAGE) != page_key:
                refused.append(f"{r['id']}: page {m.page_key(cid // PAGE)} -> {page_key}")
            elif data.get("box") and published.get("box") and data["box"] != published["box"]:
                refused.append(f"{r['id']}: box {published['box']} -> {data['box']}")
            elif data.get("image_sha256") and published.get("image_sha256") and data["image_sha256"] != published["image_sha256"]:
                refused.append(f"{r['id']}: image changed")
            continue
        if r["origin"] == "retired":
            continue
        pid_row = m.ids.execute("SELECT id FROM pages WHERE key=?", (page_key,)).fetchone()
        if pid_row and data.get("box"):
            for e in bundle_of(pid_row[0])["crops"]:
                if not e.get("retired") and e["id"] not in retiring and iou(data["box"], e.get("box")) >= 0.5:
                    refused.append(f"{r['id']}: new box overlaps published crop {e['id']} (IoU {iou(data['box'], e['box']):.2f})")
                    break
    if refused:
        report = OUT / "publish" / f"refused-{time.strftime('%Y%m%dT%H%M%S')}.txt"
        report.write_text("\n".join(refused) + "\n")
        sys.exit(f"{len(refused)} crops need the slot matcher or are newer on the site; nothing published. See {report}")

    stamp = time.strftime("%Y%m%dT%H%M%S") + f".{time.time_ns() % 10**9:09d}-{catalogue.parent.name}-{catalogue.stem}"
    run = OUT / "publish" / stamp
    (run / "pages").mkdir(parents=True)

    n, crop_ids, retired = 0, {}, set()
    for r in units:
        data = json.loads(r["data"])
        if r["origin"] == "retired":
            cid = m.known(r["id"])
            current = cid is not None and m.atlas.execute("SELECT revision FROM crops WHERE id=?", (cid,)).fetchone()
            if current:
                m.retire(cid, r["id"], current["revision"])
                retired.add(cid)
                n += 1
            continue
        cid = m.known(r["id"]) or m.allocate(r["id"], data.get("page_id") or f"book:{r['document']}", r["document"])
        crop_ids[r["id"]] = cid
        given = {"id": cid, "kind": 0, "book": m.book(r["document"]), "char": m.grapheme(r["character"]),
                 "visual_group": m.code("visual_group", r["visual_group"]), "production": m.production(r["production"]),
                 "category": m.code("category", r["category"]), "style": m.code("style", r["style"]),
                 "state": m.code("state", r["state"]), "quiz": r["quiz"], "year": years.get(r["document"], KEEP),
                 "revision": r["revision"], "shuffle": shuffle(cid)}
        bundle = {"id": r["id"], **{k: data[k] for k in BUNDLE_KEYS if data.get(k) is not None}}
        for field in ("image", "context_image"):
            if place(data, field):
                bundle[field + "_place"] = place(data, field)
        m.crop(given, bundle, r["revision"], True, suspect=bool(data.get("suspect")))
        n += 1

    if "corpus_units" in tables:
        for r in src.execute("SELECT * FROM corpus_units ORDER BY id"):
            source = r["id"].split("|")[0].split(":")[0]
            cid = m.known(r["id"])
            if cid is None:
                # New corpus glyphs fill the newest synthetic page of their source and book, then open the next.
                prefix = f"corpus:{source}:{r['document'] or ''}:"
                last = m.ids.execute("SELECT key, next_slot FROM pages WHERE substr(key, 1, ?) = ? ORDER BY id DESC LIMIT 1",
                                     (len(prefix), prefix)).fetchone()
                key = last[0] if last and last[1] < CORPUS_PAGE else f"{prefix}{int(last[0].rsplit(':', 1)[1]) + 1 if last else 0}"
                cid = m.allocate(r["id"], key, r["document"] or source)
            crop_ids[r["id"]] = cid
            o = overlay.get(r["id"])
            # An upstream glyph speaks for the review columns only through its review overlay.
            given = {"id": cid, "kind": 1, "book": m.book(r["document"] or source),
                     "char": m.grapheme((o["character"] if o else None) or r["character"]),
                     "visual_group": m.code("visual_group", r["visual_group"]), "production": m.production(r["production"]),
                     "style": m.code("style", (o["style"] if o else None) or r["style"]),
                     "state": m.code("state", o["state"] if o else "pending"), "quiz": o["quiz"] if o else 1,
                     "year": years.get(r["document"], KEEP), "revision": o["revision"] if o else 0, "shuffle": shuffle(cid)}
            m.crop(given, {"id": r["id"], "label": r["label"], "record_place": [r["object"], r["offset"], r["size"]]},
                   o["revision"] if o else 0, o is not None)
            n += 1

    # Runs: those starting at this catalogue's crops follow it; those touching a retired crop go.
    runs_note = ""
    if crop_ids and "unit_ngrams" in tables or retired:
        want, missing = {}, 0
        if "unit_ngrams" in tables:
            for r in src.execute("SELECT * FROM unit_ngrams"):
                first = crop_ids.get(r["first"])
                if first is None:
                    continue
                members = [first, crop_ids.get(r["second"]) or m.known(r["second"])]
                if r["third"]:
                    members.append(crop_ids.get(r["third"]) or m.known(r["third"]))
                if None in members:
                    missing += 1
                    continue
                if retired & set(members):
                    continue
                key = (r["size"], r["graphemes"] or "", r["hand_order"], shuffle(first), first)
                want[key] = (members[1], members[2] if len(members) > 2 else None, m.book(r["document"]), r["vertical"])
        if missing:
            sys.exit(f"{missing} runs name crops with no id; nothing published")
        # A crop's runs are replaced only when the catalogue lists runs for it: an incomplete table
        # must not read as "this crop has no runs".
        firsts = sorted({k[4] for k in want} | retired)
        have = {}
        for i in range(0, len(firsts), 500):
            chunk = firsts[i:i + 500]
            marks = ",".join("?" * len(chunk))
            sql = f"SELECT * FROM runs WHERE first IN ({marks})"
            args = chunk
            gone = sorted(retired & set(chunk))
            if gone:
                sql += f" OR second IN ({','.join('?' * len(gone))}) OR third IN ({','.join('?' * len(gone))})"
                args = chunk + gone + gone
            for r in m.runs.execute(sql, args):
                vertical = m.ids.execute("SELECT vertical FROM run_vertical WHERE first=? AND size=?", (r["first"], r["size"])).fetchone()
                have[tuple(r[k] for k in RUN_KEY)] = (r["second"], r["third"], r["book"], vertical[0] if vertical else 0)
        delta = defaultdict(lambda: [0, 0])
        for key, value in have.items():
            if want.get(key) != value:
                m.runs_sql.append("DELETE FROM runs WHERE " + " AND ".join(f"{c}={lit(v)}" for c, v in zip(RUN_KEY, key)) + ";")
                delta[key[:2]][0] -= 1
                delta[key[:2]][1] -= value[3]
        for key, value in want.items():
            if have.get(key) != value:
                m.runs_sql.append(insert("runs", (*RUN_KEY, "second", "third", "book"), (*key, *value[:3])))
                m.ids.execute("INSERT OR REPLACE INTO run_vertical VALUES(?,?,?)", (key[4], key[0], value[3]))
                delta[key[:2]][0] += 1
                delta[key[:2]][1] += value[3]
        for (size, graphemes), (dn, dd) in delta.items():
            if dn or dd:
                m.runs_sql.append(f"INSERT INTO run_stats(size,graphemes,n,down) VALUES({size},{lit(graphemes)},{dn},{dd}) "
                                  "ON CONFLICT(size,graphemes) DO UPDATE SET n=n+excluded.n, down=down+excluded.down;")
                m.runs_sql.append(f"DELETE FROM run_stats WHERE size={size} AND graphemes={lit(graphemes)} AND n<=0;")
        runs_note = f", {len(m.runs_sql)} run statements"

    # Pages: those whose crops or facts changed get the next revision, the bundle merged onto the last.
    facts = {r["id"]: json.loads(r["data"]) for r in src.execute("SELECT id, data FROM pages")} if "pages" in tables else {}
    for key, page_facts in facts.items():
        row = m.ids.execute("SELECT id FROM pages WHERE key=?", (key,)).fetchone()
        if row and bundle_of(row[0]).get("page") != page_facts:
            m.touched_pages.setdefault(row[0], {})
    files = []
    for pid, entries in m.touched_pages.items():
        p = m.atlas.execute("SELECT * FROM pages WHERE id=?", (pid,)).fetchone()
        previous = bundle_of(pid)
        merged = {e["slot"]: e for e in previous["crops"]}
        touched = p["key"] in facts and previous.get("page") != facts[p["key"]]
        for slot, (new_entry, row_changed) in entries.items():
            new = {**merged.get(slot, {}), "slot": slot, **new_entry}
            touched = touched or row_changed or new != merged.get(slot)
            merged[slot] = new
        if not touched:
            continue
        rev = p["rev"] + 1
        body = {"key": p["key"], "book": p["book"], "rev": rev, "page": facts.get(p["key"], previous.get("page")),
                "crops": sorted(merged.values(), key=lambda e: e["slot"])}
        path = run / "pages" / f"{pid}-{rev}.json.gz"
        path.write_bytes(gzip.compress(json.dumps(body, ensure_ascii=False).encode()))
        files.append(path)
        next_slot = m.ids.execute("SELECT next_slot FROM pages WHERE id=?", (pid,)).fetchone()[0]
        # The revision moves in the same part as the page's rows, so a reader sees the old page or the new.
        m.page_sql[pid].append(f"UPDATE pages SET rev={rev}, next_slot=max(next_slot,{next_slot}) WHERE id={pid};")
        m.atlas.execute("UPDATE pages SET rev=?, next_slot=max(next_slot,?) WHERE id=?", (rev, next_slot, pid))
    stray = [pid for pid, statements in m.page_sql.items() if statements and pid not in {int(f.name.split("-")[0]) for f in files}]
    if stray:
        sys.exit(f"pages {stray[:5]} have row changes but no new bundle; nothing published")

    groups = [m.head] + [m.page_sql[pid] for pid in sorted(m.page_sql)]
    atlas_parts = write_parts(run, "atlas", [g for g in groups if g])
    runs_parts = write_parts(run, "runs", [[s] for s in m.runs_sql]) if m.runs_sql else 0
    (run / "guarded.json").write_text(json.dumps(m.guarded))
    statements = sum(len(g) for g in groups)
    print(f"{n} crops read, {statements} statements in {atlas_parts} parts{runs_note} in {runs_parts} parts, "
          f"{len(files)} pages -> {run}", flush=True)
    # The local copy takes the changes only from the part files, after D1 has taken them.
    m.atlas.rollback()
    m.runs.rollback()
    if dry:
        m.ids.rollback()
        mark(run, "dry run")
        print("dry run: nothing allocated, sent or changed")
        return
    if not atlas_parts and not runs_parts:
        m.ids.rollback()
        mark(run, "nothing to send")
        print("nothing changed")
        return
    # The allocation and the record that this publish is pending are one transaction.
    m.ids.execute("INSERT INTO pending VALUES(?)", (run.name,))
    m.ids.commit()
    mark(run, "allocated")
    deliver(m, run)
    print(f"published {stamp}")


if __name__ == "__main__":
    main()
