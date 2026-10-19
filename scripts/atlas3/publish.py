"""Publish a catalogue in today's export format into the new layout (decision record 0005).

    python3 publish.py <catalogue.sqlite> [--dry-run]

The catalogue is what export_cloudflare.py / export_cloudflare_corpus.py write: `units`, and when
present `corpus_units`, `media`, `unit_ngrams`, `pages`, `document_dating`.

- Ids come from out/mirror.sqlite. A new text id gets a page and slot once; the allocation is saved
  before anything is sent, so a slot is never handed out twice.
- Only rows that differ from the local mirror (out/atlas.sqlite, out/atlas-runs.sqlite) are sent.
  A crop's review-owned columns change only when the catalogue's revision is at least the mirror's,
  and in D1 only while the crop's revision is still the one the mirror holds.
- A known crop that moved to another page, or a new box that overlaps a published crop on its page
  (IoU >= 0.5), stops the publish: those need the slot matcher.
- Order: page bundles to R2, then the rows and page revisions to glyph-atlas-v3, then the runs to
  glyph-atlas-v3-runs. Each import counts as applied when its stamp is in that database's `imports`.
- Each publish keeps a journal in out/publish/<stamp>/state. A publish interrupted after sending is
  finished by the next run before it allocates anything.

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
BUCKET = "glyph-atlas"
BUNDLE_PREFIX = "v3/pages/"
CROP_COLS = ("id", "kind", "book", "char", "form", "cluster", "visual_group", "production", "category", "style",
             "state", "quiz", "year", "cut", "revision", "flags", "shuffle")
# Columns a review owns.
REVIEW_OWNED = ("char", "state", "style", "quiz", "kind", "flags")
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
    sys.exit(f"could not read {database}")


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
    for path in files:
        for attempt in range(4):
            if wrangler("r2", "object", "put", f"{BUCKET}/{BUNDLE_PREFIX}{path.name}", "--file", str(path),
                        "--content-type", "application/json", "--content-encoding", "gzip", "--remote",
                        capture=True).returncode == 0:
                break
            time.sleep(10 * (attempt + 1))
        else:
            sys.exit(f"bundle upload failed: {path.name}; rerun to retry")


class Mirror:
    def __init__(self):
        self.ids = sqlite3.connect(OUT / "mirror.sqlite")
        self.atlas = sqlite3.connect(OUT / "atlas.sqlite")
        self.atlas.row_factory = sqlite3.Row
        self.runs = sqlite3.connect(OUT / "atlas-runs.sqlite")
        self.runs.row_factory = sqlite3.Row
        for db in (self.atlas, self.runs):
            db.execute("CREATE TABLE IF NOT EXISTS imports(stamp TEXT PRIMARY KEY, at INTEGER NOT NULL)")
        self.runs.execute("CREATE INDEX IF NOT EXISTS run_first_all ON runs(first)")
        self.sql, self.runs_sql = [], []
        self.touched_pages = defaultdict(dict)
        self.guarded = []

    def has(self, table, key, value):
        return self.atlas.execute(f"SELECT 1 FROM {table} WHERE {key}=?", (value,)).fetchone() is not None

    def emit(self, table, cols, values):
        self.sql.append(insert(table, cols, values))
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
            self.emit("pages", ("id", "key", "book", "rev", "next_slot"), (pid, key, bid, 0, 0))
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

    def crop(self, values, bundle, catalogue_revision, review_columns):
        """review_columns: whether the catalogue speaks for the review-owned columns. A year of KEEP
        leaves the crop's year as it is."""
        row = dict(zip(CROP_COLS, values))
        changed = []
        old = self.atlas.execute("SELECT * FROM crops WHERE id=?", (row["id"],)).fetchone()
        if row["year"] is KEEP:
            row["year"] = old["year"] if old else None
        if old is None:
            self.emit("crops", CROP_COLS, tuple(row[c] for c in CROP_COLS))
        else:
            speaks = review_columns and catalogue_revision >= old["revision"]
            changed = [c for c in CROP_COLS[1:] if c not in ("revision", "shuffle") and old[c] != row[c]
                       and (c not in REVIEW_OWNED or speaks)]
            own = [c for c in changed if c not in REVIEW_OWNED]
            reviewed = [c for c in changed if c in REVIEW_OWNED]
            if own:
                self.sql.append(f"UPDATE crops SET {','.join(f'{c}={lit(row[c])}' for c in own)} WHERE id={row['id']};")
            if speaks and catalogue_revision != old["revision"]:
                reviewed.append("revision")
                changed.append("revision")
                row["revision"] = catalogue_revision
            if reviewed:
                # Only while nobody has reviewed the crop since the revision the mirror holds.
                self.sql.append(f"UPDATE crops SET {','.join(f'{c}={lit(row[c])}' for c in reviewed)} "
                                f"WHERE id={row['id']} AND revision={old['revision']};")
                self.guarded.append(row["id"])
            if changed:
                self.atlas.execute(f"UPDATE crops SET {','.join(f'{c}=?' for c in changed)} WHERE id=?",
                                   [row[c] for c in changed] + [row["id"]])
        # A field the catalogue does not carry keeps what the bundle had.
        bundle = {k: v for k, v in bundle.items() if v is not None}
        self.touched_pages[row["id"] // PAGE][row["id"] % PAGE] = (bundle, old is None or bool(changed))


def write_parts(run, name, statements):
    """Statements in files of at most PART_BYTES, each with its own stamp, applied in order."""
    parts, current, size = [], [], 0
    for statement in statements:
        n = len(statement.encode()) + 1
        if current and size + n > PART_BYTES:
            parts.append(current)
            current, size = [], 0
        current.append(statement)
        size += n
    if current:
        parts.append(current)
    for k, part in enumerate(parts):
        stamp = f"{run.name}.{name}.{k:04}"
        head = "CREATE TABLE IF NOT EXISTS imports(stamp TEXT PRIMARY KEY, at INTEGER NOT NULL);\n" if name == "runs" else ""
        (run / f"{name}-{k:04}.sql").write_text(
            head + "\n".join(part) + f"\nINSERT INTO imports(stamp, at) VALUES({lit(stamp)}, unixepoch());\n")
    return len(parts)


def journal(run):
    path = run / "journal"
    return path.read_text().split("\n") if path.exists() else []


def mark(run, line):
    with (run / "journal").open("a") as f:
        f.write(line + "\n")


def deliver(m, run):
    """Finish a publish whose ids are allocated. Every step can run again: bundles to R2, then each
    part to D1, then the same part to the local mirror, in order."""
    done = journal(run)
    if "uploaded" not in done:
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
        if name == "atlas" and (run / "guarded.json").exists() and "read back" not in done:
            # A guarded update D1 refused (the crop was reviewed meanwhile) leaves D1's values; take them.
            guarded = json.loads((run / "guarded.json").read_text())
            for i in range(0, len(guarded), 200):
                chunk = guarded[i:i + 200]
                for r in query(database, f"SELECT id,{','.join(REVIEW_OWNED)},revision FROM crops WHERE id IN ({','.join(map(str, chunk))})"):
                    m.atlas.execute(f"UPDATE crops SET {','.join(f'{c}=?' for c in (*REVIEW_OWNED, 'revision'))} WHERE id=?",
                                    [r[c] for c in (*REVIEW_OWNED, "revision")] + [r["id"]])
            m.atlas.commit()
            mark(run, "read back")
    for path in (run / "pages").iterdir():
        shutil.copy2(path, OUT / "pages" / path.name)
    mark(run, "done")


def finish_unfinished(m):
    for run in sorted(p.parent for p in (OUT / "publish").glob("*/journal")):
        done = journal(run)
        if "allocated" in done and "done" not in done:
            print(f"finishing {run.name}", flush=True)
            deliver(m, run)


def main():
    catalogue, dry = Path(sys.argv[1]), "--dry-run" in sys.argv
    lock = open(OUT / "mirror.lock", "w")  # noqa: SIM115 -- held until the process exits
    fcntl.flock(lock, fcntl.LOCK_EX)
    m = Mirror()
    if not dry:
        finish_unfinished(m)
    src = sqlite3.connect(f"file:{catalogue}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    tables = {r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    # Witness years: the catalogue's own dating rows, else the dating the first build copied (dates are
    # published to D1 apart from catalogues). A book with neither keeps its crops' years.
    years = dict(m.ids.execute("SELECT key, year FROM book_years"))
    if "document_dating" in tables:
        years.update({r["document"]: r["start"] for r in src.execute("SELECT document, start FROM document_dating WHERE axis='witness'")})
    media = {}
    if "media" in tables:
        media = {r["key"]: [r["object"], r["offset"], r["size"]] for r in src.execute("SELECT key, object, offset, size FROM media")}

    def place(data, field):
        key = (data.get(field) or "").rsplit("/", 1)[-1].split(".")[0]
        return media.get(key)

    def bundle_of(pid):
        p = m.atlas.execute("SELECT rev FROM pages WHERE id=?", (pid,)).fetchone()
        if not p or p["rev"] == 0:
            return []
        path = OUT / "pages" / f"{pid}-{p['rev']}.json.gz"
        if not path.exists():
            sys.exit(f"page {pid} has revision {p['rev']} but no bundle {path.name}; refusing to rebuild it from nothing")
        return json.loads(gzip.decompress(path.read_bytes()))["crops"]

    # Collected crops, with the identity checks a publish without the slot matcher can make.
    refused, n, crop_ids = [], 0, {}
    units = list(src.execute("SELECT * FROM units WHERE origin='local' ORDER BY id")) if "units" in tables else []
    overlay = {r["id"]: r for r in src.execute("SELECT * FROM units WHERE origin='corpus'")} if "units" in tables else {}
    published_boxes = {}
    for r in units:
        data = json.loads(r["data"])
        page_key = data.get("page_id") or f"book:{r['document']}"
        cid = m.known(r["id"])
        if cid is not None:
            if m.page_key(cid // PAGE) != page_key:
                refused.append(f"{r['id']}: page {m.page_key(cid // PAGE)} -> {page_key}")
            continue
        pid_row = m.ids.execute("SELECT id FROM pages WHERE key=?", (page_key,)).fetchone()
        if pid_row and data.get("box"):
            boxes = published_boxes.setdefault(pid_row[0], [(e["id"], e.get("box")) for e in bundle_of(pid_row[0])])
            for other, box in boxes:
                if iou(data["box"], box) >= 0.5:
                    refused.append(f"{r['id']}: new box overlaps published crop {other} (IoU {iou(data['box'], box):.2f})")
                    break
    if refused:
        report = OUT / "publish" / f"refused-{time.strftime('%Y%m%dT%H%M%S')}.txt"
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text("\n".join(refused) + "\n")
        sys.exit(f"{len(refused)} crops need the slot matcher (moved page or overlapping box); nothing published. See {report}")

    stamp = time.strftime("%Y%m%dT%H%M%S") + f".{time.time_ns() % 10**9:09d}-{catalogue.parent.name}-{catalogue.stem}"
    run = OUT / "publish" / stamp
    (run / "pages").mkdir(parents=True)

    for r in units:
        data = json.loads(r["data"])
        cid = m.known(r["id"]) or m.allocate(r["id"], data.get("page_id") or f"book:{r['document']}", r["document"])
        crop_ids[r["id"]] = cid
        # A dating row decides the year, an unknown start included; without one the year stays.
        year = years.get(r["document"], KEEP)
        values = (cid, 0, m.book(r["document"]), m.grapheme(r["character"]), None, None, m.code("visual_group", r["visual_group"]),
                  m.production(r["production"]), m.code("category", r["category"]), m.code("style", r["style"]),
                  m.code("state", r["state"]), r["quiz"], year, 0, r["revision"], 1 if data.get("suspect") else 0, shuffle(cid))
        bundle = {"id": r["id"], **{k: data[k] for k in BUNDLE_KEYS if data.get(k) is not None}}
        for field in ("image", "context_image"):
            if place(data, field):
                bundle[field + "_place"] = place(data, field)
        m.crop(values, bundle, r["revision"], True)
        n += 1

    # Retired crops leave the galleries and runs; their id and bundle entry stay, marked retired.
    retired = [r["id"] for r in src.execute("SELECT id FROM units WHERE origin='retired'")] if "units" in tables else []
    for text_id in retired:
        cid = m.known(text_id)
        if cid is None or not m.atlas.execute("SELECT 1 FROM crops WHERE id=?", (cid,)).fetchone():
            continue
        m.sql.append(f"DELETE FROM crops WHERE id={cid};")
        m.atlas.execute("DELETE FROM crops WHERE id=?", (cid,))
        m.runs_sql.append(f"DELETE FROM runs WHERE first={cid} OR second={cid} OR third={cid};")
        m.touched_pages[cid // PAGE][cid % PAGE] = ({"id": text_id, "retired": True}, True)
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
            values = (cid, 1, m.book(r["document"] or source), m.grapheme((o["character"] if o else None) or r["character"]), None, None,
                      m.code("visual_group", r["visual_group"]), m.production(r["production"]), None,
                      m.code("style", r["style"]), m.code("state", o["state"] if o else "pending"), o["quiz"] if o else 1,
                      years.get(r["document"], KEEP), 0, o["revision"] if o else 0, 0, shuffle(cid))
            # An upstream glyph speaks for the review columns only through a review overlay.
            m.crop(values, {"id": r["id"], "label": r["label"], "record_place": [r["object"], r["offset"], r["size"]]},
                   o["revision"] if o else 0, o is not None)
            n += 1

    # Runs that start at a crop of this catalogue.
    runs_note = ""
    if "unit_ngrams" in tables and crop_ids:
        want, missing = {}, 0
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
            book_key = r["document"]
            key = (r["size"], r["graphemes"] or "", r["hand_order"], shuffle(first), first)
            want[key] = (members[1], members[2] if len(members) > 2 else None, m.book(book_key), r["vertical"])
        if missing:
            sys.exit(f"{missing} runs name crops with no id; nothing published")
        firsts = sorted({k[4] for k in want} | set(crop_ids.values()))
        have = {}
        for i in range(0, len(firsts), 500):
            chunk = firsts[i:i + 500]
            for r in m.runs.execute(f"SELECT * FROM runs WHERE first IN ({','.join('?' * len(chunk))})", chunk):
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

    # Touched pages: next revision, the bundle merged onto the previous one.
    facts = {r["id"]: json.loads(r["data"]) for r in src.execute("SELECT id, data FROM pages")} if "pages" in tables else {}
    bundles = []
    for pid, entries in m.touched_pages.items():
        p = m.atlas.execute("SELECT * FROM pages WHERE id=?", (pid,)).fetchone()
        rev = p["rev"] + 1
        merged = {e["slot"]: e for e in bundle_of(pid)}
        touched = p["key"] in facts and p["rev"] == 0
        for slot, (entry, row_changed) in entries.items():
            new = {**merged.get(slot, {}), "slot": slot, **entry}
            touched = touched or row_changed or new != merged.get(slot)
            merged[slot] = new
        if not touched:
            continue
        body = {"key": p["key"], "book": p["book"], "rev": rev, "crops": sorted(merged.values(), key=lambda e: e["slot"])}
        previous = OUT / "pages" / f"{pid}-{p['rev']}.json.gz"
        if previous.exists():
            body["page"] = json.loads(gzip.decompress(previous.read_bytes())).get("page")
        if p["key"] in facts:
            body["page"] = facts[p["key"]]
        path = run / "pages" / f"{pid}-{rev}.json.gz"
        path.write_bytes(gzip.compress(json.dumps(body, ensure_ascii=False).encode()))
        bundles.append(path)
        next_slot = m.ids.execute("SELECT next_slot FROM pages WHERE id=?", (pid,)).fetchone()[0]
        m.sql.append(f"UPDATE pages SET rev={rev}, next_slot=max(next_slot,{next_slot}) WHERE id={pid};")
        m.atlas.execute("UPDATE pages SET rev=?, next_slot=max(next_slot,?) WHERE id=?", (rev, next_slot, pid))

    atlas_parts = write_parts(run, "atlas", m.sql)
    runs_parts = write_parts(run, "runs", m.runs_sql) if m.runs_sql else 0
    (run / "guarded.json").write_text(json.dumps(m.guarded))
    print(f"{n} crops read, {len(m.sql)} statements in {atlas_parts} parts{runs_note} in {runs_parts} parts, "
          f"{len(bundles)} pages -> {run}", flush=True)
    # The local mirror takes the changes only from the part files, after D1 has taken them.
    m.atlas.rollback()
    m.runs.rollback()
    if dry:
        m.ids.rollback()
        mark(run, "dry run")
        print("dry run: nothing allocated, sent or changed")
        return
    m.ids.commit()
    mark(run, "allocated")
    deliver(m, run)
    print(f"published {stamp}")


if __name__ == "__main__":
    main()
