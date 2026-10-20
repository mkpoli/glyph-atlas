"""Publish a catalogue in today's export format into the new layout (decision record 0005).

    python3 publish.py <catalogue.sqlite> [--dry-run]
    python3 publish.py [<catalogue.sqlite>] --replacements [--dry-run]

The catalogue is what export_cloudflare.py / export_cloudflare_corpus.py write: `units`, and when
present `corpus_units`, `media`, `unit_ngrams`, `pages`, `document_dating`, `unit_redirects`. A `media` table also adds its
images to the image key map (`media_map.py`).

- Ids come from out/mirror.sqlite. A new text id gets a page and slot once; the allocation and a
  record of the pending publish are saved in one transaction before anything is sent.
- Only what differs from the local copy (out/atlas.sqlite, out/atlas-runs.sqlite) is sent, and a
  field the catalogue does not carry keeps its published value. Review-owned columns change only
  when the catalogue's revision is at least the published one, and in D1 only while the crop's
  revision is unchanged; the outcome is read back.
- A known crop with another box or image on its own page is re-cut: same id, its cut moves on.
- A publish that would move a known crop to another page, or add or re-cut a box overlapping another
  crop on its page (IoU >= 0.5), is refused with a report: those need the slot matcher. A box a
  reviewer redrew on the site (`box_pending`) names its crop by itself and is published as it is.
- Order: page bundles to R2, then the rows to glyph-atlas-v3 in parts that never split a page (its
  crops and its revision go together), then the runs to glyph-atlas-v3-runs. Each part counts as
  applied when its stamp is in that database's `imports`, and is then replayed on the local copy.
- A publish stopped after allocating is finished by the next run before anything else.
- A crop the catalogue carries with `origin='withdrawn'` (a document taken off the site,
  `withdraw.py`; such a catalogue holds nothing else) is first deleted in glyph-atlas-v3 under its
  revision guard and read back. Only a crop D1 no longer holds is retired, its bundle entry keeping
  only its id and cut, its images leaving the image key map unless another entry names them, and a
  retired entry naming it as its replacement names the next live crop on its redirect chain. A crop
  reviewed on the site since stays as it is, and the publish ends by listing it as refused.
- A catalogue's `corpus_flags` (id, character, finding, realigned, transcribed), as `flags.py` writes
  it, sends each named corpus glyph to the Flagged view as the Worker's admin flag does: its finding
  in `corpus_flags`, its first row in `corpus_rows`, and a `flag` event under a submission of the
  actor `publish`, which moves its state to `flagged` one revision on, its review overlay and tally. A glyph anything
  was written about already (a `corpus_rows` row), a collected crop, one whose character is no longer
  the one the finding was made against, or one this site may not show is left as it is; in D1 each
  flag waits on the crop's revision and on no `corpus_rows` row, and the outcome is read back.
- A catalogue whose `units` carry `visual` and `context` sets the published crops' model votes for
  their cut in the page objects `v3/votes/<build>/<page>.json.gz` (votes.py), uploaded once the D1
  parts naming their model codes have landed; a reading that is not ready, or a retirement, removes the
  crop's entry.
- A statement over STATEMENT_BYTES stops the publish before anything is allocated.
- A publish that sent something ends by publishing the answers (`answers.py`), so the counts follow it.

With `--replacements` the publish also reads `source-unit_redirects.sqlite` (the catalogue's own
`unit_redirects` win) and sets `replaced_by` on every retired bundle entry whose value differs from the
first live crop on its chain, catalogued or not; without a catalogue it does only that.

One publish runs at a time (a lock on out/mirror.lock).
"""
import argparse
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
from datetime import UTC, datetime
from pathlib import Path

import media_map
import votes
from build import CORPUS_PAGE, PAGE, WRITTEN, bundle_entry, char_text, horizontal, replacement, shuffle
from pull import real
from run_answers import PREFIX as ANSWER_PREFIX
from run_answers import RUN_TEXT, Answers, pending, uploaded

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
             "state", "quiz", "year", "cut", "revision", "flags", "priority", "family", "shuffle")
# Columns a review owns.
REVIEW_OWNED = ("char", "state", "style", "quiz", "kind", "flags")
# What a crop takes when it is new and the catalogue does not say.
DEFAULTS = {"form": None, "cluster": None, "category": None, "year": None, "cut": 0, "flags": 0, "priority": 1, "family": None}
SUSPECT = 1
# The mark states, the pending state and the event kind of a re-cut that clears a mark, as migrations
# 0003 and 0009 and the first build number them. The publication's own events name actor and
# submission 0.
MARK_STATES, PENDING, RECUT_KIND = (3, 4), 0, 7
# What a bundle entry keeps of each earlier cut: enough to show and cite its pixels.
CUT_FIELDS = ("box", "crop_box", "context_box", "image", "image_sha256", "image_size", "image_place",
              "context_image", "context_image_place")


def merged_cuts(*lists):
    """Earlier cuts by number, a later list's entry replacing an earlier one's, in cut order."""
    by = {c["cut"]: c for cuts in lists for c in cuts or []}
    return [by[k] for k in sorted(by)]


RUN_KEY = ("size", "graphemes", "hand", "shuffle", "first")
# D1 takes an imported file whole; parts stay under the size the publication scripts use.
PART_BYTES = 40 * 1024 ** 2
# D1 refuses a statement over 100 KB.
STATEMENT_BYTES = 90_000
KEEP = object()
# The actor a publish writes its flags under, and the glyphs one submission names (FLAG_MAX in the Worker).
PUBLISHER, FLAG_MAX = "publish", 96
# A crop facts load (crop_facts.py) names its run in `pending` with this prefix.
FACTS_RUN = "crop-facts/"
# A withdrawn crop's bundle entry: retired, with no record.
WITHDRAWN = {"withdrawn": True}
GONE = ("retired", "withdrawn")


def image_keys(entry):
    """The image keys a bundle entry names, its earlier cuts' among them."""
    return {key for e in (entry, *entry.get("cuts", ())) for field in ("image", "context_image")
            if (key := (e.get(field) or "").rsplit("/", 1)[-1].removesuffix(".webp"))}


def lit(v):
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return str(int(v))
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return real(v)
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


def upload(files, prefix=BUNDLE_PREFIX):
    """Files to R2 under a prefix, through rclone's S3 interface. A page bundle's name carries its
    revision and a publication's files sit under its own `v3/pub/<pub>/`, so resending them writes the
    same bytes again. The image key shards (`v3/media/`), the alias shards (`v3/aliases/`) and
    `v3/pointer.json` have fixed names: a resend replaces the object with the local file."""
    if not files:
        return
    folder = files[0].parent
    names = "\n".join(f.name for f in files) + "\n"
    for attempt in range(4):
        done = subprocess.run(["rclone", "copy", str(folder), f"{R2_REMOTE}:{BUCKET}/{prefix}", "--files-from-raw", "-",
                               "--transfers", "16", "--s3-no-check-bucket", "--header-upload", "Content-Type: application/json",
                               "--header-upload", "Content-Encoding: gzip"], input=names, text=True, check=False)
        listed = subprocess.run(["rclone", "lsf", f"{R2_REMOTE}:{BUCKET}/{prefix}", "--files-from-raw", "-"],
                                input=names, text=True, capture_output=True, check=False)
        if done.returncode == 0 and set(listed.stdout.split()) >= {f.name for f in files}:
            return
        time.sleep(10 * (attempt + 1))
    sys.exit(f"upload to {prefix} failed; rerun to finish")


# Whether a form decision holds crop ?: its own glyph decision, else the form or issue of the cluster
# decision it carries from an earlier clustering or of its cluster. A corpus glyph's base is kept only
# while one does; history.py and check.py leave out the bases it releases.
ACTIVE_DECISION = ("EXISTS(SELECT 1 FROM crops c LEFT JOIN form_glyphs g ON g.crop=c.id "
                   "LEFT JOIN form_clusters k ON k.id=c.cluster WHERE c.id=? AND CASE WHEN g.crop IS NOT NULL "
                   "THEN g.form IS NOT NULL OR g.issue IS NOT NULL "
                   "OR EXISTS(SELECT 1 FROM form_decisions d WHERE d.id=g.decision AND d.kind='glyph') "
                   "ELSE k.form IS NOT NULL OR (k.issue IS NOT NULL AND k.issue<>'mixed') END)")


