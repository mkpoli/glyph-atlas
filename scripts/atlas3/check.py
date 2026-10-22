"""Check the new layout against a copy of the live tables, crop by crop, before the site switches to it.

    ATLAS3_DIR=work/atlas-v3 python3 scripts/atlas3/check.py [--cutover]

Reads the `source-*.sqlite` copies (`pull.py`) and the local copies the publisher keeps (`out/`): every
publish is confirmed by its stamp in D1 and replayed on the local copy. Every check covers every crop;
a failing one lists up to SHOWN examples in `out/check-<stamp>.txt` and the run exits 1. A check that
cannot run yet is skipped.

--cutover, for the run before the switch, fails every skip and also reads the remote databases, after
printing how many rows it will read: glyph-atlas-v3 (this translation's load stamps, each loaded
table's row count and checksum, each marked crop's state and revision; about 150k rows), and
glyph-atlas and glyph-atlas-accounts for the accounts (a few thousand rows each). Without it nothing
remote is read.

- Ids: every live crop, corpus glyph and retired crop has a number, and no number has two crops.
- Rows: each crop's character, state, style, withheld flag, production, category, visual group,
  revision, book, witness year and suspect flag equal its live record's; a corpus glyph's review
  columns come from its review overlay. Once load_history.py has set the marks, a pending crop with a
  live mark has the mark as its state, one revision on.
- Bundles: each collected crop's entry is its live record (less the fields its row holds), a corpus
  glyph's points at its live record, and a retired crop's is marked retired and has no row. Its
  `replaced_by`, followed through retired entries, reaches a live crop within 8 steps; it has none
  only when its redirects reach no live crop.
- Runs: the runs of two and three crops are the live ones, crop for crop, with their graphemes, hand
  and book; once runs.py has loaded them, each is written as its live row (its crops' `run_written`
  rows, else their family heads), its graphemes are its crops' family heads in the layout's
  `graphemes` (the fold a query of it takes), and Explore's grids, each book's grid and the forms shards
  in out/runs-answers/ count the live runs as the old `ngram_counts` and `ngram_forms` did.
- Images: every live image key is in the image key map, at its live place.

History (decision record 0006), when `out/history.sqlite` exists (history.py):
- Rows: every source row of every history table (actors, submissions, events, seen, skips,
  favourites, the claim ledger, the representations forms name, rejections, form decisions,
  corpus_follow, form_bases, written forms, suspects and shapes) is in it, every translated field equal to one derived again from the source copies (actor
  and submission keys, times in ms, crop through the mirror, cut from the live record's box and
  crop_versions), or left out for a reason history-report.txt lists exactly as often; and it holds no
  other rows. An actor with no source row has no user, via or time; actor_stats are recounted as the
  ranking counts. Every form's anchor names a representation. Every claim whose catalogue subject
  is a crop names its crop and cut, and a saved claim response on a crop names it by integer id and
  the cut its claims rest on.
  An event's shown label and named character are read from its evidence as the old
  history page read them; every source corpus row has a corpus row with a number of its own (kept
  from the load that first wrote it), with each admin's flag.
- Cuts: a crop with no collected record is cut 0 only if corpus_units has it, and a tombstone is
  checked against its retired bundle entry; every crop cited at cut -1 has that cut, with its earlier
  box, in its bundle once the cuts are published; no event is past its crop's revision (a tombstone
  with no record has none, and is listed).
- Marks: recomputed from seen marks and skips by the old triggers' rules and compared with the live
  `unit_marks`; the live ones move where they differ, so the differences are listed, not failed.
  history-marks.json holds every live mark.
- After the load: the local copy holds every row load_history.py wrote, equal to history.sqlite's;
  rows loaded with no marks set fail; each marked crop that was pending has its mark as state, with an
  event at its revision and cut by the latest round not undone that showed it there (of rounds at one
  time, the highest submission id); at the cutover the count of marks the load left must be recorded.
  With --cutover, glyph-atlas-v3 equals the local copy in all of this.
- Written: every row the old site wrote to a history table (reviews, rounds, undos, seen marks, skips,
  favourites, claims and actions, form decisions, rejections) is in the local copy by its id or legacy
  id, less exactly the rows history-report.txt leaves out, and each table's newest time agrees; with
  --cutover glyph-atlas-v3 has each table's row count and newest time. Run on a copy taken after the
  freeze, this gates the switch.
- Accounts: with `accounts.sqlite`, every `actors.user` and `favourites.user` names a user, and the
  parts accounts.py load sent (`accounts-loaded.sqlite`) rebuild it, each session among them. With --cutover, the
  copy equals the old database's auth tables and glyph-atlas-accounts equals the copy, as accounts.py
  check compares them.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import itertools
import json
import re
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime

from build import HERE, OUT, PAGE, bundle_entry, char_text
from history import CROP_PREDICATES
from load_history import TABLES as LOADED
from publish import releases_base
from run_answers import FORMS_LISTED
from run_answers import GRID as BOOK_GRID

SHOWN = 20
# How many `replaced_by` steps the site follows from a retired crop.
HOPS = 8


def source(table):
    return sqlite3.connect(f"file:{HERE / f'source-{table}.sqlite'}?mode=ro", uri=True)


def latest(db, table, cols):
    # A keyset copy can hold a row twice when it was copied again; the last copy wins.
    return db.execute(f"SELECT {cols} FROM {table} WHERE _rowid IN (SELECT max(_rowid) FROM {table} GROUP BY id)")


def file_digest(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def translation():
    """load_history.py's name for the translation in `out/history.sqlite`, from its content; None without one."""
    path = OUT / "history.sqlite"
    return "history-" + file_digest(path)[:12] if path.exists() else None


def delivered(atlas, run):
    """Whether load_history.py built the run folder and the local copy holds the stamp of each of its parts
    (a run with nothing to send has none)."""
    if not run.is_dir():
        return False
    stamps = {r[0] for r in atlas.execute("SELECT stamp FROM imports WHERE stamp LIKE ?", (f"{run.name}.%",))}
    return len(stamps) == len(list(run.glob("*.sql")))


def applied_marks(atlas, name):
    """Crop -> mark of history-marks.json once load_history.py has set this translation's marks on the
    local copy, else None."""
    if name is None or not delivered(atlas, OUT / "history-load" / f"{name}-marks"):
        return None
    return {m["crop"]: m["state"] for m in json.loads((OUT / "history-marks.json").read_text())}


class Report:
    def __init__(self, strict=False):
        self.lines, self.failed, self.strict = [], 0, strict

    def check(self, name, problems):
        problems = list(problems)
        self.lines.append(("ok   " if not problems else f"FAIL ({len(problems)}) ") + name)
        self.lines += [f"     {p}" for p in problems[:SHOWN]]
        self.failed += bool(problems)
        print(self.lines[-1 - min(len(problems), SHOWN)], flush=True)

    def info(self, name, lines):
        """Lines to read, not a failure: every one is listed."""
        self.lines.append(f"info {name} ({len(lines)})")
        self.lines += [f"     {line}" for line in lines]
        print(self.lines[-1 - len(lines)], flush=True)

    def skip(self, name, why):
        if self.strict:
            self.check(name, [f"not run at the cutover: {why}"])
            return
        self.lines.append(f"skip {name}: {why}")
        print(self.lines[-1], flush=True)


def check_crops(report, name):
    atlas = sqlite3.connect(f"file:{OUT / 'atlas.sqlite'}?mode=ro", uri=True)
    runs_db = sqlite3.connect(f"file:{OUT / 'atlas-runs.sqlite'}?mode=ro", uri=True)
    mirror = sqlite3.connect(f"file:{OUT / 'mirror.sqlite'}?mode=ro", uri=True)

    id_of = dict(mirror.execute("SELECT text, id FROM ids"))
    names = {f: dict(atlas.execute("SELECT code, key FROM codes WHERE field=?", (f,))) for f in ("state", "style", "category", "visual_group")}
    graphemes = dict(atlas.execute("SELECT id, text FROM graphemes"))
    productions = dict(atlas.execute("SELECT id, key FROM productions"))
    rows = {r[0]: r for r in atlas.execute("SELECT id, kind, char, state, style, quiz, production, category, visual_group, revision, "
                                           "book, year, flags, family FROM crops")}
    books = dict(atlas.execute("SELECT id, key FROM books"))
    book_ids = {key: bid for bid, key in books.items()}
    years = dict(source("document_dating").execute("SELECT document, start FROM document_dating WHERE axis='witness'"))
    redirects = dict(source("unit_redirects").execute("SELECT id, target FROM unit_redirects")) \
        if (HERE / "source-unit_redirects.sqlite").exists() else {}
    rev = dict(atlas.execute("SELECT id, rev FROM pages"))

    units = list(latest(source("units"), "units",
                        "id, origin, character, state, style, quiz, production, category, visual_group, revision, data, document, family"))
    corpus = list(latest(source("corpus_units"), "corpus_units",
                         "id, character, production, style, visual_group, document, object, offset, size, label, family"))
    overlay = {u[0]: u for u in units if u[1] == "corpus"}
    local = [u for u in units if u[1] == "local"]
    retired = [u for u in units if u[1] == "retired"]

    # Ids.
    every = [u[0] for u in local + retired] + [c[0] for c in corpus]
    report.check("every live crop, corpus glyph and retired crop has a number", (t for t in every if t not in id_of))
    taken = Counter(id_of.values())
    report.check("no number has two crops", (f"{cid}: {n} text ids" for cid, n in taken.items() if n > 1))
    live = {id_of[t] for t in [u[0] for u in local] + [c[0] for c in corpus] if t in id_of}
    report.check("the crops table holds the live crops and nothing else",
                 [f"{cid}: no row" for cid in live - rows.keys()] + [f"{cid}: row for no live crop" for cid in rows.keys() - live])

    # Rows.
    def want(character, state, style, quiz, production, category, visual_group, revision, kind):
        return (kind, char_text(character) or None, state, style or "unassessed", quiz, production or "unknown",
                category or None, visual_group or None, revision)

    def got(cid):
        r = rows[cid]
        return (r[1], graphemes.get(r[2]), names["state"].get(r[3]), names["style"].get(r[4]), r[5], productions.get(r[6]),
                names["category"].get(r[7]), names["visual_group"].get(r[8]), r[9], books.get(r[10]), r[11], r[12] & 1,
                graphemes.get(r[13]))

    # Every crop is filed under the family its source names.
    def filed(_character, family):
        return char_text(family) or None

    # A pending crop with a live mark takes the mark as its state, one revision on, once the marks are set.
    marks = applied_marks(atlas, name) or {}

    def marked(cid, w):
        return w[:2] + (marks[cid],) + w[3:8] + (w[8] + 1,) + w[9:] if w[2] == "pending" and cid in marks else w

    diffs = []
    for u in local:
        cid = id_of.get(u[0])
        w = marked(cid, want(*u[2:10], 0) + (u[11] or "unknown", years.get(u[11]), int(bool(json.loads(u[10]).get("suspect"))),
                                              filed(u[2], u[12])))
        if cid in rows and got(cid) != w:
            diffs.append(f"{u[0]} ({cid}): live {w}, new layout {got(cid)}")
    for c in corpus:
        cid, o = id_of.get(c[0]), overlay.get(c[0])
        if cid not in rows:
            continue
        book = c[5] or c[0].split("|")[0].split(":")[0]
        w = want((o[2] if o else None) or c[1], o[3] if o else "pending", (o[4] if o else None) or c[3], o[5] if o else 1,
                 c[2], None, c[4], o[9] if o else 0, 1) + (book, years.get(c[5]), 0,
                                                          filed((o[2] if o else None) or c[1], o[12] if o else c[10]))
        w, g = marked(cid, w), got(cid)
        # A corpus glyph has no category of its own.
        if g[:6] + g[7:] != w[:6] + w[7:]:
            diffs.append(f"{c[0]} ({cid}): live {w}, new layout {g}")
    report.check("every crop's row equals its live record", diffs)
    if "priority" not in {c[1] for c in source("units").execute("PRAGMA table_info(units)")}:
        report.skip("every collected crop has its live unit's review priority", "the units copy has no priority; pull.py --fresh units")
    elif "priority" not in {c[1] for c in atlas.execute("PRAGMA table_info(crops)")}:
        report.skip("every collected crop has its live unit's review priority", "the local copy lacks migration 0011; the next publish applies it")
    else:
        priority = dict(atlas.execute("SELECT id, priority FROM crops WHERE kind = 0"))
        report.check("every collected crop has its live unit's review priority",
                     (f"{t} ({id_of[t]}): live {p}, new layout {priority[id_of[t]]}" for t, p in latest(source("units"), "units", "id, priority")
                      if t in id_of and id_of[t] in priority and priority[id_of[t]] != p))

    # Bundles.
    bundles = {}

    def entry(cid):
        pid = cid // PAGE
        if pid not in bundles:
            # Crops are read page by page, so one bundle is held at a time.
            bundles.clear()
            path = OUT / "pages" / f"{pid}-{rev.get(pid)}.json.gz"
            bundles[pid] = {e["slot"]: e for e in json.loads(gzip.decompress(path.read_bytes()))["crops"]} if path.exists() else None
        return None if bundles[pid] is None else bundles[pid].get(cid % PAGE)

    wrong = []
    for u in sorted(local, key=lambda u: id_of.get(u[0], -1)):
        cid = id_of.get(u[0])
        if cid is None:
            continue
        data, e = json.loads(u[10]), entry(cid)
        if e is None:
            wrong.append(f"{u[0]} ({cid}): no bundle entry")
        else:
            # Earlier cuts are the new layout's own record of a crop's history, not part of the live record.
            have = {k: v for k, v in e.items() if k not in ("slot", "id", "image_place", "context_image_place", "cuts", "cut_at", "encodings")}
            if e.get("id") != u[0] or have != bundle_entry(data):
                differ = sorted(k for k in have.keys() | bundle_entry(data).keys() if have.get(k) != bundle_entry(data).get(k))
                wrong.append(f"{u[0]} ({cid}): bundle entry {e.get('id')} differs in {differ}")
    # When the old site first recorded each crop's current version, and its other encodings under the same
    # box, worked out here from crop_versions and the live records: a collected crop's box and checksum, a
    # corpus glyph's review overlay, whose record crop_versions names.
    recorded = defaultdict(list)
    has_versions = (HERE / "source-crop_versions.sqlite").exists()
    for unit, pixels, box, image, at in source("crop_versions").execute("SELECT unit, pixels, box, image, at FROM crop_versions") \
            if has_versions else []:
        if box4(box) is not None:
            recorded[unit].append((box4(box), pixels, image, at))
    timed, facts_wrong = 0, []
    for u in local + list(overlay.values()):
        cid, versions = id_of.get(u[0]), recorded.get(u[0])
        if cid is None or not versions or cid not in rows:
            continue
        data = json.loads(u[10])
        box, pixels = box4(data.get("box")), data.get("image_sha256") or data.get("source_revision")
        here = [v for v in versions if v[0] == box]
        now = here if pixels is None else [v for v in here if v[1] == pixels]
        want = {"cut_at": min((v[3] for v in now if v[3]), default=None),
                "encodings": [{"pixels": v[1], "image": v[2], "at": v[3]} for v in sorted(here, key=lambda v: v[3] or "") if v not in now] or None}
        e = entry(cid) or {}
        timed += "cut_at" in e
        got = {"cut_at": e.get("cut_at"), "encodings": e.get("encodings")}
        if got != want:
            facts_wrong.append(f"{u[0]} ({cid}): bundle {got}, crop_versions {want}")
    name_facts = "every crop's cut_at and other encodings equal crop_versions' record of its current version"
    if not has_versions:
        report.skip(name_facts, "no copy of crop_versions")
    elif not timed:
        report.skip(name_facts, "no bundle has cut_at yet; publish history-cuts.sqlite (load_history.py writes it)")
    else:
        report.check(name_facts, facts_wrong)

    # The site follows `replaced_by` through retired entries for up to HOPS steps.
    others = {}

    def other_entry(cid):
        pid = cid // PAGE
        if pid not in others:
            path = OUT / "pages" / f"{pid}-{rev.get(pid)}.json.gz"
            others[pid] = {e["slot"]: e for e in json.loads(gzip.decompress(path.read_bytes()))["crops"]} if path.exists() else {}
        return others[pid].get(cid % PAGE, {})

    def reaches_live(cid):
        for _ in range(HOPS):
            if not isinstance(cid, int) or isinstance(cid, bool):
                return False
            if cid in rows:
                return True
            e = other_entry(cid)
            if not e.get("retired"):
                return False
            cid = e.get("replaced_by")
        return False

    def redirects_to_live(text):
        # Walked here on the redirect copy, apart from the publisher's walk.
        seen, at = {text}, redirects.get(text)
        while at is not None and at not in seen:
            if id_of.get(at) in rows:
                return True
            seen.add(at)
            at = redirects.get(at)
        return False

    for u in sorted(retired, key=lambda u: id_of.get(u[0], -1)):
        cid = id_of.get(u[0])
        if cid is None:
            continue
        e = entry(cid) or {}
        named = e.get("replaced_by")
        if cid in rows or not e.get("retired") or e.get("id") != u[0]:
            wrong.append(f"{u[0]} ({cid}): retired on the live site; new layout row {cid in rows}, entry {e.get('id')} "
                         f"retired {e.get('retired')}")
        elif named is not None and not reaches_live(named):
            wrong.append(f"{u[0]} ({cid}): replaced by {named!r}, which reaches no live crop in {HOPS} steps")
        elif named is None and redirects_to_live(u[0]):
            wrong.append(f"{u[0]} ({cid}): names no replacement, though its redirects reach a live crop")
    report.check("every collected and retired crop's bundle entry is its live record", wrong)

    wrong = []
    for c in sorted(corpus, key=lambda c: id_of.get(c[0], -1)):
        cid = id_of.get(c[0])
        if cid is None:
            continue
        e = entry(cid)
        if e is None or e.get("id") != c[0] or e.get("record_place") != [c[6], c[7], c[8]] or e.get("label") != c[9]:
            wrong.append(f"{c[0]} ({cid}): entry {None if e is None else {k: e.get(k) for k in ('id', 'record_place', 'label')}}, "
                         f"live {[c[6], c[7], c[8]]} {c[9]}")
    report.check("every corpus glyph's bundle entry points at its live record", wrong)

    # Runs.
    def key(size, graphemes_text, hand, document, first, second, third):
        return (size, graphemes_text or "", hand, book_ids.get(document or "unknown"), id_of.get(first), id_of.get(second),
                id_of.get(third) if third else None)

    live_runs = Counter(key(*r) for r in source("unit_ngrams").execute(
        "SELECT size, graphemes, hand_order, document, first, second, third FROM unit_ngrams"))
    new_runs = Counter(runs_db.execute("SELECT size, graphemes, hand, book, first, second, third FROM runs"))
    report.check("the runs are the live ones", [f"{k}: live {live_runs[k]}, new layout {new_runs[k]}"
                                                for k in (live_runs.keys() | new_runs.keys()) if live_runs[k] != new_runs[k]])

    check_run_forms(report, book_ids)

    # Images.
    missing, shard_cache = [], {}
    # In key order, so each shard is read once.
    for k, obj, offset, size in source("media").execute("SELECT key, object, offset, size FROM media ORDER BY key"):
        if k[:3] not in shard_cache:
            path = OUT / "media" / f"{k[:3]}.json.gz"
            shard_cache.clear()
            shard_cache[k[:3]] = json.loads(gzip.decompress(path.read_bytes())) if path.exists() else {"objects": [], "keys": {}}
        s = shard_cache[k[:3]]
        place = s["keys"].get(k[3:])
        if not place or [s["objects"][place[0]], place[1], place[2]] != [obj, offset, size]:
            missing.append(k)
    report.check("every live image is in the image key map at its place", missing)