def releases_base(atlas, overlay):
    """Whether a publish releases a corpus glyph's base: no form decision holds the glyph and no review
    overlay (a `units` row of origin 'corpus', text ids in `overlay`) speaks for it. Before the crop facts
    load has put the decisions in the local copy, every base is kept."""
    tables = {r[0] for r in atlas.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {"form_glyphs", "form_clusters"} <= tables or \
            "cluster" not in {r[1] for r in atlas.execute("PRAGMA table_info(crops)")}:
        return lambda cid, text: False

    def released(cid, text):
        if text in overlay:
            return False
        row = atlas.execute("SELECT kind FROM crops WHERE id=?", (cid,)).fetchone()
        return row is not None and row[0] == 1 and not atlas.execute(f"SELECT {ACTIVE_DECISION}", (cid,)).fetchone()[0]
    return released


class Mirror:
    def __init__(self):
        self.ids = sqlite3.connect(OUT / "mirror.sqlite")
        self.ids.executescript("""
          CREATE TABLE IF NOT EXISTS book_years(key TEXT PRIMARY KEY, year INTEGER) WITHOUT ROWID;
          CREATE TABLE IF NOT EXISTS pending(run TEXT PRIMARY KEY);
          CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
        """)
        self.atlas = sqlite3.connect(OUT / "atlas.sqlite")
        self.atlas.row_factory = sqlite3.Row
        self.runs = sqlite3.connect(OUT / "atlas-runs.sqlite")
        self.runs.row_factory = sqlite3.Row
        for db in (self.atlas, self.runs):
            db.execute("CREATE TABLE IF NOT EXISTS imports(stamp TEXT PRIMARY KEY, at INTEGER NOT NULL)")
        self.runs.execute("CREATE INDEX IF NOT EXISTS run_first_all ON runs(first)")
        # The runs' answers are written again for the books and crops a publish changes (run_answers.py).
        for name, cols in (("run_book_all", "book, size, graphemes"), ("run_second_all", "second"), ("run_third_all", "third")):
            self.runs.execute(f"CREATE INDEX IF NOT EXISTS {name} ON runs({cols})")
        self.atlas.commit()
        self.runs.commit()
        self.head, self.runs_sql = [], []
        # Each page's vote entries by slot, read from out/votes/ when first touched, and the pages changed.
        self.votes, self.voted = {}, set()
        self.page_sql = defaultdict(list)
        self.touched_pages = defaultdict(dict)
        self.guarded, self.stale, self.relabelled, self.runs_seen = [], [], [], set()

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
        page_changed = False
        if old is None:
            self.emit("crops", CROP_COLS, tuple(row[c] for c in CROP_COLS), page=pid)
        else:
            speaks = review_columns and catalogue_revision >= old["revision"]
            if review_columns and not speaks:
                self.stale.append(cid)
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
            if old["kind"] == 1 and given.get("kind") == 1 and not review_columns and "char" in given:
                # A followed glyph retains its character; the incoming character becomes the base
                # restored when its decision is withdrawn. Stage exactly what the remote guards allow,
                # since the run projection below reads these local crops.
                upstream = row["char"]
                base = self.atlas.execute("SELECT char FROM form_bases WHERE crop=?", (cid,)).fetchone()
                # The legacy drain retained bases after withdrawal. Once no decision is active, a
                # publication supplies the new upstream character, so that old base can be released.
                active = ACTIVE_DECISION
                if base is not None and not self.atlas.execute(f"SELECT {active}", (cid,)).fetchone()[0]:
                    self.page_sql[pid].append(f"DELETE FROM form_bases WHERE crop={cid} AND NOT {active.replace('?', str(cid))};")
                    self.atlas.execute("DELETE FROM form_bases WHERE crop=?", (cid,))
                    base = None
                    page_changed = True
                base_changed = base is not None and base[0] != upstream
                crop_changed = base is None and old["revision"] == 0 and old["char"] != upstream
                if base_changed or crop_changed:
                    self.page_sql[pid].append(f"UPDATE form_bases SET char={lit(upstream)} WHERE crop={cid} AND char IS NOT {lit(upstream)};")
                    self.atlas.execute("UPDATE form_bases SET char=? WHERE crop=?", (upstream, cid))
                    self.page_sql[pid].append(f"UPDATE crops SET char={lit(upstream)} WHERE id={cid} AND revision=0 "
                                              f"AND NOT EXISTS (SELECT 1 FROM form_bases WHERE crop={cid});")
                    # Conditional row writes travel with the page even when only its base changed.
                    page_changed = True
                if crop_changed:
                    changed.append("char")
                else:
                    row["char"] = old["char"]
            if "char" in changed:
                self.relabelled.append(cid)
            if changed:
                self.atlas.execute(f"UPDATE crops SET {','.join(f'{c}=?' for c in changed)} WHERE id=?",
                                   [row[c] for c in changed] + [cid])
        bundle = {k: v for k, v in bundle.items() if v is not None}
        self.touched_pages[pid][cid % PAGE] = (bundle, old is None or bool(changed) or page_changed)

    def unmark(self, cid):
        """A crop re-cut while marked seen or hard: rounds showed or skipped its old box, so at the new
        cut it is pending again, one revision on, with an event recording it, as the old site judged
        marks against the current box. Only while it still holds a mark, so a review since stands."""
        pid, held = cid // PAGE, f"state IN ({','.join(map(str, MARK_STATES))})"
        self.page_sql[pid] += [
            (f"INSERT INTO events(crop, revision, cut, gen, actor, submission, at, kind, label, state) SELECT id, revision + 1, cut, "
             f"(SELECT gen FROM generation WHERE id = 0), 0, 0, {round(time.time() * 1000)}, {RECUT_KIND}, char, {PENDING} "
             f"FROM crops WHERE id = {cid} AND {held};"),
            f"UPDATE reviews SET state = {PENDING} WHERE crop = {cid} AND {held};",
            f"UPDATE crops SET state = {PENDING}, revision = revision + 1 WHERE id = {cid} AND {held};"]
        self.guarded.append(cid)

    def vote_slots(self, pid):
        if pid not in self.votes:
            self.votes[pid] = votes.read(OUT / "votes", pid)
        return self.votes[pid]

    def vote(self, cid, cut, visual, context):
        """The crop's votes for its cut from the catalogue's readings; its page object changes only when
        they differ."""
        key, stored = votes.visual(visual)
        new = votes.entry(cut, self.code("models", key), stored, votes.context(context))
        slots, slot = self.vote_slots(cid // PAGE), cid % PAGE
        if slots.get(slot) == new:
            return
        if new is None:
            del slots[slot]
        else:
            slots[slot] = new
        self.voted.add(cid // PAGE)

    def retire(self, cid, text_id, mirror_revision, withdrawn=False):
        pid = cid // PAGE
        # The entry keeps the crop's cut, so a crop put back continues from it.
        cut = self.atlas.execute("SELECT cut FROM crops WHERE id=?", (cid,)).fetchone()["cut"]
        self.page_sql[pid].append(f"DELETE FROM crops WHERE id={cid} AND revision={mirror_revision};")
        if self.vote_slots(pid).pop(cid % PAGE, None) is not None:
            self.voted.add(pid)
        self.atlas.execute("DELETE FROM crops WHERE id=?", (cid,))
        self.guarded.append(cid)
        self.touched_pages[pid][cid % PAGE] = ({"id": text_id, "retired": True, "cut": cut, **(WITHDRAWN if withdrawn else {})}, True)


def fits(groups):
    """Stop when a statement is over D1's limit; run before anything is allocated or committed."""
    for group in groups:
        if long := [s for s in group if len(s.encode()) > STATEMENT_BYTES]:
            sys.exit(f"a statement of {len(long[0].encode())} bytes is over D1's limit, nothing sent: {long[0][:200]}")


def write_parts(run, name, groups):
    """Groups of statements in files of at most PART_BYTES (a group larger than that is a part of its
    own); a group is never split. Each file carries its own stamp and is applied in order."""
    fits(groups)
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
    # Image places first, so no crop is listed before its image can be served. The local shards change
    # only here, in a run recorded as pending, so a run that stopped before leaves none for the next.
    if "media merged" not in journal(run):
        if (run / "media.json").exists():
            media_map.merge(json.loads((run / "media.json").read_text()))
        if (run / "unmapped.json").exists():
            media_map.remove(json.loads((run / "unmapped.json").read_text()))
        mark(run, "media merged")
    media_map.send(upload)
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
            guarded, refused = json.loads((run / "guarded.json").read_text()), []
            for i in range(0, len(guarded), 200):
                chunk = guarded[i:i + 200]
                found = {r["id"]: r for r in query(database, f"SELECT * FROM crops WHERE id IN ({','.join(map(str, chunk))})")}
                for cid in chunk:
                    local = m.atlas.execute("SELECT revision FROM crops WHERE id=?", (cid,)).fetchone()
                    if (cid in found) != (local is not None) or (local and found[cid]["revision"] != local["revision"]):
                        refused.append(cid)
                    if cid in found:
                        r = found[cid]
                        m.atlas.execute(f"INSERT OR REPLACE INTO crops({','.join(CROP_COLS)}) VALUES({','.join('?' * len(CROP_COLS))})",
                                        [r[c] for c in CROP_COLS])
                    else:
                        m.atlas.execute("DELETE FROM crops WHERE id=?", (cid,))
            # A flagged glyph's corpus row, finding, overlay and events as D1 holds them: a flag D1 refused
            # (a round or a review wrote about the glyph first) leaves D1's rows, and its tally cells move back.
            sent = json.loads((run / "flags.json").read_text()) if (run / "flags.json").exists() else {}
            flagged = sorted(m.known(i) for i in sent.get("flagged", []))
            for i in range(0, len(flagged), 200):
                chunk = ",".join(map(str, flagged[i:i + 200]))
                for table in ("corpus_rows", "corpus_flags", "reviews", "events"):
                    found = query(database, f"SELECT * FROM {table} WHERE crop IN ({chunk})")
                    m.atlas.execute(f"DELETE FROM {table} WHERE crop IN ({chunk})")
                    cols = [c[1] for c in m.atlas.execute(f"PRAGMA table_info({table})")]
                    m.atlas.executemany(f"INSERT INTO {table}({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
                                        [[r.get(c) for c in cols] for r in found])
                    if table == "corpus_flags":
                        mine = {r["crop"] for r in found if r["at"] == sent["at"]}
                        for cid in flagged[i:i + 200]:
                            if cid in mine:
                                continue
                            if cid not in refused:
                                refused.append(cid)
                            old = sent["cells"][str(cid)]
                            for state, step in ((old[5], 1), (sent["state"], -1)):
                                m.atlas.execute("INSERT INTO tally(gen,kind,char,book,production,style,state,quiz,n) "
                                                "SELECT gen,?,?,?,?,?,?,?,? FROM generation WHERE id=0 ON CONFLICT DO UPDATE SET n=n+excluded.n",
                                                [*old[:5], state, old[6], step])
            m.atlas.commit()
            (run / "refused-by-d1.json").write_text(json.dumps(refused))
            if refused:
                print(f"{len(refused)} changes kept D1's newer review instead (ids in {run.name}/refused-by-d1.json)", flush=True)
            mark(run, "read back")
    # The runs' answers, once both databases hold what they count: the books and shards the publish
    # changed, and Explore's grids, written again from the local copies; those whose bytes R2 lacks go up.
    if "run answers" not in journal(run):
        if (run / "run-answers.json").exists() and m.runs.execute("SELECT 1 FROM sqlite_master WHERE name='run_written'").fetchone():
            touched = json.loads((run / "run-answers.json").read_text())
            # The runs' texts, which the local copy alone holds, then the answers they count.
            m.runs.execute(RUN_TEXT)
            m.runs.executemany("DELETE FROM run_text WHERE first=? AND size=?", [t[:2] for t in touched["texts"] if t[2] is None])
            m.runs.executemany("INSERT OR REPLACE INTO run_text VALUES(?,?,?)", [t for t in touched["texts"] if t[2] is not None])
            m.runs.commit()
            if touched["books"] or touched["shards"]:
                written = Answers()
                written.books(touched["books"])
                written.shards({tuple(k) for k in touched["shards"]})
                written.grids()
                written.close()
        for folder, paths in sorted(pending().items()):
            upload(paths, ANSWER_PREFIX + (folder + "/" if folder else ""))
            uploaded(paths)
        mark(run, "run answers")
    # The vote objects go once D1 holds the model codes they name; out/votes/ takes each as it goes up.
    voted = sorted((run / "votes").glob("*.json.gz")) if (run / "votes").exists() else []
    if voted and "votes uploaded" not in journal(run):
        upload(voted, votes.prefix(m.build))
        (OUT / "votes").mkdir(exist_ok=True)
        for path in voted:
            shutil.copy2(path, OUT / "votes" / path.name)
        mark(run, "votes uploaded")
    for path in (run / "pages").iterdir():
        shutil.copy2(path, OUT / "pages" / path.name)
        page, rev = path.name.split(".")[0].split("-")
        for older in (OUT / "pages").glob(f"{page}-*.json.gz"):
            if int(older.name.split(".")[0].split("-")[1]) < int(rev):
                older.unlink()
    m.ids.execute("DELETE FROM pending WHERE run=?", (run.name,))
    m.ids.commit()
    mark(run, "done")


def delete_withdrawn(m, run, crops, dry):
    """Delete withdrawn crops, (id, catalogue revision) pairs, in glyph-atlas-v3 while their revision is
    the catalogue's, and return the ids D1 still holds: those were reviewed since. Nothing local changes
    here, so a publish that stops later and runs again deletes nothing more and finds the same ids. A
    dry run sends nothing and returns none."""
    if dry or not crops:
        return []
    stamp, path = f"{run.name}.withdraw", run / "withdraw.sql"
    path.write_text("".join(f"DELETE FROM crops WHERE id={cid} AND revision={rev};\n" for cid, rev in crops)
                    + f"INSERT INTO imports(stamp, at) VALUES({lit(stamp)}, unixepoch());\n")
    if not send("glyph-atlas-v3", path, stamp):
        sys.exit(f"{run.name}/{path.name} did not apply; nothing else was sent. Publish the withdrawal again")
    kept, ids = [], [cid for cid, _ in crops]
    for i in range(0, len(ids), 200):
        kept += [r["id"] for r in query("glyph-atlas-v3", f"SELECT id FROM crops WHERE id IN ({','.join(map(str, ids[i:i + 200]))})")]
    return kept


def refuse_withdrawals(run):
    """End the publish with the withdrawn crops D1 kept, if any."""
    path = run / "refused-withdrawals.txt"
    if path.exists():
        refused = path.read_text().split()
        sys.exit(f"{len(refused)} withdrawn crops were reviewed on the site since and stay live with their records and "
                 f"images: {', '.join(refused[:10])} (all in {run.name}/refused-withdrawals.txt). Write the withdrawal "
                 "again with withdraw.py to take them off at their new revision")


def published_bundles(m, cached):
    """Every page's published bundle as (page id, body), read one at a time unless already read."""
    for pid, rev in m.atlas.execute("SELECT id, rev FROM pages WHERE rev > 0 ORDER BY id").fetchall():
        if pid in cached:
            yield pid, cached[pid]
            continue
        path = OUT / "pages" / f"{pid}-{rev}.json.gz"
        if not path.exists():
            sys.exit(f"page {pid} has revision {rev} but no bundle {path.name}; nothing published")
        yield pid, json.loads(gzip.decompress(path.read_bytes()))


def finish_unfinished(m):
    for (name,) in m.ids.execute("SELECT run FROM pending ORDER BY run").fetchall():
        print(f"finishing {name}", flush=True)
        deliver(m, OUT / "publish" / name)
        if (OUT / "publish" / name / "refused-withdrawals.txt").exists():
            print(f"WARNING: {name} left withdrawn crops live that were reviewed on the site since; "
                  f"see {name}/refused-withdrawals.txt", file=sys.stderr, flush=True)


def main():
    parser = argparse.ArgumentParser(description="Publish a catalogue into the new layout.")
    parser.add_argument("catalogue", type=Path, nargs="?")
    parser.add_argument("--dry-run", action="store_true", help="write the parts and bundles, publish nothing")
    parser.add_argument("--replacements", action="store_true",
                        help="name the replacement of every retired crop from source-unit_redirects.sqlite")
    args = parser.parse_args()
    if args.catalogue is None and not args.replacements:
        parser.error("a catalogue, --replacements, or both")
    catalogue, dry = args.catalogue, args.dry_run
    (OUT / "publish").mkdir(parents=True, exist_ok=True)
    lock = open(OUT / "mirror.lock", "w")  # noqa: SIM115 -- held until the process exits
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("another publish holds out/mirror.lock; waiting for it", flush=True)
        fcntl.flock(lock, fcntl.LOCK_EX)
    m = Mirror()
    # The id allocation belongs to one build of the databases; a mirror from another build would hand
    # live ids to other crops.
    build_id = m.ids.execute("SELECT value FROM meta WHERE key='build'").fetchone()
    if not build_id:
        sys.exit("out/mirror.sqlite names no build; it is not the mirror of glyph-atlas-v3")
    m.build = build_id[0]
    if not dry and not applied("glyph-atlas-v3", f"build:{build_id[0]}"):
        sys.exit(f"glyph-atlas-v3 was not loaded from build {build_id[0]}; this mirror does not belong to it")
    # A crop facts load writes the same tables and objects; it finishes first.
    if m.ids.execute("SELECT 1 FROM pending WHERE run LIKE 'fill/%'").fetchone():
        sys.exit("the crop fill is unfinished; finish it with fill.py --send first")
    for (name,) in m.ids.execute("SELECT run FROM pending WHERE run LIKE ?", (FACTS_RUN + "%",)):
        sys.exit(f"the crop facts load {name.removeprefix(FACTS_RUN)} is unfinished; finish it with crop_facts.py --send first")
    if not dry:
        finish_unfinished(m)
    elif m.ids.execute("SELECT 1 FROM pending").fetchone():
        sys.exit("an earlier publish is unfinished; run a publish (not a dry run) to finish it first")
    # The cutover fills these columns once, before publications resume.
    missing = {"family", "priority"} - {r[1] for r in m.atlas.execute("PRAGMA table_info(crops)")}
    if missing:
        sys.exit(f"the local copy lacks crops columns {sorted(missing)}; run fill.py --send first")
    src = sqlite3.connect(f"file:{catalogue}?mode=ro", uri=True) if catalogue else sqlite3.connect(":memory:")
    src.row_factory = sqlite3.Row
    tables = {r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    # Witness years: the catalogue's own dating rows, else the dating already published (dates reach
    # D1 apart from catalogues). A book with neither keeps its crops' years.
    years = dict(m.ids.execute("SELECT key, year FROM book_years"))
    if "document_dating" in tables:
        dated = {r["document"]: r["start"] for r in src.execute("SELECT document, start FROM document_dating WHERE axis='witness'")}
        years.update(dated)
        m.ids.executemany("INSERT OR REPLACE INTO book_years VALUES(?,?)", dated.items())
    media, fresh_media = {}, []
    if "media" in tables:
        media = {r["key"]: [r["object"], r["offset"], r["size"]] for r in src.execute("SELECT key, object, offset, size FROM media")}
        # Checked against the map now, merged into the local shards once the run is pending (`deliver`).
        fresh_media = media_map.grow(src.execute("SELECT key, object, offset, size, content_type FROM media"))[1]

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
            bundles[pid]["by_slot"] = {e["slot"]: e for e in bundles[pid]["crops"]}
        return bundles[pid]

    def entry(cid):
        return bundle_of(cid // PAGE)["by_slot"].get(cid % PAGE, {})

    # Identity checks a publish without the slot matcher can make.
    columns = "id, origin, character, visual_group, production, category, state, revision, quiz, document, style, data"
    unit_columns = {r[1] for r in src.execute("PRAGMA table_info(units)")} if "units" in tables else set()
    # A catalogue carries each crop's review priority; one that does not keeps the published one.
    with_priority = "priority" in unit_columns
    if with_priority:
        columns += ", priority"
    with_family = "family" in unit_columns
    if with_family:
        columns += ", family"
    with_votes = {"visual", "context"} <= unit_columns
    if with_votes:
        columns += ", visual, context"
    units = list(src.execute(f"SELECT {columns} FROM units WHERE origin IN ('local', 'retired', 'withdrawn') ORDER BY id")) if "units" in tables else []
    overlay = {r["id"]: r for r in src.execute(f"SELECT {columns} FROM units WHERE origin='corpus'")} if "units" in tables else {}
    retiring = {r["id"] for r in units if r["origin"] in GONE}
    withdrawing = [r for r in units if r["origin"] == "withdrawn"]
    if withdrawing and (len(withdrawing) < len(units) or "corpus_units" in tables or args.replacements):
        sys.exit("a withdrawal catalogue (withdraw.py) holds withdrawn units alone and is published without "
                 "--replacements; nothing published")
    # A live crop with another box or image on its own page is re-cut: it keeps its id and its cut
    # moves on, so claims and reviews made on the old cut stay with it.
    recut = set()
    for r in units:
        # A live crop, or a retired one put back, against the entry it had.
        if r["origin"] == "local" and (cid := m.known(r["id"])) is not None and (
                m.atlas.execute("SELECT 1 FROM crops WHERE id=?", (cid,)).fetchone() or entry(cid).get("retired")):
            data, published = json.loads(r["data"]), entry(cid)
            if (data.get("box") and published.get("box") and data["box"] != published["box"]) or \
                    (data.get("image_sha256") and published.get("image_sha256") and data["image_sha256"] != published["image_sha256"]):
                recut.add(r["id"])
    refused = []
    for r in units:
        data = json.loads(r["data"])
        page_key = data.get("page_id") or f"book:{r['document']}"
        cid = m.known(r["id"])
        if cid is not None:
            published = entry(cid)
            current = m.atlas.execute("SELECT revision FROM crops WHERE id=?", (cid,)).fetchone()
            if r["origin"] == "withdrawn":
                # The read-back after the D1 deletion decides each withdrawn crop on its own.
                pass
            elif r["origin"] in GONE:
                if current and r["revision"] < current["revision"]:
                    refused.append(f"{r['id']}: {r['origin']} at revision {r['revision']}, reviewed since (revision {current['revision']})")
            elif m.page_key(cid // PAGE) != page_key:
                refused.append(f"{r['id']}: page {m.page_key(cid // PAGE)} -> {page_key}")
            elif r["id"] in recut and data.get("box") and not data.get("box_pending"):
                # Against every other crop's published box, re-cut ones too, so two crops cannot trade glyphs.
                # A box a reviewer redrew on the site (`box_pending`) is the crop's own, whatever it covers.
                for e in bundle_of(cid // PAGE)["crops"]:
                    if e["id"] != r["id"] and not e.get("retired") and e["id"] not in retiring \
                            and iou(data["box"], e.get("box")) >= 0.5:
                        refused.append(f"{r['id']}: re-cut box overlaps published crop {e['id']} (IoU {iou(data['box'], e['box']):.2f})")
                        break
            continue
        if r["origin"] in GONE:
            continue
        pid_row = m.ids.execute("SELECT id FROM pages WHERE key=?", (page_key,)).fetchone()
        if pid_row and data.get("box"):
            for e in bundle_of(pid_row[0])["crops"]:
                if not e.get("retired") and e["id"] not in retiring | recut and iou(data["box"], e.get("box")) >= 0.5:
                    refused.append(f"{r['id']}: new box overlaps published crop {e['id']} (IoU {iou(data['box'], e['box']):.2f})")
                    break
    # New and re-cut boxes against each other, page by page.
    fresh = defaultdict(list)
    for r in units:
        if r["origin"] == "local" and (m.known(r["id"]) is None or r["id"] in recut):
            data = json.loads(r["data"])
            if data.get("box"):
                fresh[data.get("page_id") or f"book:{r['document']}"].append((r["id"], data["box"]))
    for boxes in fresh.values():
        for i, (a, box_a) in enumerate(boxes):
            for b, box_b in boxes[i + 1:]:
                if iou(box_a, box_b) >= 0.5:
                    refused.append(f"{a} and {b}: two new or re-cut boxes on one glyph (IoU {iou(box_a, box_b):.2f})")
    if refused:
        report = OUT / "publish" / f"refused-{time.strftime('%Y%m%dT%H%M%S')}.txt"
        report.write_text("\n".join(refused) + "\n")
        sys.exit(f"{len(refused)} crops need the slot matcher or are newer on the site; nothing published. See {report}")

    stamp = time.strftime("%Y%m%dT%H%M%S") + f".{time.time_ns() % 10**9:09d}-" + (
        f"{catalogue.parent.name}-{catalogue.stem}" if catalogue else "replacements")
    run = OUT / "publish" / stamp
    (run / "pages").mkdir(parents=True)

    # A withdrawal deletes its live crops in D1 first and goes on only with those D1 no longer holds: a
    # crop reviewed on the site since keeps its row, its record and its images, and the publish ends
    # by refusing it.
    kept_live = set()
    if withdrawing:
        live_now = [(cid, r["revision"]) for r in withdrawing if (cid := m.known(r["id"])) is not None
                    and m.atlas.execute("SELECT 1 FROM crops WHERE id=?", (cid,)).fetchone()]
        # Every bundle is read below; a missing one stops the publish before D1 is touched.
        for pid, rev in m.atlas.execute("SELECT id, rev FROM pages WHERE rev > 0").fetchall():
            if not (OUT / "pages" / f"{pid}-{rev}.json.gz").exists():
                sys.exit(f"page {pid} has revision {rev} but no bundle {pid}-{rev}.json.gz; nothing published")
        if dry:
            print(f"dry run: {len(live_now)} withdrawn crops would be deleted in D1 and read back before anything else is sent",
                  flush=True)
        kept_live = set(delete_withdrawn(m, run, live_now, dry))
    withdraw_refused = sorted(r["id"] for r in withdrawing if m.known(r["id"]) in kept_live)
    retiring -= set(withdraw_refused)
    # The local copy takes D1's rows for them with the run's read-back (`deliver`).
    m.guarded += sorted(kept_live)

    # A retired crop names the crop it was retired in favour of by integer id: the first text id on its
    # redirect chain that is a live crop after this publish, past retired crops and text ids with no crop.
    replaced = {}
    if args.replacements:
        replaced = dict(sqlite3.connect(f"file:{HERE / 'source-unit_redirects.sqlite'}?mode=ro", uri=True)
                        .execute("SELECT id, target FROM unit_redirects"))
    if "unit_redirects" in tables:
        replaced.update(src.execute("SELECT id, target FROM unit_redirects"))
    catalogued_live = {r["id"] for r in units if r["origin"] == "local"}

    def live(text):
        if text in catalogued_live or ("corpus_units" in tables and src.execute("SELECT 1 FROM corpus_units WHERE id=?", (text,)).fetchone()):
            return True
        cid = None if text in retiring else m.known(text)
        return cid is not None and m.atlas.execute("SELECT 1 FROM crops WHERE id=?", (cid,)).fetchone() is not None

    replacing, unresolved = {}, []
    for r in units:
        if r["origin"] == "retired" and r["id"] in replaced:
            try:
                found = replacement(r["id"], replaced, live)
            except ValueError as loop:
                found = None
                unresolved.append(f"{r['id']}: its redirects loop ({loop})")
            else:
                if found is None:
                    unresolved.append(f"{r['id']}: no live crop on its redirect chain from {replaced[r['id']]}")
            # A chain with no live crop leaves `replaced_by` unset; the site answers that the crop is retired.
            replacing[r["id"]] = found
    naming = []
    if args.replacements:
        # Every other retired crop with a redirect: a mirror id with no row and a retired entry.
        catalogued = {r["id"] for r in units}
        for text in sorted(replaced.keys() - catalogued):
            cid = m.known(text)
            if cid is None or m.atlas.execute("SELECT 1 FROM crops WHERE id=?", (cid,)).fetchone() or not entry(cid).get("retired") \
                    or entry(cid).get("withdrawn") or text in retiring:
                continue
            try:
                found = replacement(text, replaced, live)
            except ValueError as loop:
                found = None
                unresolved.append(f"{text}: its redirects loop ({loop})")
            else:
                if found is None:
                    unresolved.append(f"{text}: no live crop on its redirect chain from {replaced[text]}")
            replacing[text] = found
            naming.append((cid, text))

    # What the withdrawn crops leave behind: a retired entry naming one as its replacement is named
    # again from the redirects (or names none), and an image another entry also names stays mapped.
    shared = set()
    if withdrawing:
        gone = {cid for r in withdrawing if r["id"] not in withdraw_refused and (cid := m.known(r["id"])) is not None}
        leaving = set().union(*(image_keys(entry(cid)) for cid in gone)) if gone else set()
        redirects = HERE / "source-unit_redirects.sqlite"
        if redirects.exists():
            replaced = dict(sqlite3.connect(f"file:{redirects}?mode=ro", uri=True).execute("SELECT id, target FROM unit_redirects"))
        for pid, body in published_bundles(m, bundles):
            for e in body["crops"]:
                cid = pid * PAGE + e["slot"]
                if cid in gone or e.get("withdrawn"):
                    continue
                shared |= image_keys(e) & leaving
                if e.get("retired") and e.get("replaced_by") in gone:
                    try:
                        found = replacement(e["id"], replaced, live) if e["id"] in replaced else None
                    except ValueError as loop:
                        found = None
                        unresolved.append(f"{e['id']}: its redirects loop ({loop})")
                    replacing[e["id"]] = found
                    naming.append((cid, e["id"]))
        if shared:
            print(f"{len(shared)} images of withdrawn crops are named by other crops too and stay mapped", flush=True)

    # When a re-cut's new cut is recorded (`cut_at`), as the old site timed its crop versions.
    cut_time = datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")

    n, crop_ids, retired = 0, {}, set()
    for r in units:
        data = json.loads(r["data"])
        if r["origin"] == "withdrawn":
            if r["id"] in withdraw_refused:
                continue
            cid = m.known(r["id"])
            current = cid is not None and m.atlas.execute("SELECT revision FROM crops WHERE id=?", (cid,)).fetchone()
            if current:
                m.retire(cid, r["id"], current["revision"], withdrawn=True)
                retired.add(cid)
                n += 1
            elif cid is not None and entry(cid) and not entry(cid).get("withdrawn"):
                # Retired before: its entry gives up its record too.
                m.touched_pages[cid // PAGE][cid % PAGE] = ({"id": r["id"], "retired": True, "cut": entry(cid).get("cut", 0),
                                                             **WITHDRAWN}, True)
                n += 1
            continue
        if r["origin"] == "retired":
            cid = m.known(r["id"])
            current = cid is not None and m.atlas.execute("SELECT revision FROM crops WHERE id=?", (cid,)).fetchone()
            if current:
                m.retire(cid, r["id"], current["revision"])
                retired.add(cid)
                n += 1
            elif cid is None:
                # A crop retired before it reached the new layout still gets a number, kept only in its
                # page's bundle, so its reviews, claims and links resolve.
                cid = m.allocate(r["id"], data.get("page_id") or f"book:{r['document']}", r["document"])
                m.touched_pages[cid // PAGE][cid % PAGE] = ({"id": r["id"], "retired": True, **bundle_entry(data)}, True)
                n += 1
            if r["id"] in replacing:
                naming.append((cid, r["id"]))
            continue
        cid = m.known(r["id"]) or m.allocate(r["id"], data.get("page_id") or f"book:{r['document']}", r["document"])
        crop_ids[r["id"]] = cid
        char = m.grapheme(r["character"])
        given = {"id": cid, "kind": 0, "book": m.book(r["document"]), "char": char,
                 "family": m.grapheme(r["family"]) if with_family else KEEP,
                 "visual_group": m.code("visual_group", r["visual_group"]), "production": m.production(r["production"]),
                 "category": m.code("category", r["category"]),
                 # A crop with no style is unassessed: galleries page by style and hold no crop without one.
                 "style": m.code("style", r["style"] or "unassessed"),
                 "state": m.code("state", r["state"]), "quiz": r["quiz"], "year": years.get(r["document"], KEEP),
                 "revision": r["revision"], "priority": r["priority"] if with_priority else KEEP, "shuffle": shuffle(cid)}
        bundle = {"id": r["id"], **bundle_entry(data)}
        previous, row = entry(cid), m.atlas.execute("SELECT cut, state FROM crops WHERE id=?", (cid,)).fetchone()
        old_cut = row["cut"] if row else previous.get("cut", 0)
        if r["id"] in recut:
            # The version the crop had joins its earlier cuts, with when it was recorded, so a citation of it
            # still finds its pixels; the new cut is recorded now.
            given["cut"] = old_cut + 1
            bundle["cuts"] = merged_cuts(previous.get("cuts"), bundle.get("cuts"),
                                         [{"cut": old_cut, **{k: previous[k] for k in CUT_FIELDS if k in previous},
                                           **({"at": previous["cut_at"]} if previous.get("cut_at") else {})}])
            bundle["cut_at"] = cut_time
        elif row is None and previous.get("retired"):
            given["cut"] = old_cut
        for field in ("image", "context_image"):
            if place(data, field):
                bundle[field + "_place"] = place(data, field)
        m.crop(given, bundle, r["revision"], True, suspect=bool(data.get("suspect")))
        if r["id"] in recut and row is not None and row["state"] in MARK_STATES:
            m.unmark(cid)
        if with_votes:
            m.vote(cid, given.get("cut", row["cut"] if row else 0), r["visual"], r["context"])
        n += 1

    if "corpus_units" in tables:
        for r in src.execute("SELECT * FROM corpus_units ORDER BY id"):
            source = r["id"].split("|")[0].split(":")[0]
            cid = m.known(r["id"])
            if cid is None:
                # New corpus glyphs fill the newest synthetic page of their source and book, then open the next.
                prefix = f"corpus:{source}:{r['document'] or ''}:"
                last = m.ids.execute("SELECT key, next_slot FROM pages WHERE substr(key, 1, ?) = ? "
                                     "AND substr(key, ? + 1) NOT GLOB '*[^0-9]*' ORDER BY id DESC LIMIT 1",
                                     (len(prefix), prefix, len(prefix))).fetchone()
                key = last[0] if last and last[1] < CORPUS_PAGE else f"{prefix}{int(last[0].rsplit(':', 1)[1]) + 1 if last else 0}"
                cid = m.allocate(r["id"], key, r["document"] or source)
            crop_ids[r["id"]] = cid
            o = overlay.get(r["id"])
            # An upstream glyph speaks for the review columns only through its review overlay.
            char = m.grapheme((o["character"] if o else None) or r["character"])
            filed = o["family"] if o and with_family else r["family"]
            given = {"id": cid, "kind": 1, "book": m.book(r["document"] or source), "char": char,
                     "family": m.grapheme(filed),
                     "visual_group": m.code("visual_group", r["visual_group"]), "production": m.production(r["production"]),
                     "style": m.code("style", (o["style"] if o else None) or r["style"] or "unassessed"),
                     "state": m.code("state", o["state"] if o else "pending"), "quiz": o["quiz"] if o else 1,
                     "year": years.get(r["document"], KEEP), "revision": o["revision"] if o else 0, "shuffle": shuffle(cid),
                     # A corpus glyph's script category is its review overlay's, as the old site listed it.
                     "category": m.code("category", o["category"]) if o else None}
            m.crop(given, {"id": r["id"], "label": r["label"], "record_place": [r["object"], r["offset"], r["size"]]},
                   o["revision"] if o else 0, o is not None)
            n += 1

    # When each crop's current cut was recorded and its other encodings, from the history load's catalogue:
    # set on the entry this publish writes for the crop, else on its published entry.
    if "cut_facts" in tables:
        unknown, gone = [], 0
        for r in src.execute("SELECT id, cut_at, encodings FROM cut_facts ORDER BY id"):
            cid = m.known(r["id"])
            if cid is None:
                unknown.append(r["id"])
                continue
            facts = {"cut_at": r["cut_at"], "encodings": json.loads(r["encodings"]) or None}
            pid, slot = cid // PAGE, cid % PAGE
            if slot in m.touched_pages.get(pid, {}):
                written, changed = m.touched_pages[pid][slot]
                if not written.get("retired"):
                    m.touched_pages[pid][slot] = ({**written, **{k: v for k, v in facts.items() if v is not None}}, changed)
                continue
            old = entry(cid)
            if not old or old.get("retired"):
                gone += 1
                continue
            new = {k: v for k, v in {**old, **facts}.items() if k != "slot" and v is not None}
            if new != {k: v for k, v in old.items() if k != "slot"}:
                m.touched_pages[pid][slot] = (new, False)
        if unknown:
            sys.exit(f"{len(unknown)} cut_facts rows name crops the mirror lacks: {unknown[:5]}; nothing published")
        if gone:
            print(f"cut_facts: {gone} crops retired or without an entry, left as they are", flush=True)

    # Corpus glyphs flagged for a person, in the order the catalogue names them, after the crops above.
    # Each is the Worker's admin flag (`flagCorpus`): its finding, its first corpus row and an event of
    # kind `flag` with the wrong-character issue under a submission of the publisher, which moves the
    # crop, its review overlay and its tally cells as `cropChanges` does.
    flag_sql, flags, cells = [], defaultdict(list), {}
    if "corpus_flags" in tables:
        at, flagged = int(time.time() * 1000), m.code("state", "flagged")
        kind, issue = (m.atlas.execute("SELECT code FROM codes WHERE field=? AND key=?", key).fetchone() for key in
                       (("kind", "flag"), ("issue", "character")))
        if kind is None or issue is None:
            sys.exit("the local copy has no code for the flag event; apply migration 0010 to it first")
        listed = []
        for r in src.execute("SELECT id, character, finding, realigned, transcribed FROM corpus_flags ORDER BY rowid"):
            cid = m.known(r["id"])
            row = cid is not None and m.atlas.execute("SELECT c.*, g.text FROM crops c LEFT JOIN graphemes g ON g.id=c.char "
                                                      "WHERE c.id=?", (cid,)).fetchone()
            if not row:
                flags["missing"].append(r["id"])
            elif row["kind"] != 1 or m.atlas.execute("SELECT 1 FROM corpus_rows WHERE crop=?", (cid,)).fetchone():
                flags["held"].append(r["id"])
            elif row["text"] != r["character"]:
                flags["changed"].append(r["id"])
            elif not row["quiz"]:
                flags["unshowable"].append(r["id"])
            else:
                flags["flagged"].append(r["id"])
                listed.append((cid, row, r))
        gen = "(SELECT gen FROM generation WHERE id=0)"
        actor = f"(SELECT id FROM actors WHERE key={lit(PUBLISHER)})"
        cell = "c.kind,coalesce(c.char,-1),c.book,coalesce(c.production,-1),coalesce(c.style,-1),c.state,c.quiz"
        for start in range(0, len(listed), FLAG_MAX):
            chunk = listed[start:start + FLAG_MAX]
            key = f"{PUBLISHER}:{stamp}:{start // FLAG_MAX}"
            glyphs = [{"id": r["id"], "character": r["character"], "finding": r["finding"], "realigned": r["realigned"],
                       "transcribed": r["transcribed"]} for _, _, r in chunk]
            submission = f"(SELECT id FROM submissions WHERE key={lit(key)})"
            group = [f"INSERT OR IGNORE INTO actors(key,user,via,at) VALUES({lit(PUBLISHER)},NULL,{lit(PUBLISHER)},{at});",
                     (f"INSERT INTO submissions(key,actor,request,response,at) VALUES({lit(key)},{actor},"
                      f"{lit(json.dumps({'target': '@flags', 'input': {'glyphs': glyphs}}, ensure_ascii=False))},"
                      f"{lit(json.dumps({'flagged': [g['id'] for g in glyphs]}, ensure_ascii=False))},{at});")]
            for cid, row, r in chunk:
                rev, fresh = row["revision"], f"NOT EXISTS (SELECT 1 FROM corpus_rows WHERE crop={cid})"
                mine = f"EXISTS (SELECT 1 FROM corpus_flags WHERE crop={cid} AND at={at})"
                event = f"EXISTS (SELECT 1 FROM events WHERE crop={cid} AND submission={submission})"
                tally = ("INSERT INTO tally(gen,kind,char,book,production,style,state,quiz,n) SELECT "
                         f"{gen},{cell},{{n}} FROM crops c WHERE c.id={cid} AND c.revision={{rev}} AND {event} "
                         "ON CONFLICT DO UPDATE SET n=n+excluded.n;")
                # Each waits on the one before, so a glyph reviewed or named since keeps the site's rows.
                group += [
                    (f"INSERT OR REPLACE INTO corpus_flags(crop,finding,realigned,transcribed,at) SELECT {cid},{lit(r['finding'])},"
                     f"{lit(r['realigned'])},{lit(r['transcribed'])},{at} WHERE EXISTS (SELECT 1 FROM crops WHERE id={cid} "
                     f"AND revision={rev}) AND {fresh};"),
                    f"INSERT INTO corpus_rows(crop,seq) SELECT {cid},(SELECT coalesce(max(seq),0)+1 FROM corpus_rows) WHERE {mine} AND {fresh};",
                    ("INSERT INTO events(crop,revision,cut,gen,actor,submission,at,kind,label,state,issue,shown) "
                     f"SELECT c.id,c.revision+1,c.cut,{gen},{actor},{submission},{at},{kind[0]},c.char,{flagged},{issue[0]},c.char "
                     f"FROM crops c WHERE c.id={cid} AND c.revision={rev} AND {mine};"),
                    tally.format(n=-1, rev=rev),
                    ("INSERT INTO reviews(crop,gen,at,char,state,style,quiz,x,y,w,h,base_char,base_state,base_style,base_quiz) "
                     f"SELECT c.id,{gen},{at},c.char,{flagged},c.style,c.quiz,r.x,r.y,r.w,r.h,c.char,c.state,c.style,c.quiz "
                     f"FROM crops c LEFT JOIN reviews r ON r.crop=c.id WHERE c.id={cid} AND c.revision={rev} AND {event} "
                     "ON CONFLICT(crop) DO UPDATE SET gen=excluded.gen,at=excluded.at,char=excluded.char,state=excluded.state,"
                     "quiz=excluded.quiz,x=excluded.x,y=excluded.y,w=excluded.w,h=excluded.h;"),
                    f"UPDATE crops SET state={flagged},revision={rev + 1} WHERE id={cid} AND revision={rev} AND {event};",
                    tally.format(n=1, rev=rev + 1)]
                cells[cid] = [row["kind"], -1 if row["char"] is None else row["char"], row["book"],
                              -1 if row["production"] is None else row["production"], -1 if row["style"] is None else row["style"],
                              row["state"], row["quiz"]]
                m.atlas.execute("UPDATE crops SET state=?, revision=? WHERE id=?", (flagged, rev + 1, cid))
                m.guarded.append(cid)
                n += 1
            flag_sql.append(group)

    # Replacements are numbered once every crop of the catalogue has its id. None removes the field.
    for cid, text_id in naming:
        target = m.known(replacing[text_id]) if replacing[text_id] else None
        retiring_now = m.touched_pages.get(cid // PAGE, {}).get(cid % PAGE)
        if retiring_now:
            retiring_now[0]["replaced_by"] = target
        elif entry(cid).get("replaced_by") != target:
            # Already retired: a replacement named since, or one the entry holds as a text id, is set.
            m.touched_pages[cid // PAGE][cid % PAGE] = ({"id": text_id, "retired": True, "replaced_by": target}, True)

    # Runs: those starting at this catalogue's crops follow it; those touching a retired crop go.
    runs_note, scope, texts = "", {"books": set(), "shards": set()}, {}
    if (crop_ids and "unit_ngrams" in tables) or retired:
        want, missing = {}, 0
        if "unit_ngrams" in tables:
            horizontal(src.execute("SELECT vertical FROM unit_ngrams"))
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
                want[key] = (members[1], members[2] if len(members) > 2 else None, m.book(r["document"]))
                if r["text"] is not None:
                    texts[(first, r["size"])] = r["text"]
                m.runs_seen.add(key)
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
                have[tuple(r[k] for k in RUN_KEY)] = (r["second"], r["third"], r["book"])
        # Every run is written down the page, so each counts once in `n` and in `down`.
        delta = defaultdict(int)

        def touch(key, value):
            if key[1]:
                scope["books"].add(value[2])
                scope["shards"].add((key[0], ord(key[1][0])))
        for key, value in have.items():
            if key not in want:
                texts.setdefault((key[4], key[0]), None)
                m.runs_sql.append("DELETE FROM runs WHERE " + " AND ".join(f"{c}={lit(v)}" for c, v in zip(RUN_KEY, key)) + ";")
                delta[key[:2]] -= 1
                touch(key, value)
        for key, value in want.items():
            old = have.get(key)
            if old is None:
                m.runs_sql.append(insert("runs", (*RUN_KEY, "second", "third", "book"), (*key, *value)))
                delta[key[:2]] += 1
            elif old != value:
                # The same run with other crops or another book: only what changed is written.
                changed = ", ".join(f"{c}={lit(v)}" for c, v, w in zip(("second", "third", "book"), value, old) if v != w)
                m.runs_sql.append(f"UPDATE runs SET {changed} WHERE " + " AND ".join(f"{c}={lit(v)}" for c, v in zip(RUN_KEY, key)) + ";")
                touch(key, old)
            else:
                continue
            touch(key, value)
        # A run written anew counts again in its shard, though its row is unchanged.
        has_texts = m.runs.execute("SELECT 1 FROM sqlite_master WHERE name='run_text'").fetchone()
        for key in want:
            text = texts.get((key[4], key[0]))
            held_text = m.runs.execute("SELECT text FROM run_text WHERE first=? AND size=?", (key[4], key[0])).fetchone() if has_texts else None
            if key[1] and text != (held_text[0] if held_text else None):
                scope["shards"].add((key[0], ord(key[1][0])))
        for (size, graphemes), dn in delta.items():
            if dn:
                m.runs_sql.append(f"INSERT INTO run_stats(size,graphemes,n,down) VALUES({size},{lit(graphemes)},{dn},{dn}) "
                                  "ON CONFLICT(size,graphemes) DO UPDATE SET n=n+excluded.n, down=down+excluded.down;")
                m.runs_sql.append(f"DELETE FROM run_stats WHERE size={size} AND graphemes={lit(graphemes)} AND n<=0;")
        runs_note = f", {len(m.runs_sql)} run statements"
    # How this catalogue's crops are written: a crop written apart from its family head has its row in
    # run_written, and the runs it is in are counted again in the answers. A crop with no character (a
    # corpus glyph known by its label) keeps its row, which only runs.py derives.
    published = sorted((set(crop_ids.values()) | retired) - {None})
    if published and not m.runs.execute("SELECT 1 FROM sqlite_master WHERE name='run_written'").fetchone():
        sys.exit("the local runs have no written crops yet; runs.py --send loads them first")
    if published:
        written, held = {}, {}
        for i in range(0, len(published), 500):
            chunk = published[i:i + 500]
            marks = ",".join("?" * len(chunk))
            written.update(m.atlas.execute(f"{WRITTEN} AND c.id IN ({marks})", chunk).fetchall())
            named = {r[0] for r in m.atlas.execute(f"SELECT id FROM crops WHERE char IS NOT NULL AND id IN ({marks})", chunk)}
            held.update((c, w) for c, w in m.runs.execute(f"SELECT crop, char FROM run_written WHERE crop IN ({marks})", chunk)
                        if c in named or c in retired)
        moved = sorted(c for c in held.keys() | written.keys() if held.get(c) != written.get(c))
        m.runs_sql += [f"DELETE FROM run_written WHERE crop={c};" if c not in written else
                       f"INSERT INTO run_written(crop,char) VALUES({c},{lit(written[c])}) ON CONFLICT(crop) DO UPDATE "
                       "SET char=excluded.char WHERE char<>excluded.char;" for c in moved]
        for i in range(0, len(moved), 300):
            chunk = moved[i:i + 300]
            marks = ",".join("?" * len(chunk))
            for size, graphemes in m.runs.execute(f"SELECT size, graphemes FROM runs WHERE first IN ({marks}) OR second IN ({marks}) "
                                                  f"OR third IN ({marks})", chunk * 3):
                if graphemes:
                    scope["shards"].add((size, ord(graphemes[0])))
    # Runs this catalogue cannot rebuild: a relabelled crop that is the second or third of a run
    # anchored on a crop outside the catalogue keeps that run's old text until its first is published.
    rebuilt = {k[4] for k in m.runs_seen}
    behind = []
    for i in range(0, len(m.relabelled), 500):
        chunk = m.relabelled[i:i + 500]
        marks = ",".join("?" * len(chunk))
        behind += [r[0] for r in m.runs.execute(f"SELECT first FROM runs WHERE second IN ({marks}) OR third IN ({marks})", chunk + chunk)
                   if r[0] not in rebuilt]
    if behind:
        (run / "runs-behind.json").write_text(json.dumps(sorted(set(behind))))
        runs_note += f"; {len(set(behind))} runs keep an old text until their first crop is published"

    # Pages: those whose crops or facts changed get the next revision, the bundle merged onto the last.
    facts = {r["id"]: json.loads(r["data"]) for r in src.execute("SELECT id, data FROM pages")} if "pages" in tables else {}
    for key, page_facts in facts.items():
        row = m.ids.execute("SELECT id FROM pages WHERE key=?", (key,)).fetchone()
        if row and bundle_of(row[0]).get("page") != page_facts:
            m.touched_pages.setdefault(row[0], {})
    files, unmapped = [], set()
    for pid, entries in m.touched_pages.items():
        p = m.atlas.execute("SELECT * FROM pages WHERE id=?", (pid,)).fetchone()
        previous = bundle_of(pid)
        merged = dict(previous["by_slot"])
        touched = p["key"] in facts and previous.get("page") != facts[p["key"]]
        for slot, (new_entry, row_changed) in entries.items():
            old = merged.get(slot, {})
            # A crop's new record replaces its old one, so a field the record no longer has goes. A
            # retired crop keeps its record; an image keeps its pack place when the catalogue could
            # not look it up.
            new = {k: v for k, v in {**old, **new_entry, "slot": slot}.items() if v is not None} \
                if new_entry.get("retired") and not new_entry.get("withdrawn") else {"slot": slot, **new_entry}
            if new_entry.get("withdrawn"):
                unmapped |= image_keys(old) - shared
            for field in ("image", "context_image"):
                if field + "_place" not in new and field + "_place" in old and new.get(field) == old.get(field):
                    new[field + "_place"] = old[field + "_place"]
            # A crop's earlier cuts stay, and when its cut was recorded; a record that names cuts sets those it
            # names, and a re-cut its new time.
            if "cuts" in old and not new.get("retired"):
                new["cuts"] = merged_cuts(old["cuts"], new.get("cuts"))
            if "cut_at" not in new and not new.get("retired"):
                new.update({k: old[k] for k in ("cut_at", "encodings") if k in old and k not in new})
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

    groups = [m.head] + [m.page_sql[pid] for pid in sorted(m.page_sql)] + flag_sql
    atlas_parts = write_parts(run, "atlas", [g for g in groups if g])
    runs_parts = write_parts(run, "runs", [[s] for s in m.runs_sql]) if m.runs_sql else 0
    (run / "guarded.json").write_text(json.dumps(m.guarded))
    (run / "run-answers.json").write_text(json.dumps({"books": sorted(scope["books"]), "shards": sorted(scope["shards"]),
                                                      "texts": [[*k, v] for k, v in sorted(texts.items())]}))
    statements = sum(len(g) for g in groups)
    (run / "stale.json").write_text(json.dumps(m.stale))
    voted = [votes.write(run / "votes", pid, m.votes[pid]) for pid in sorted(m.voted)
             if votes.encode(m.votes[pid]) != ((OUT / "votes" / f"{pid}.json.gz").read_bytes()
                                              if (OUT / "votes" / f"{pid}.json.gz").exists() else None)]
    print(f"{n} crops read, {statements} statements in {atlas_parts} parts{runs_note} in {runs_parts} parts, "
          f"{len(files)} pages, {len(voted)} vote objects -> {run}", flush=True)
    if unmapped:
        (run / "unmapped.json").write_text(json.dumps(sorted(unmapped)))
        print(f"{len(unmapped)} images of withdrawn crops leave the image key map", flush=True)
    if fresh_media:
        (run / "media.json").write_text(json.dumps(fresh_media))
    if flags:
        (run / "flags.json").write_text(json.dumps({**flags, "at": at, "state": flagged, "cells": cells}))
        print("corpus flags: " + ", ".join(f"{len(v)} {k}" for k, v in sorted(flags.items())) + f" (ids in {run.name}/flags.json)",
              flush=True)
    if unresolved:
        (run / "unreplaced.txt").write_text("\n".join(unresolved) + "\n")
        print(f"{len(unresolved)} retired crops have no live crop on their redirect chain and name no replacement "
              f"(all in {run.name}/unreplaced.txt), for example: " + "; ".join(unresolved[:5]), flush=True)
    if m.stale:
        print(f"{len(m.stale)} crops have a newer review on the site than in this catalogue; their review columns were "
              f"left as published (ids in {run.name}/stale.json)", flush=True)
    # The local copy takes the changes only from the part files, after D1 has taken them.
    m.atlas.rollback()
    m.runs.rollback()
    if dry:
        m.ids.rollback()
        mark(run, "dry run")
        print("dry run: nothing allocated, sent or changed")
        return
    if withdraw_refused:
        (run / "refused-withdrawals.txt").write_text("\n".join(withdraw_refused) + "\n")
    if not atlas_parts and not runs_parts and not voted and not withdraw_refused and not pending():
        m.ids.rollback()
        mark(run, "nothing to send")
        # Nothing else is written, so the new image keys go into the local shards here.
        if fresh_media:
            media_map.merge(fresh_media)
        media_map.send(upload)
        print("nothing changed")
        return
    # The allocation and the record that this publish is pending are one transaction.
    m.ids.execute("INSERT INTO pending VALUES(?)", (run.name,))
    m.ids.commit()
    mark(run, "allocated")
    deliver(m, run)
    print(f"published {stamp}")
    answer()
    refuse_withdrawals(run)


def answer():
    """The publication's answers, so the counts follow this publish (answers.py); the lock is held."""
    import answers
    answers.publication(False)


if __name__ == "__main__":
    main()