def check_run_forms(report, book_ids):
    """Each run's written form against its live row, and the runs' R2 answers against the live runs, as
    the old triggers counted them (`ngram_forms`, `ngram_counts`, migration 0074)."""
    names = ("each run is written as its live row", "Explore's grids count the live runs",
             "each book's grid counts its live runs", "the forms shards count the live runs' written forms",
             "each run's graphemes are its crops' family heads in the layout's graphemes")
    runs_db = sqlite3.connect(f"file:{OUT / 'atlas-runs.sqlite'}?mode=ro", uri=True)
    # runs.py --send stamps the local copy once it has loaded; a build has `run_written` empty until then.
    if not runs_db.execute("SELECT 1 FROM sqlite_master WHERE name='imports'").fetchone() or \
            not runs_db.execute("SELECT 1 FROM imports WHERE stamp LIKE 'runs-%.answers'").fetchone():
        for check_name in names:
            report.skip(check_name, "the local runs have no written crops yet; runs.py --send loads them")
        return
    db = sqlite3.connect("file::memory:", uri=True)
    for name, path in (("s", HERE / "source-unit_ngrams.sqlite"), ("m", OUT / "mirror.sqlite"), ("a", OUT / "atlas.sqlite"),
                       ("r", OUT / "atlas-runs.sqlite"), ("u", HERE / "source-units.sqlite"), ("k", HERE / "source-corpus_units.sqlite")):
        db.execute("ATTACH ? AS " + name, (f"file:{path}?mode=ro",))
    # Each run crop's written character as the old triggers wrote it into its runs' text (0063), and its
    # family head in the layout; the Worker writes a crop as its run_written row, else that head.
    db.executescript("""
      CREATE TEMP TABLE members(t TEXT PRIMARY KEY) WITHOUT ROWID;
      INSERT OR IGNORE INTO members SELECT first FROM s.unit_ngrams UNION SELECT second FROM s.unit_ngrams
        UNION SELECT third FROM s.unit_ngrams WHERE third IS NOT NULL;
      CREATE TEMP TABLE uc(t TEXT PRIMARY KEY, c TEXT) WITHOUT ROWID;
      INSERT OR REPLACE INTO uc SELECT id, character FROM u.units WHERE id IN (SELECT t FROM members) ORDER BY _rowid;
      CREATE TEMP TABLE kc(t TEXT PRIMARY KEY, c TEXT, l TEXT) WITHOUT ROWID;
      INSERT OR REPLACE INTO kc SELECT id, character, label FROM k.corpus_units WHERE id IN (SELECT t FROM members) ORDER BY _rowid;
      CREATE TEMP TABLE crop(t TEXT PRIMARY KEY, shown TEXT, head TEXT) WITHOUT ROWID;
      INSERT INTO crop SELECT x.t, coalesce(w.char, x.h), x.h FROM (SELECT v.t, i.id,
          coalesce((SELECT hh.text FROM a.graphemes g JOIN a.graphemes hh ON hh.id = g.head WHERE g.text = v.w), v.w) AS h
        FROM (SELECT c.t, coalesce(iif(uc.t IS NOT NULL, uc.c, kc.c), kc.l) AS w FROM members c
          LEFT JOIN uc ON uc.t = c.t LEFT JOIN kc ON kc.t = c.t) v LEFT JOIN m.ids i ON i.text = v.t) x
        LEFT JOIN r.run_written w ON w.crop = x.id;
    """)
    concat = "{0}.{1} || {2}.{1} || iif(u.third IS NULL, '', {3}.{1})"
    joins = "FROM s.unit_ngrams u JOIN crop a0 ON a0.t = u.first JOIN crop a1 ON a1.t = u.second LEFT JOIN crop a2 ON a2.t = u.third"
    shown, heads = concat.format("a0", "shown", "a1", "a2"), concat.format("a0", "head", "a1", "a2")
    report.check(names[0], [f"{first} size {size}: live {text!r}, new layout {got!r}" for first, size, text, got in db.execute(
        f"SELECT u.first, u.size, u.text, {shown} {joins} WHERE u.text IS NOT NULL AND u.text IS NOT {shown}")])
    report.check(names[4], [f"{first} size {size}: {graphemes!r}, heads {got!r}" for first, size, graphemes, got in db.execute(
        f"SELECT u.first, u.size, u.graphemes, {heads} {joins} WHERE u.graphemes IS NOT NULL AND u.graphemes IS NOT {heads}")])

    forms, sites, books = defaultdict(Counter), Counter(), defaultdict(Counter)
    for size, graphemes, text, document in source("unit_ngrams").execute(
            "SELECT size, graphemes, text, document FROM unit_ngrams WHERE text IS NOT NULL AND graphemes IS NOT NULL"):
        forms[(size, graphemes)][text] += 1
        sites[(size, graphemes)] += 1
        if document:
            books[(book_ids.get(document), size)][graphemes] += 1

    def read(path):
        return json.loads(gzip.decompress(path.read_bytes())) if path.exists() else None
    answers = OUT / "runs-answers"

    def ranked(counts, n):
        return [list(x) for x in sorted(counts.items(), key=lambda x: (-x[1], x[0]))[:n]]
    problems = []
    for size in (2, 3):
        want = [[g, n, n, len(forms[(size, g)])] for g, n in ranked(Counter({g: n for (s, g), n in sites.items() if s == size}), BOOK_GRID)]
        if (got := read(answers / f"grid-{size}.json.gz")) != want:
            problems.append(f"grid of {size}: {len(want)} live rows, {None if got is None else len(got)} in the answer")
    report.check(names[1], problems)
    problems = []
    for (book, size), counts in sorted(books.items(), key=str):
        want = [[g, n, n] for g, n in ranked(counts, BOOK_GRID)]
        if (got := read(answers / "books" / f"{book}-{size}.json.gz")) != want:
            problems.append(f"book {book} size {size}: live {want[:3]}, answer {None if got is None else got[:3]}")
    report.check(names[2], problems)
    shards = defaultdict(dict)
    for (size, graphemes), counts in forms.items():
        shards[f"{size}-{ord(graphemes[0]):x}"][graphemes] = [len(counts), ranked(counts, FORMS_LISTED)]
    report.check(names[3], [f"shard {name}" for name, want in sorted(shards.items()) if read(answers / "forms" / f"{name}.json.gz") != want])


# History (decision record 0006): `out/history.sqlite` against the source copies, row by row.

def ms(value):
    """ISO time -> Unix milliseconds."""
    return None if value is None else round(datetime.fromisoformat(value).timestamp() * 1000)


def box4(value):
    """(x, y, w, h) from {"x": ..}, its JSON text, or "x,y,w,h"; None when there is no box."""
    if isinstance(value, str):
        value = value.strip()
        value = (json.loads(value) if value.startswith("{") else value.split(",")) if value else None
    if isinstance(value, dict):
        value = [value.get(k) for k in "xywh"]
    if not value or len(value) != 4 or None in value:
        return None
    return tuple(round(float(v)) for v in value)


def stream(table, cols, order=""):
    db = source(table)
    db.row_factory = sqlite3.Row
    return db.execute(f'SELECT {cols} FROM "{table}"' + (f" ORDER BY {order}" if order else ""))


class Cuts:
    """A crop's cut for a box, from the source copies alone: its live record's box is cut 0 (and every
    corpus glyph is), an earlier box crop_versions holds is -1, and a box no recorded version has is the
    cut below the oldest recorded one. A crop with no collected record is a corpus glyph when
    corpus_units has it, else a tombstone, whose record is its retired bundle entry. None for a crop
    that is neither, and for one with two earlier boxes."""

    def __init__(self, entry):
        # Text id -> its bundle entry, for tombstones.
        self.entry = entry
        # The source copies have no indexes: the crops wanted go in a table here, and each source is
        # read once, joined to it.
        self.db = sqlite3.connect(":memory:", uri=True)
        for t in ("units", "crop_versions", "corpus_units"):
            self.db.execute(f"ATTACH ? AS {t}", (f"file:{HERE / f'source-{t}.sqlite'}?mode=ro",))
        self.db.execute("CREATE TABLE wanted(text TEXT PRIMARY KEY) WITHOUT ROWID")
        self.db.execute("CREATE TABLE refs(ref TEXT PRIMARY KEY) WITHOUT ROWID")

    def want(self, texts):
        self.db.executemany("INSERT OR IGNORE INTO wanted VALUES(?)", ((t,) for t in texts))

    def refer(self, refs):
        self.db.executemany("INSERT OR IGNORE INTO refs VALUES(?)", ((r,) for r in refs))

    def load(self):
        self.ref_unit = dict(self.db.execute("SELECT id, unit FROM crop_versions.crop_versions WHERE id IN (SELECT ref FROM refs)"))
        self.want(self.ref_unit.values())
        self.record, self.revision = {}, {}
        # The last copy of a record wins.
        for text, origin, box, revision in self.db.execute("SELECT id, origin, json_extract(data, '$.box'), revision FROM units.units "
                                                           "WHERE id IN (SELECT text FROM wanted) ORDER BY _rowid"):
            self.record[text], self.revision[text] = (origin, box4(box)), revision
        self.versions = defaultdict(set)
        for unit, box in self.db.execute("SELECT unit, box FROM crop_versions.crop_versions WHERE unit IN (SELECT text FROM wanted)"):
            if box4(box) is not None:
                self.versions[unit].add(box4(box))
        self.corpus = {t for (t,) in self.db.execute("SELECT id FROM corpus_units.corpus_units WHERE id IN (SELECT text FROM wanted)")}
        self.db.close()

    def current(self, text):
        """A crop's current box: its live record's, or a tombstone's from its retired bundle entry."""
        origin, current = self.record.get(text, (None, None))
        if origin in ("local", "retired"):
            return current
        e = self.entry(text)
        return box4(e.get("box")) if e and e.get("retired") else None

    def cut(self, text, box):
        # A corpus glyph has no record of its own, or a review overlay with no box.
        if self.record.get(text, (None,))[0] not in ("local", "retired"):
            if text in self.corpus:
                return 0
            e = self.entry(text)
            if not (e and e.get("retired")):
                return None
        current = self.current(text)
        box = box4(box)
        if box is not None and box == current:
            return 0
        earlier = self.versions[text] - {current}
        if len(earlier) > 1:
            return None
        if box in earlier:
            return -1
        return -2 if earlier else -1

    def ref_cut(self, ref):
        """Crop evidence `id@pixels@box` -> (text id, cut), through the crop_versions row of that id."""
        unit = self.ref_unit.get(ref)
        return (None, None) if unit is None else (unit, self.cut(unit, ref.rsplit("@", 1)[1]))


class Table:
    """One history table: each source row against its translated row, and the rows accounted for.
    `name` is the source table, which history-report.txt counts under; `target` the translated one."""

    def __init__(self, report, history, name, listed, target=None):
        self.report, self.history, self.name, self.listed = report, history, name, listed
        self.target = target or name
        self.problems, self.present, self.gone = [], 0, Counter()

    def compare(self, label, want, got):
        if got is None:
            self.problems.append(f"{label}: not translated")
            return
        self.present += 1
        differ = [f"{k} {v!r} expected, {got.get(k)!r} translated" for k, v in want.items() if got.get(k) != v]
        if differ:
            self.problems.append(f"{label}: {'; '.join(differ)}")

    def account(self, reason):
        self.gone[reason] += 1

    def done(self, extra=True):
        # The rows left out, per reason, are exactly those the report lists.
        for reason in self.gone.keys() | self.listed[self.name].keys():
            if self.gone[reason] != self.listed[self.name][reason]:
                self.problems.append(f"{self.gone[reason]} rows {reason}, history-report.txt lists {self.listed[self.name][reason]}")
        total = self.history.execute(f'SELECT count(*) FROM "{self.target}"').fetchone()[0]
        if extra and total != self.present:
            self.problems.append(f"{total} translated rows, {self.present} of them from a source row")
        gone = "; ".join(f"{n} {reason}" for reason, n in self.gone.items())
        into = f" into {self.target}" if self.target != self.name else ""
        self.report.check(f"every {self.name} row is translated{into}" + (f" or accounted for ({gone})" if gone else ""),
                          self.problems)


def sql_order(value):
    """A value's place in SQLite's ORDER BY: NULL, then numbers, then text, then blobs."""
    rank = 0 if value is None else 1 if isinstance(value, int | float) else 2 if isinstance(value, str) else 3
    return rank, value


def keyed_rows(db, table, cols, keys, where=""):
    """(key, digest of the whole row) for each row of a table in key order, one row at a time; the key
    compares as SQLite orders it, NULL among the rest."""
    names = ",".join(f'"{c}"' for c in cols)
    order = ",".join(f'"{k}"' for k in keys)
    for r in db.execute(f'SELECT {names} FROM "{table}" {where} ORDER BY {order}'):
        yield tuple(sql_order(r[cols.index(k)]) for k in keys), hashlib.sha256(repr(tuple(r)).encode()).digest()


def compare_loaded(report, history, atlas):
    """Every table load_history.py wrote, row for row, in the local copy against history.sqlite; events
    with no `legacy` are the marks', left for their own check."""
    problems = []

    def shown(key):
        return tuple(v for _rank, v in key)

    for table in LOADED:
        info = list(history.execute(f'PRAGMA table_info("{table}")'))
        cols = [r[1] for r in info]
        keys = [r[1] for r in sorted(info, key=lambda r: r[5]) if r[5]]
        where = "WHERE legacy IS NOT NULL" if table == "events" else ""
        ours, theirs = keyed_rows(history, table, cols, keys), keyed_rows(atlas, table, cols, keys, where)
        a, b = next(ours, None), next(theirs, None)
        while a or b:
            if b is None or (a is not None and a[0] < b[0]):
                problems.append(f"{table} {shown(a[0])}: not in the local copy")
                a = next(ours, None)
            elif a is None or b[0] < a[0]:
                problems.append(f"{table} {shown(b[0])}: in the local copy, not in history.sqlite")
                b = next(theirs, None)
            else:
                if a[1] != b[1]:
                    problems.append(f"{table} {shown(a[0])}: differs")
                a, b = next(ours, None), next(theirs, None)
    report.check("the local copy holds every translated row as history.sqlite has it, and no other", problems)


def remote(database):
    """Read-only queries on a D1 database, through publish.query."""
    import publish

    def run(sql):
        assert sql.lstrip().upper().startswith("SELECT"), sql
        return publish.query(database, sql)
    return run


def remote_atlas(report, name, atlas, query):
    """glyph-atlas-v3 against the local copy every load replays: the history loads' stamps, each
    loaded table's row count and checksum (accounts.py's summary on both sides), and each marked crop's
    state and revision."""
    import accounts

    marked = sorted({m["crop"] for m in json.loads((OUT / "history-marks.json").read_text())}) \
        if (OUT / "history-marks.json").exists() else []
    rows = sum(atlas.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0] for t in LOADED)
    print(f"glyph-atlas-v3: about {rows + len(marked):,} rows to read ({rows:,} in {len(LOADED)} tables, "
          f"{len(marked):,} marked crops)", flush=True)
    here = accounts.local(atlas)
    stamps = "SELECT stamp FROM imports WHERE stamp LIKE 'history-%'"
    ours, theirs = ({r["stamp"] for r in q(stamps)} for q in (here, query))
    problems = [f"{s}: in the local copy, not in glyph-atlas-v3" for s in sorted(ours - theirs)]
    problems += [f"{s}: in glyph-atlas-v3, not in the local copy" for s in sorted(theirs - ours)]
    report.check(f"glyph-atlas-v3 holds the {len(ours)} history load stamps the local copy holds, and no other",
                 problems)

    ours, theirs = accounts.summary(here, atlas, LOADED), accounts.summary(query, atlas, LOADED)
    report.check(f"glyph-atlas-v3 holds every loaded table as the local copy does, row count and checksum ({rows:,} rows)",
                 [f"{t}: {ours[t][0]} rows here, {theirs[t][0]} there, checksums {ours[t][1][:12]} and {theirs[t][1][:12]}"
                  for t in LOADED if ours[t] != theirs[t]])

    states = dict(atlas.execute("SELECT code, key FROM codes WHERE field='state'"))
    ours, theirs = {}, {}
    for i in range(0, len(marked), 500):
        sql = f"SELECT id, state, revision FROM crops WHERE id IN ({','.join(map(str, marked[i:i + 500]))})"
        ours.update((r["id"], (r["state"], r["revision"])) for r in here(sql))
        theirs.update((r["id"], (r["state"], r["revision"])) for r in query(sql))
    by_state = Counter(states.get(s, s) for s, _rev in theirs.values())
    report.check("glyph-atlas-v3 gives each marked crop the local copy's state and revision ("
                 + ", ".join(f"{k} {n}" for k, n in sorted(by_state.items())) + ")",
                 [f"{cid}: state and revision {ours.get(cid)} here, {theirs.get(cid)} there"
                  for cid in marked if ours.get(cid) != theirs.get(cid)])


def check_accounts(report, acc, query=None):
    """Every part accounts.py load sent to glyph-atlas-accounts, as replayed in accounts-loaded.sqlite,
    compared with accounts.sqlite table by table, schema, row count and checksum. With `query` (the
    cutover), the chain old database -> accounts.sqlite -> glyph-atlas-accounts as accounts.py check
    compares it."""
    import accounts

    sessions = acc.execute('SELECT count(*) FROM "session"').fetchone()[0] if "session" in accounts.tables(acc) else 0
    name = f"the parts loaded into glyph-atlas-accounts rebuild accounts.sqlite, every session among them ({sessions} sessions)"
    if not accounts.LOADED.exists():
        report.skip(name, f"no {accounts.LOADED.name}; accounts.py load --send writes it")
    else:
        loaded = sqlite3.connect(f"file:{accounts.LOADED}?mode=ro", uri=True)
        report.check(name, accounts.differences(acc, accounts.local(loaded))[0])
    if query is None:
        return
    rows = sum(acc.execute(f"SELECT count(*) FROM {accounts.q(t)}").fetchone()[0] for t in accounts.tables(acc))
    print(f"{accounts.OLD} and {accounts.DATABASE}: about {rows:,} rows to read from each", flush=True)
    old = query(accounts.OLD)
    report.check(f"accounts.sqlite equals the auth tables of {accounts.OLD}, schema, row count and checksum",
                 accounts.differences(acc, old, accounts.auth_tables(old))[0])
    report.check(f"{accounts.DATABASE} equals accounts.sqlite, schema, row count and checksum",
                 accounts.differences(acc, query(accounts.DATABASE))[0])


# What the old site wrote, table by table: (source table, its key and time, the new layout's key and time
# for the same row, whether the new time is in ms). A key names the row as the old site did: its id, or
# the legacy id the translation keeps, through `submissions.key`, `actors.key` and the mirror's text ids.
WRITTEN = (
    ("submissions", "id, at", "SELECT key, at FROM submissions", True),
    ("events", "id, at", "SELECT legacy, at FROM events WHERE legacy IS NOT NULL", True),
    ("seen", "target, submission, at", ("SELECT t.text, s.key, n.at FROM seen n JOIN submissions s ON s.id = n.submission "
                                        "JOIN mirror.ids t ON t.id = n.crop"), True),
    # One skip per crop and actor: the newest.
    ("skips", "target, actor, max(at)", ("SELECT t.text, a.key, k.at FROM skips k JOIN actors a ON a.id = k.actor "
                                         "JOIN mirror.ids t ON t.id = k.crop"), True),
    ("favourites", "user_id, unit, at", "SELECT f.user, t.text, f.at FROM favourites f JOIN mirror.ids t ON t.id = f.crop", True),
    ("actors", "actor, at", "SELECT key, at FROM actors WHERE at IS NOT NULL", True),
    ("assertions", "id, asserted_at", "SELECT id, asserted_at FROM assertions", False),
    ("assertion_actions", "id, at", "SELECT id, at FROM assertion_actions", False),
    ("assertion_evidence", "assertion, kind, ref, NULL", "SELECT assertion, kind, ref, NULL FROM assertion_evidence", False),
    ("assertion_premises", "assertion, premise, NULL", "SELECT assertion, premise, NULL FROM assertion_premises", False),
    ("current_claims", "subject, predicate, scope, slot, at", "SELECT subject, predicate, scope, slot, at FROM current_claims", False),
    ("ledger_submissions", "id, at", "SELECT id, at FROM ledger_submissions", False),
    ("rejections", "submission, at", "SELECT s.key, r.at FROM rejections r JOIN submissions s ON s.id = r.submission", True),
    ("forms", "id, created_at", "SELECT id, created_at FROM forms", False),
    ("representations", "id, NULL", "SELECT id, NULL FROM representations", False),
    ("form_decisions", "id, at", "SELECT id, at FROM form_decisions", False),
    ("corpus_follow", "id, NULL", "SELECT t.text, NULL FROM corpus_follow c JOIN mirror.ids t ON t.id = c.crop", False),
    ("written_forms", "id, at", "SELECT id, at FROM written_forms", False),
)


def check_written(report, query=None):
    """Every row the old site wrote to a history table before the copy (reviews, rounds, undos, seen marks,
    skips, favourites, claims and their actions, form decisions, rejections) is in the new layout, matched
    by its id or the legacy id the translation keeps, and the newest time agrees; rows history.py leaves out
    are those its report lists. With `query` (the cutover), glyph-atlas-v3's row count and newest time of
    each table equal the local copy's. Gates the switch: after the freeze, a fresh copy and the loads."""
    atlas = sqlite3.connect(f"file:{OUT / 'atlas.sqlite'}?mode=ro", uri=True)
    if not atlas.execute("SELECT 1 FROM imports WHERE stamp LIKE 'history-%.rows-%' LIMIT 1").fetchone():
        report.skip("every row the old site wrote is in the new layout", "the history is not loaded; load_history.py loads it")
        return
    absent = [t for t, *_ in WRITTEN if not (HERE / f"source-{t}.sqlite").exists()]
    if absent:
        report.skip("every row the old site wrote is in the new layout", f"no copy of {', '.join(absent)}; pull.py copies them")
        return
    atlas.execute("ATTACH ? AS mirror", (f"file:{OUT / 'mirror.sqlite'}?mode=ro",))
    text = (OUT / "history-report.txt").read_text() if (OUT / "history-report.txt").exists() else ""
    dropped, table = Counter(), None
    for line in text.splitlines():
        if m := re.match(r"(\w+): in \d+, out \d+", line):
            table = m[1]
        elif table and (m := re.match(r"\s+dropped \((\d+)\) (.+)$", line)) and "older skip" not in m[2]:
            dropped[table] += int(m[1])
    figures, problems = [], []
    for table, cols, sql, in_ms in WRITTEN:
        group = " GROUP BY target, actor" if table == "skips" else ""
        old = {tuple(r[:-1]): r[-1] for r in source(table).execute(f'SELECT {cols} FROM "{table}"{group}')}
        new = {tuple(r[:-1]): r[-1] for r in atlas.execute(sql)}
        missing = [k for k in old if k not in new]
        if len(missing) != dropped[table]:
            problems += [f"{table} {k}: written on the old site, not in the new layout" for k in missing[:SHOWN]]
            problems.append(f"{table}: {len(missing)} rows missing, history-report.txt leaves out {dropped[table]}")
        # The newest time among the old rows the layout keeps.
        times = [ms(v) if in_ms else v for k, v in old.items() if v is not None and k in new]
        newest_old, newest_new = max(times, default=None), max((v for v in new.values() if v is not None), default=None)
        if newest_old != newest_new:
            problems.append(f"{table}: newest time {newest_old} on the old site, {newest_new} in the new layout")
        figures.append(f"{table}: {len(old)} old, {len(new)} new, {len(missing)} left out; newest {newest_old}")
    report.info("rows the old site wrote, and the new layout's", figures)
    report.check("every row the old site wrote to a history table is in the new layout, by id or legacy id", problems)
    if query is None:
        return
    remote_ = query("glyph-atlas-v3")
    problems = []
    for table, *_ in WRITTEN:
        target = table
        at = {"assertions": "asserted_at", "forms": "created_at", "assertion_evidence": None, "assertion_premises": None,
              "representations": None, "corpus_follow": None}.get(table, "at")
        sql = f'SELECT count(*) AS n{f", max({at}) AS t" if at else ", NULL AS t"} FROM "{target}"'
        here = atlas.execute(sql).fetchone()
        there = remote_(sql)[0]
        if (there["n"], there["t"]) != tuple(here):
            problems.append(f"{target}: {tuple(here)} in the local copy, ({there['n']}, {there['t']}) in glyph-atlas-v3")
    report.check("glyph-atlas-v3 has each history table's row count and newest time as the local copy", problems)


def check_history(report, name, query=None):
    path = OUT / "history.sqlite"
    if name is None:
        report.skip("history", "no out/history.sqlite; run history.py first")
        return
    history = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    history.row_factory = sqlite3.Row
    atlas = sqlite3.connect(f"file:{OUT / 'atlas.sqlite'}?mode=ro", uri=True)
    mirror = sqlite3.connect(f"file:{OUT / 'mirror.sqlite'}?mode=ro", uri=True)

    def crop(text):
        r = mirror.execute("SELECT id FROM ids WHERE text=?", (text,)).fetchone()
        return r and r[0]

    def one(sql, *args):
        r = history.execute(sql, args).fetchone()
        return None if r is None else dict(r)

    # What history.py's report gives as the reason rows were left out, per table.
    text = (OUT / "history-report.txt").read_text() if (OUT / "history-report.txt").exists() else ""
    report.check("the translation stops no row", [] if text.rstrip().endswith("no row stops the move")
                 else [text.rstrip().splitlines()[-1] if text.strip() else "no history-report.txt"])
    listed, table = defaultdict(Counter), None
    for line in text.splitlines():
        if m := re.match(r"(\w+): in \d+, out \d+", line):
            table = m[1]
        elif table and (m := re.match(r"\s+(?:dropped|stopped) \((\d+)\) (.+)$", line)):
            listed[table][m[2]] += int(m[1])
        elif not line.strip():
            table = None

    actor_key = dict(history.execute("SELECT id, key FROM actors"))
    actor_id = {k: i for i, k in actor_key.items()}
    sub_key = dict(history.execute("SELECT id, key FROM submissions"))
    sub_id = {k: i for i, k in sub_key.items()}
    kinds = dict(history.execute("SELECT code, key FROM codes WHERE field='kind'"))
    issues = dict(history.execute("SELECT code, key FROM codes WHERE field='issue'"))
    states = dict(atlas.execute("SELECT code, key FROM codes WHERE field='state'"))
    graphemes = dict(atlas.execute("SELECT id, text FROM graphemes"))

    rev = dict(atlas.execute("SELECT id, rev FROM pages"))
    pages = {}

    def entry(cid):
        """A crop's bundle entry; the last page read is kept."""
        pid = cid // PAGE
        if pid not in pages:
            pages.clear()
            path = OUT / "pages" / f"{pid}-{rev.get(pid)}.json.gz"
            pages[pid] = {e["slot"]: e for e in json.loads(gzip.decompress(path.read_bytes()))["crops"]} if path.exists() else {}
        return pages[pid].get(cid % PAGE)

    cuts = Cuts(lambda text: (cid := crop(text)) is not None and entry(cid) or None)
    for t in ("seen", "skips", "events"):
        cuts.want(r[0] for r in source(t).execute(f"SELECT DISTINCT target FROM {t}"))
    cuts.refer(r[0] for r in source("assertion_evidence").execute("SELECT ref FROM assertion_evidence WHERE kind='crop'"))
    cuts.refer(r[0] for r in source("current_claims").execute("SELECT crop_version FROM current_claims WHERE crop_version IS NOT NULL"))
    cuts.load()

    # Actors: one per actor text any source table names; those with no source actors row have no
    # user, via or time.
    t, named, known = Table(report, history, "actors", listed), set(), set()
    for table_, column in (("submissions", "actor"), ("events", "actor"), ("skips", "actor"), ("form_decisions", "actor"),
                           ("ledger_submissions", "actor"), ("assertion_actions", "actor"), ("assertions", "asserted_by")):
        named |= {k for (k,) in source(table_).execute(f"SELECT DISTINCT {column} FROM {table_}")}
    for r in stream("actors", "actor, user_id, via, at"):
        known.add(r["actor"])
        t.compare(f"actor {r['actor']}", {"user": r["user_id"], "via": r["via"], "at": ms(r["at"])},
                  one("SELECT user, via, at FROM actors WHERE key=?", r["actor"]))
    for key_ in sorted(named - known):
        t.compare(f"actor {key_} (no source row)", {"user": None, "via": None, "at": None},
                  one("SELECT user, via, at FROM actors WHERE key=?", key_))
    t.done()

    t, undone, src_sub = Table(report, history, "submissions", listed), set(), {}
    undone_ids = {i for (i,) in history.execute("SELECT id FROM submissions WHERE undone")}
    for r in stream("submissions", "id, actor, request, response, at, undone"):
        if r["undone"]:
            undone.add(r["id"])
        src_sub[r["id"]] = (r["actor"], ms(r["at"]))
        got = one("SELECT actor, request, response, at, undone FROM submissions WHERE key=?", r["id"])
        if got:
            got["actor"] = actor_key.get(got["actor"])
        t.compare(f"submission {r['id']}", {"actor": r["actor"], "request": r["request"], "response": r["response"],
                                            "at": ms(r["at"]), "undone": r["undone"]}, got)
    t.done()

    # Events: a crop's revisions follow expected_revision, or time where two share one.
    t = Table(report, history, "events", listed)
    # The label a review showed, as the old history page read it, and the character it named.
    evidence_sql = "json_extract(event, '$.evidence')"
    for target, rows_ in itertools.groupby(stream("events", "id, submission, target, actor, expected_revision, before_data, "
                                                  f"after_data, event, at, coalesce(json_extract({evidence_sql}, '$.label'), "
                                                  f"json_extract({evidence_sql}, '$.snapshot.character.label')) AS shown_label, "
                                                  f"json_extract({evidence_sql}, '$.suggested_character') AS named_code, "
                                                  f"json_extract({evidence_sql}, '$.snapshot.character.reading') AS snapshot_reading, "
                                                  f"json_extract({evidence_sql}, '$.correction.reading') AS saved", "target"),
                                           key=lambda r: r["target"]):
        group, cid = list(rows_), crop(target)
        if cid is None:
            for _r in group:
                t.account("tombstone not yet numbered")
            continue
        revisions = [r["expected_revision"] + 1 for r in group]
        if len(set(revisions)) < len(revisions):
            group = sorted(group, key=lambda r: (ms(r["at"]), r["id"]))
            revisions = range(1, len(group) + 1)
        for r, revision in zip(group, revisions):
            before, after = json.loads(r["before_data"]), json.loads(r["after_data"])
            evidence = json.loads(json.loads(r["event"])["evidence"])
            want = {"crop": cid, "revision": revision, "cut": cuts.cut(target, before.get("box")), "gen": 0, "actor": r["actor"],
                    "submission": r["submission"], "at": ms(r["at"]), "kind": evidence.get("verdict"),
                    "label": char_text(after.get("label")) or None, "state": after.get("state"), "style": None,
                    "quiz": after.get("quiz"), "box": box4(after.get("box")) or (None,) * 4,
                    "note": (evidence.get("note") or None) and evidence["note"][:2000], "issue": evidence.get("issue") or None,
                    "text": evidence.get("suggested_text") or evidence.get("suggested_reading") or None,
                    "shown": char_text(r["shown_label"]) or None, "named": char_text(r["named_code"]) or None,
                    "reading": r["snapshot_reading"] or None, "saved_reading": r["saved"], "legacy": r["id"]}
            got = one("SELECT * FROM events WHERE crop=? AND revision=?", cid, revision)
            if got:
                got.update(actor=actor_key.get(got["actor"]), submission=sub_key.get(got["submission"]), kind=kinds.get(got["kind"]),
                           label=graphemes.get(got["label"]), state=states.get(got["state"]), issue=issues.get(got["issue"]),
                           shown=graphemes.get(got["shown"]), named=graphemes.get(got["named"]),
                           box=(got["x"], got["y"], got["w"], got["h"]))
            t.compare(f"event {r['id']} on {target} ({cid})", want, got)
    t.done()

    # Within one submission, events are numbered in the order its request lists the crops, the order the
    # old site wrote their rows in; and across submissions, by time.
    text_of = {}
    problems, previous = [], None
    by_submission = defaultdict(list)
    for eid, submission, cid, at in history.execute("SELECT id, submission, crop, at FROM events ORDER BY id"):
        if previous is not None and at < previous:
            problems.append(f"event {eid}: at {at} before the event numbered just below it")
        previous = at
        by_submission[sub_key[submission]].append(cid)
    for key_, request in source("submissions").execute("SELECT id, request FROM submissions"):
        got = by_submission.get(key_)
        if not got or len(got) < 2:
            continue
        input_ = json.loads(request).get("input") or {}
        named = [c["id"] for c in (input_.get("answers") or input_.get("crops") or []) if isinstance(c, dict)]
        for cid in got:
            if cid not in text_of:
                text_of[cid] = mirror.execute("SELECT text FROM ids WHERE id=?", (cid,)).fetchone()[0]
        order = [text_of[c] for c in got]
        if order != [t for t in named if t in order]:
            problems.append(f"submission {key_}: events in the order {order[:5]}, the request lists {named[:5]}")
    report.check("events are numbered by time and, within a submission, in its request's order", problems)

    # No event is past its crop's revision: the crops row's, or a retired crop's last live record's.
    problems, unrecorded = [], []
    for cid, revision in history.execute("SELECT crop, max(revision) FROM events GROUP BY crop"):
        r = atlas.execute("SELECT revision FROM crops WHERE id=?", (cid,)).fetchone()
        if r is None:
            text = mirror.execute("SELECT text FROM ids WHERE id=?", (cid,)).fetchone()
            r = (cuts.revision[text[0]],) if text and text[0] in cuts.revision else None
        if r is None:
            # A tombstone with no record left on the old site has no revision to stay below.
            unrecorded.append(f"{cid}: events up to revision {revision}")
        elif revision > r[0]:
            problems.append(f"{cid}: event at revision {revision}, crop at {r[0]}")
    report.check("no event is past its crop's revision", problems)
    report.info("tombstones with no record, so no revision to check their events against", unrecorded)

    # Seen marks and skips, and the marks the old triggers would give: the crop's current box, a
    # round not undone, two or more skippers for `hard`.
    t, marked, seen_rows = Table(report, history, "seen", listed), set(), defaultdict(list)
    for r in stream("seen", "target, submission, box, at"):
        cid = crop(r["target"])
        if cid is None:
            t.account("crop unknown")
            continue
        cut = cuts.cut(r["target"], r["box"])
        seen_rows[r["target"]].append((r["submission"], ms(r["at"]), cut))
        if cut == 0 and r["submission"] not in undone:
            marked.add(r["target"])
        t.compare(f"seen {r['target']} ({cid}) in {r['submission']}", {"cut": cut, "at": ms(r["at"])},
                  one("SELECT cut, at FROM seen WHERE submission=? AND crop=?", sub_id.get(r["submission"]), cid))
    t.done()

    t, skippers = Table(report, history, "skips", listed), defaultdict(set)
    for (target, actor), rows_ in itertools.groupby(stream("skips", "target, submission, actor, box, at", "target, actor"),
                                                    key=lambda r: (r["target"], r["actor"])):
        group, cid = list(rows_), crop(target)
        if cid is None:
            for _r in group:
                t.account("crop unknown")
            continue
        for r in group:
            if cuts.cut(target, r["box"]) == 0 and r["submission"] not in undone:
                skippers[target].add(actor)
        got = one("SELECT cut, at, submission FROM skips WHERE crop=? AND actor=?", cid, actor_id.get(actor))
        if got:
            got["submission"] = sub_key.get(got["submission"])
        # An actor's newest skip of a crop is kept; of two at one time, the one translated.
        newest = max(ms(r["at"]) for r in group)
        kept = [r for r in group if ms(r["at"]) == newest]
        kept = next((r for r in kept if got and r["submission"] == got["submission"]), kept[0])
        for _r in range(len(group) - 1):
            t.account("an older skip by the same actor")
        t.compare(f"skip of {target} ({cid}) by {actor}", {"cut": cuts.cut(target, kept["box"]), "at": newest,
                                                         "submission": kept["submission"]}, got)
    t.done()

    recomputed = {target: "hard" for target, who in skippers.items() if len(who) >= 2}
    for target in marked:
        recomputed.setdefault(target, "seen")
    # Where they differ the live mark is the one moved (decision record 0006): listed, not a failure.
    differ = []
    for target, mark in source("unit_marks").execute("SELECT id, mark FROM unit_marks"):
        if (r := recomputed.pop(target, None)) != mark:
            differ.append(f"{target}: live {mark}, recomputed {r}")
    differ += [f"{target}: recomputed {mark}, no live mark" for target, mark in recomputed.items()]
    report.info("live marks that differ from those recomputed from seen marks and skips; the live ones move", differ)

    marks = json.loads((OUT / "history-marks.json").read_text()) if (OUT / "history-marks.json").exists() else []
    written = Counter((m["crop"], m["state"]) for m in marks)
    problems = []
    for target, mark in source("unit_marks").execute("SELECT id, mark FROM unit_marks"):
        if written[(crop(target), mark)] > 0:
            written[(crop(target), mark)] -= 1
        else:
            problems.append(f"{target} ({crop(target)}): live {mark}, not in history-marks.json")
    problems += [f"{cid}: {mark} in history-marks.json, no live mark" for (cid, mark), n in written.items() for _ in range(n)]
    report.check("history-marks.json holds every live mark", problems)

    # After the load, a marked crop that was pending has its mark as its state, with an event at
    # that revision; a crop not pending (or retired) keeps its state.
    loaded = delivered(atlas, OUT / "history-load" / name)
    if not loaded:
        report.skip("the local copy holds every translated row as history.sqlite has it, and no other",
                    "this translation is not loaded; load_history.py loads it")
    else:
        compare_loaded(report, history, atlas)
        if query is not None:
            remote_atlas(report, name, atlas, query("glyph-atlas-v3"))
    if applied_marks(atlas, name) is None and loaded:
        report.check("every marked crop has its mark as its state",
                     ["the rows of this translation are loaded and its marks are not set; rerun load_history.py"])
    elif applied_marks(atlas, name) is None:
        report.skip("every marked crop has its mark as its state", "the local copy has not had this translation's marks "
                    "applied (no marks stamp in imports); load_history.py applies them, then check again")
    else:
        # Each mark set on a pending crop has its event: the mark's state at the crop's revision and
        # cut, written by the latest round not undone that showed the crop at that cut, derived here
        # from the source seen marks and submissions.
        code = {key: c for c, key in kinds.items()}
        problems, left, matched = [], 0, set()
        # The crops this translation's load marked; the rest were marked by an earlier one.
        set_now = {int(x) for part in (OUT / "history-load" / f"{name}-marks").glob("*.sql")
                   for x in re.findall(r"^UPDATE crops SET .* WHERE id = (\d+) AND", part.read_text(), re.MULTILINE)}
        for m in marks:
            r = atlas.execute("SELECT state, revision, cut FROM crops WHERE id=?", (m["crop"],)).fetchone()
            state = r and states.get(r[0])
            if r is None or state not in ("pending", "seen", "hard"):
                left += 1
                continue
            if state != m["state"]:
                problems.append(f"{m['crop']}: state {state}, marked {m['state']}")
                continue
            e = atlas.execute("SELECT id, cut, gen, actor, submission, at, state FROM events WHERE crop=? AND revision=? AND kind=?",
                              (m["crop"], r[1], code[m["state"]])).fetchone()
            if e is None:
                problems.append(f"{m['crop']}: state {state} with no event setting it at revision {r[1]}")
                continue
            matched.add(e[0])
            text = mirror.execute("SELECT text FROM ids WHERE id=?", (m["crop"],)).fetchone()[0]
            # Of rounds at one time, the highest submission id, as history.py and load_history.py pick it.
            shown = [(at, sub_id.get(key, -1), key) for key, at, cut in seen_rows[text] if cut == r[2] and key not in undone]
            latest, _id, submission = max(shown, default=(None, None, None))
            earlier = m["crop"] not in set_now and [s for s in shown if s[2] == sub_key.get(e[4])]
            if earlier:
                # Set by an earlier load, from the latest round of its copy: a later round leaves it.
                latest, _id, submission = earlier[0]
            got = {"cut": e[1], "gen": e[2], "actor": actor_key.get(e[3]), "submission": sub_key.get(e[4]), "at": e[5],
                   "state": states.get(e[6]), "actor id": e[3], "submission at": history.execute(
                       "SELECT at FROM submissions WHERE id=?", (e[4],)).fetchone()}
            got["submission at"] = got["submission at"] and got["submission at"][0]
            want = {"cut": r[2], "gen": 0, "actor": src_sub.get(submission, (None,))[0], "submission": submission, "at": latest,
                    "state": m["state"], "actor id": actor_id.get(src_sub[submission][0]) if earlier else m["actor"],
                    "submission at": src_sub[submission][1] if earlier else m["at"]}
            differ = [f"{k} {v!r} expected, {got[k]!r} set" for k, v in want.items() if got[k] != v]
            if differ:
                problems.append(f"{m['crop']} ({text}): mark event {'; '.join(differ)}")
        extra = {i for (i,) in atlas.execute("SELECT id FROM events WHERE legacy IS NULL")}
        problems += [f"event {i}: an event no source event names that sets no live mark" for i in sorted(extra - matched)]
        problems += [f"event {i}: a mark event that names a source event" for i in sorted(matched - extra)]
        recorded = OUT / "history-load" / f"{name}-marks" / "left"
        if recorded.exists() and int(recorded.read_text()) != left:
            problems.append(f"{left} marked crops not pending or retired, the load left {recorded.read_text()}")
        elif not recorded.exists() and report.strict:
            problems.append(f"no {recorded.relative_to(HERE)}, the count of marks the load left")
        report.check(f"every marked crop has its mark as its state ({left} not pending at the load keep theirs)", problems)

    t = Table(report, history, "favourites", listed)
    for r in stream("favourites", "user_id, unit, at"):
        cid = crop(r["unit"])
        if cid is None:
            t.account("crop unknown")
            continue
        t.compare(f"favourite {r['unit']} ({cid}) of {r['user_id']}", {"at": ms(r["at"])},
                  one("SELECT at FROM favourites WHERE user=? AND crop=?", r["user_id"], cid))
    t.done()

    # The claim ledger: crop evidence names its crop and cut; a claim on a crop takes cut -1 when
    # its evidence names it, else 0.
    t, named = Table(report, history, "assertion_evidence", listed), defaultdict(set)
    for r in stream("assertion_evidence", "assertion, kind, ref, locator"):
        cid = cut = None
        if r["kind"] == "crop":
            unit, cut = cuts.ref_cut(r["ref"])
            cid = unit and crop(unit)
            if cid is None:
                t.account("crop version unknown")
                continue
            named[r["assertion"]].add((cid, cut))
        t.compare(f"evidence {r['assertion']} {r['kind']} {r['ref']}", {"locator": r["locator"], "crop": cid, "cut": cut},
                  one("SELECT locator, crop, cut FROM assertion_evidence WHERE assertion=? AND kind=? AND ref=?",
                      r["assertion"], r["kind"], r["ref"]))
    t.done()

    t, claim_on, by_submission = Table(report, history, "assertions", listed), {}, defaultdict(set)
    for r in stream("assertions", "*"):
        want = dict(r)
        want.pop("_rowid", None)
        want["crop"] = want["cut"] = None
        if r["predicate"] in CROP_PREDICATES:
            want["crop"] = crop(r["subject"])
            if want["crop"] is None:
                t.account("crop unknown")
                continue
            want["cut"] = -1 if -1 in {c for i, c in named[r["id"]] if i == want["crop"]} else 0
        claim_on[r["id"]] = (want["crop"], want["cut"])
        if r["submission"]:
            by_submission[r["submission"]].add(r["id"])
        t.compare(f"assertion {r['id']}", want, one("SELECT * FROM assertions WHERE id=?", r["id"]))
    t.done()

    t = Table(report, history, "current_claims", listed)
    for r in stream("current_claims", "*"):
        want = dict(r)
        want.pop("_rowid", None)
        version = want.pop("crop_version")
        want["crop"] = want["cut"] = None
        if r["predicate"] in CROP_PREDICATES:
            want["crop"] = crop(r["subject"])
            if want["crop"] is None:
                t.account("crop unknown")
                continue
            unit, cut = cuts.ref_cut(version) if version else (r["subject"], 0)
            want["cut"] = cut if unit and crop(unit) == want["crop"] else None
        t.compare(f"current claim {r['subject']} {r['predicate']} {r['scope']} {r['slot']}", want,
                  one("SELECT * FROM current_claims WHERE subject=? AND predicate=? AND scope=? AND slot=?",
                      r["subject"], r["predicate"], r["scope"], r["slot"]))
    t.done()

    # A saved response on a crop names it by integer id and the cut its claims rest on, beside the old text id.
    for r in source("assertion_actions").execute("SELECT submission, assertion FROM assertion_actions WHERE submission IS NOT NULL"):
        by_submission[r[0]].add(r[1])
    t = Table(report, history, "ledger_submissions", listed)
    for r in stream("ledger_submissions", "*"):
        want = dict(r)
        want.pop("_rowid", None)
        want["response"] = json.loads(r["response"])
        cid = crop(want["response"].get("subject"))
        if cid is not None:
            cut = {claim_on[a][1] for a in by_submission[r["id"]] if claim_on.get(a, (None,))[0] == cid}
            want["response"].update(crop=cid, cut=max(cut) if cut else None)
        got = one("SELECT * FROM ledger_submissions WHERE id=?", r["id"])
        if got:
            got["response"] = json.loads(got["response"])
        t.compare(f"ledger_submissions {r['id']}", want, got)
    t.done()

    for name_, key in (("assertion_actions", "id"), ("forms", "id"), ("representations", "id")):
        t = Table(report, history, name_, listed)
        for r in stream(name_, "*"):
            want = dict(r)
            want.pop("_rowid", None)
            t.compare(f"{name_} {r[key]}", want, one(f'SELECT * FROM "{name_}" WHERE {key}=?', r[key]))
        t.done()

    # A form claim's name is its form's anchor, so every anchor must be a representation the move holds.
    report.check("every form's anchor names a representation", [
        f"form {f}: anchor {a}" for f, a in history.execute(
            "SELECT id, anchor FROM forms WHERE anchor IS NOT NULL AND anchor NOT IN (SELECT id FROM representations)")])

    # A form decision's `crops` are the glyphs of its `units` that still exist, in order.
    t = Table(report, history, "form_decisions", listed)
    for r in stream("form_decisions", "*"):
        want = dict(r)
        want.pop("_rowid", None)
        want["crops"] = [i for (i,) in mirror.execute("SELECT i.id FROM json_each(?) j JOIN ids i ON i.text = j.value "
                                                      "ORDER BY j.key", (r["units"],))]
        got = one("SELECT * FROM form_decisions WHERE seq=?", r["seq"])
        if got:
            got["crops"] = json.loads(got["crops"])
        t.compare(f"form decision {r['seq']}", want, got)
    t.done()

    t = Table(report, history, "written_forms", listed)
    for r in stream("written_forms", "*"):
        want = dict(r)
        want.pop("_rowid", None)
        want["crop"] = crop(want.pop("target"))
        if want["crop"] is None:
            t.account("crop unknown")
            continue
        t.compare(f"written form {r['id']}", want, one("SELECT * FROM written_forms WHERE id=?", r["id"]))
    t.done()

    t = Table(report, history, "assertion_premises", listed)
    for r in stream("assertion_premises", "assertion, premise, role"):
        t.compare(f"premise {r['assertion']} {r['premise']}", {"role": r["role"]},
                  one("SELECT role FROM assertion_premises WHERE assertion=? AND premise=?", r["assertion"], r["premise"]))
    t.done()

    t = Table(report, history, "rejections", listed)
    for r in stream("rejections", "submission, by, reason, at"):
        got = one("SELECT by, reason, at FROM rejections WHERE submission=?", sub_id.get(r["submission"]))
        t.compare(f"rejection of {r['submission']}", {"by": r["by"], "reason": r["reason"], "at": ms(r["at"])},
                  got if r["submission"] in sub_id else None)
    t.done()

    t = Table(report, history, "corpus_follow", listed)
    for r in stream("corpus_follow", "id"):
        cid = crop(r["id"])
        if cid is None:
            t.account("crop unknown")
            continue
        t.compare(f"followed {r['id']} ({cid})", {"crop": cid}, one("SELECT crop FROM corpus_follow WHERE crop=?", cid))
    t.done()

    # A followed glyph's first character, as a grapheme id.
    t = Table(report, history, "form_bases", listed)
    overlaid = {t_ for (t_,) in source("units").execute("SELECT id FROM units WHERE origin='corpus'")}
    released = releases_base(atlas, overlaid)
    upstream = dict(source("corpus_units").execute("SELECT id, character FROM corpus_units"))
    for r in stream("form_bases", "id, character"):
        cid = crop(r["id"])
        if cid is None:
            t.account("crop unknown")
            continue
        if released(cid, r["id"]):
            t.account("no form decision holds the glyph; a publish releases it")
            continue
        # A glyph no review overlay speaks for has its upstream character as its base, once a publish has
        # numbered it.
        character = char_text(r["character"]) or None
        if r["id"] in upstream and r["id"] not in overlaid and (now := char_text(upstream[r["id"]]) or None) and \
                atlas.execute("SELECT 1 FROM graphemes WHERE text=?", (now,)).fetchone():
            character = now
        found = atlas.execute("SELECT id FROM graphemes WHERE text=?", (character,)).fetchone() if character else None
        want = {"char": found[0] if found else None}
        t.compare(f"base of {r['id']} ({cid})", want, one("SELECT char FROM form_bases WHERE crop=?", cid))
    t.done()


    # Corpus glyphs the old site held a row for, numbered in the order of those rows, with any flag an
    # admin set on one.
    t, flags_t = Table(report, history, "corpus_rows", listed), Table(report, history, "corpus_flags", listed)
    # A keyset copy can hold a row twice; the copy at the later rowid is the row as it stands.
    corpus_units = {}
    for text_, data in source("units").execute("SELECT id, data FROM units WHERE origin='corpus' ORDER BY _rowid"):
        corpus_units.pop(text_, None)
        corpus_units[text_] = data
    # A row keeps the number its first load gave it; rows new since then follow, in rowid order.
    seqs = []
    for text_, data in corpus_units.items():
        cid = crop(text_)
        if cid is None:
            t.account("crop unknown")
            continue
        got = one("SELECT seq FROM corpus_rows WHERE crop=?", cid)
        t.compare(f"corpus row {text_} ({cid})", {"seq": got and got["seq"]}, got)
        if got:
            seqs.append(got["seq"])
        flag = json.loads(data).get("flag")
        if flag:
            flags_t.compare(f"flag on {text_} ({cid})", {"finding": flag["finding"], "realigned": flag.get("realigned"),
                            "transcribed": flag.get("transcribed"), "at": ms(flag["at"])},
                            one("SELECT finding, realigned, transcribed, at FROM corpus_flags WHERE crop=?", cid))
    t.done()
    flags_t.done()
    report.check("every corpus row has a number of its own",
                 [f"{len(seqs) - len(set(seqs))} corpus rows share a number"] if len(set(seqs)) != len(seqs) else [])

    # Script facts on a crop the mirror lacks are dropped; every other row keeps its values.
    for source_, target, cols in (("unit_suspects", "suspects", ("p", "reads_as", "label", "box")),
                                  ("unit_shapes", "shapes", ("shape_order",))):
        t = Table(report, history, source_, listed, target)
        for r in stream(source_, "id, " + ", ".join(cols)):
            cid = crop(r["id"])
            if cid is None:
                t.account("crop no longer exists")
                continue
            t.compare(f"{source_} {r['id']} ({cid})", {c: r[c] for c in cols},
                      one(f"SELECT {', '.join(cols)} FROM {target} WHERE crop=?", cid))
        t.done()

    # Each actor's totals as the ranking counts them: the reviews whose submission stands, the
    # submissions that stand, and the time of the latest submission.
    t = Table(report, history, "actor_stats", listed)
    reviews, rounds, last = Counter(), Counter(), {}
    verdicts = {c for c, k in kinds.items() if k in ("match", "wrong", "unsure")}
    for actor_, kind, submission in history.execute("SELECT actor, kind, submission FROM events"):
        if kind in verdicts and submission not in undone_ids:
            reviews[actor_] += 1
    for actor_, at, is_undone in history.execute("SELECT actor, at, undone FROM submissions"):
        rounds[actor_] += not is_undone
        last[actor_] = max(last.get(actor_, at), at)
    for actor_, key_ in actor_key.items():
        t.compare(f"stats of {key_}", {"reviews": reviews[actor_], "rounds": rounds[actor_], "at": last.get(actor_)},
                  one("SELECT reviews, rounds, at FROM actor_stats WHERE actor=?", actor_))
    t.done()

    # Earlier cuts: every crop a history row cites at cut -1 has that cut in its bundle entry, with the
    # earlier box crop_versions holds.
    cited = sorted({cid for table_ in ("events", "seen", "skips", "assertion_evidence", "assertions", "current_claims")
                    for (cid,) in history.execute(f"SELECT DISTINCT crop FROM {table_} WHERE cut = -1")})
    problems, published = [], 0
    for cid in cited:
        text = mirror.execute("SELECT text FROM ids WHERE id=?", (cid,)).fetchone()[0]
        earlier = cuts.versions[text] - {cuts.current(text)}
        e = entry(cid) or {}
        have = [box4(c.get("box")) for c in e.get("cuts", []) if c.get("cut") == -1]
        published += "cuts" in e
        if len(earlier) != 1 or have != list(earlier):
            problems.append(f"{text} ({cid}): bundle cut -1 {have}, crop_versions' earlier box {sorted(earlier)}")
    name_cuts = f"every crop cited at cut -1 has that cut in its bundle with its earlier box ({len(cited)} crops)"
    if cited and not published:
        report.skip(name_cuts, "no bundle has earlier cuts yet; publish history-cuts.sqlite (load_history.py writes it)")
    else:
        report.check(name_cuts, problems)

    # Accounts: every user an actor or favourite names has an account.
    accounts = HERE / "accounts.sqlite"
    where = "the local copy" if loaded else "history.sqlite"
    if not accounts.exists():
        report.skip("every actors.user and favourites.user names an account", "no accounts.sqlite")
    else:
        db, acc = atlas if loaded else history, sqlite3.connect(f"file:{accounts}?mode=ro", uri=True)
        problems = [f"{table_}.user {user}" for table_, column in (("actors", "key"), ("favourites", "crop"))
                    for _k, user in db.execute(f"SELECT {column}, user FROM {table_} WHERE user IS NOT NULL")
                    if not acc.execute('SELECT 1 FROM "user" WHERE id=?', (user,)).fetchone()]
        report.check(f"every actors.user and favourites.user in {where} names an account", problems)
        check_accounts(report, acc, query)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cutover", action="store_true", help="fail every check that cannot run yet instead of skipping it, "
                        "and read glyph-atlas-v3, glyph-atlas and glyph-atlas-accounts to compare them with the local copies")
    cutover = parser.parse_args().cutover
    report = Report(strict=cutover)
    name = translation()
    check_crops(report, name)
    check_history(report, name, remote if cutover else None)
    check_written(report, remote if cutover else None)
    path = OUT / f"check-{time.strftime('%Y%m%dT%H%M%S')}.txt"
    path.write_text("\n".join(report.lines) + "\n")
    print(f"{'all checks pass' if not report.failed else f'{report.failed} checks fail'}; report in {path}", flush=True)
    sys.exit(1 if report.failed else 0)


if __name__ == "__main__":
    main()
