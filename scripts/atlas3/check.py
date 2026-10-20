"""Check the new layout against a copy of the live tables, crop by crop, before the site switches to it.

    ATLAS3_DIR=work/atlas-v3 python3 scripts/atlas3/check.py

Reads the `source-*.sqlite` copies (`pull.py`) and the local copies the publisher keeps (`out/`), and
nothing remote: every publish is confirmed by its stamp in D1 and replayed on the local copy, so the
local copy is what D1 holds. Every check covers every crop; a failing one lists up to SHOWN examples
in `out/check-<stamp>.txt` and the run exits 1.

- Ids: every live crop, corpus glyph and retired crop has a number, and no number has two crops.
- Rows: each crop's character, state, style, withheld flag, production, category, visual group,
  revision, book, witness year and suspect flag equal its live record's; a corpus glyph's review
  columns come from its review overlay. Once load_history.py has set the marks, a pending crop with a
  live mark has the mark as its state, one revision on.
- Bundles: each collected crop's entry is its live record (less the fields its row holds), a corpus
  glyph's points at its live record, and a retired crop's is marked retired, names its replacement
  and has no row.
- Runs: the runs of two and three crops are the live ones, crop for crop, with their graphemes, hand
  and book.
- Images: every live image key is in the image key map, at its live place.

History (decision record 0006), when `out/history.sqlite` exists (history.py):
- Rows: every source row of the history tables is in it, every translated field equal to one derived
  again from the source copies (actor and submission keys, times in ms, crop through the mirror, cut
  from the live record's box and crop_versions), or left out for a reason history-report.txt lists
  as often; and it holds no other rows.
- Marks: recomputed from seen marks and skips by the old triggers' rules, they are the live
  `unit_marks`, which history-marks.json lists; after the load each marked crop that was pending has
  its mark as state, with an event setting it.
- Accounts: with `accounts.sqlite`, every `actors.user` and `favourites.user` names a user.
"""

from __future__ import annotations

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

SHOWN = 20


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


def applied_marks(atlas, name):
    """Crop -> mark of history-marks.json once load_history.py has set this translation's marks on the
    local copy, else None."""
    if name is None or not atlas.execute("SELECT 1 FROM imports WHERE stamp LIKE ? LIMIT 1", (f"{name}-marks.%",)).fetchone():
        return None
    return {m["crop"]: m["state"] for m in json.loads((OUT / "history-marks.json").read_text())}


class Report:
    def __init__(self):
        self.lines, self.failed = [], 0

    def check(self, name, problems):
        problems = list(problems)
        self.lines.append(("ok   " if not problems else f"FAIL ({len(problems)}) ") + name)
        self.lines += [f"     {p}" for p in problems[:SHOWN]]
        self.failed += bool(problems)
        print(self.lines[-1 - min(len(problems), SHOWN)], flush=True)

    def skip(self, name):
        self.lines.append(f"skip {name}")
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
                                           "book, year, flags FROM crops")}
    books = dict(atlas.execute("SELECT id, key FROM books"))
    book_ids = {key: bid for bid, key in books.items()}
    years = dict(source("document_dating").execute("SELECT document, start FROM document_dating WHERE axis='witness'"))
    redirects = dict(source("unit_redirects").execute("SELECT id, target FROM unit_redirects")) \
        if (HERE / "source-unit_redirects.sqlite").exists() else {}
    rev = dict(atlas.execute("SELECT id, rev FROM pages"))

    units = list(latest(source("units"), "units",
                        "id, origin, character, state, style, quiz, production, category, visual_group, revision, data, document"))
    corpus = list(latest(source("corpus_units"), "corpus_units", "id, character, production, style, visual_group, document, object, offset, size, label"))
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
                names["category"].get(r[7]), names["visual_group"].get(r[8]), r[9], books.get(r[10]), r[11], r[12] & 1)

    # A pending crop with a live mark takes the mark as its state, one revision on, once the marks are set.
    marks = applied_marks(atlas, name) or {}

    def marked(cid, w):
        return w[:2] + (marks[cid],) + w[3:8] + (w[8] + 1,) + w[9:] if w[2] == "pending" and cid in marks else w

    diffs = []
    for u in local:
        cid = id_of.get(u[0])
        w = marked(cid, want(*u[2:10], 0) + (u[11] or "unknown", years.get(u[11]), int(bool(json.loads(u[10]).get("suspect")))))
        if cid in rows and got(cid) != w:
            diffs.append(f"{u[0]} ({cid}): live {w}, new layout {got(cid)}")
    for c in corpus:
        cid, o = id_of.get(c[0]), overlay.get(c[0])
        if cid not in rows:
            continue
        book = c[5] or c[0].split("|")[0].split(":")[0]
        w = want((o[2] if o else None) or c[1], o[3] if o else "pending", (o[4] if o else None) or c[3], o[5] if o else 1,
                 c[2], None, c[4], o[9] if o else 0, 1) + (book, years.get(c[5]), 0)
        w, g = marked(cid, w), got(cid)
        # A corpus glyph has no category of its own.
        if g[:6] + g[7:] != w[:6] + w[7:]:
            diffs.append(f"{c[0]} ({cid}): live {w}, new layout {g}")
    report.check("every crop's row equals its live record", diffs)

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
            have = {k: v for k, v in e.items() if k not in ("slot", "id", "image_place", "context_image_place", "cuts")}
            if e.get("id") != u[0] or have != bundle_entry(data):
                differ = sorted(k for k in have.keys() | bundle_entry(data).keys() if have.get(k) != bundle_entry(data).get(k))
                wrong.append(f"{u[0]} ({cid}): bundle entry {e.get('id')} differs in {differ}")
    for u in sorted(retired, key=lambda u: id_of.get(u[0], -1)):
        cid = id_of.get(u[0])
        e = (entry(cid) or {}) if cid is not None else {}
        if cid is not None and (cid in rows or not e.get("retired") or e.get("id") != u[0] or e.get("replaced_by") != redirects.get(u[0])):
            wrong.append(f"{u[0]} ({cid}): retired on the live site; new layout row {cid in rows}, entry {e.get('id')} "
                         f"retired {e.get('retired')} replaced by {e.get('replaced_by')}, live {redirects.get(u[0])}")
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
    cut below the oldest recorded one. None for a crop with two earlier boxes."""

    def __init__(self):
        # The source copies have no indexes: the crops wanted go in a table here, and each source is
        # read once, joined to it.
        self.db = sqlite3.connect(":memory:", uri=True)
        for t in ("units", "crop_versions"):
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
        self.record = {}
        # The last copy of a record wins.
        for text, origin, box in self.db.execute("SELECT id, origin, json_extract(data, '$.box') FROM units.units "
                                                 "WHERE id IN (SELECT text FROM wanted) ORDER BY _rowid"):
            self.record[text] = (origin, box4(box))
        self.versions = defaultdict(set)
        for unit, box in self.db.execute("SELECT unit, box FROM crop_versions.crop_versions WHERE unit IN (SELECT text FROM wanted)"):
            if box4(box) is not None:
                self.versions[unit].add(box4(box))
        self.db.close()

    def cut(self, text, box):
        # A corpus glyph has no record of its own, or a review overlay with no box.
        origin, current = self.record.get(text, ("corpus", None))
        if origin == "corpus":
            return 0
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
    """One history table: each source row against its translated row, and the rows accounted for."""

    def __init__(self, report, history, name, listed):
        self.report, self.history, self.name, self.listed = report, history, name, listed
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
        for reason, n in self.gone.items():
            if n > self.listed[self.name][reason]:
                self.problems.append(f"{n} rows {reason}, history-report.txt lists {self.listed[self.name][reason]}")
        total = self.history.execute(f'SELECT count(*) FROM "{self.name}"').fetchone()[0]
        if extra and total != self.present:
            self.problems.append(f"{total} translated rows, {self.present} of them from a source row")
        gone = "; ".join(f"{n} {reason}" for reason, n in self.gone.items())
        self.report.check(f"every {self.name} row is translated" + (f" or accounted for ({gone})" if gone else ""), self.problems)


def check_history(report, name):
    path = OUT / "history.sqlite"
    if name is None:
        report.skip("history: no out/history.sqlite; run history.py first")
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

    cuts = Cuts()
    for t in ("seen", "skips", "events"):
        cuts.want(r[0] for r in source(t).execute(f"SELECT DISTINCT target FROM {t}"))
    cuts.refer(r[0] for r in source("assertion_evidence").execute("SELECT ref FROM assertion_evidence WHERE kind='crop'"))
    cuts.refer(r[0] for r in source("current_claims").execute("SELECT crop_version FROM current_claims WHERE crop_version IS NOT NULL"))
    cuts.load()

    # Actors: the source rows; actors only other tables name have no source row.
    t = Table(report, history, "actors", listed)
    for r in stream("actors", "actor, user_id, via, at"):
        t.compare(f"actor {r['actor']}", {"user": r["user_id"], "via": r["via"], "at": ms(r["at"])},
                  one("SELECT user, via, at FROM actors WHERE key=?", r["actor"]))
    t.done(extra=False)

    t, undone = Table(report, history, "submissions", listed), set()
    for r in stream("submissions", "id, actor, request, response, at, undone"):
        if r["undone"]:
            undone.add(r["id"])
        got = one("SELECT actor, request, response, at, undone FROM submissions WHERE key=?", r["id"])
        if got:
            got["actor"] = actor_key.get(got["actor"])
        t.compare(f"submission {r['id']}", {"actor": r["actor"], "request": r["request"], "response": r["response"],
                                            "at": ms(r["at"]), "undone": r["undone"]}, got)
    t.done()

    # Events: a crop's revisions follow expected_revision, or time where two share one.
    t = Table(report, history, "events", listed)
    for target, rows_ in itertools.groupby(stream("events", "id, submission, target, actor, expected_revision, before_data, "
                                                  "after_data, event, at", "target"), key=lambda r: r["target"]):
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
                    "text": evidence.get("suggested_text") or evidence.get("suggested_reading") or None}
            got = one("SELECT * FROM events WHERE crop=? AND revision=?", cid, revision)
            if got:
                got.update(actor=actor_key.get(got["actor"]), submission=sub_key.get(got["submission"]), kind=kinds.get(got["kind"]),
                           label=graphemes.get(got["label"]), state=states.get(got["state"]), issue=issues.get(got["issue"]),
                           box=(got["x"], got["y"], got["w"], got["h"]))
            t.compare(f"event {r['id']} on {target} ({cid})", want, got)
    t.done()

    # Seen marks and skips, and the marks the old triggers would give: the crop's current box, a
    # round not undone, two or more skippers for `hard`.
    t, marked = Table(report, history, "seen", listed), set()
    for r in stream("seen", "target, submission, box, at"):
        cid = crop(r["target"])
        if cid is None:
            t.account("crop unknown")
            continue
        cut = cuts.cut(r["target"], r["box"])
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
    problems = []
    for target, mark in source("unit_marks").execute("SELECT id, mark FROM unit_marks"):
        if (r := recomputed.pop(target, None)) != mark:
            problems.append(f"{target}: live {mark}, recomputed {r}")
    problems += [f"{target}: recomputed {mark}, no live mark" for target, mark in recomputed.items()]
    report.check("the marks recomputed from seen marks and skips are the live ones", problems)

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
    if applied_marks(atlas, name) is None:
        report.skip("every marked crop has its mark as its state: the local copy has not had this translation's marks "
                    "applied (no marks stamp in imports); load_history.py applies them, then check again")
    else:
        code = {key: c for c, key in kinds.items()}
        problems, left = [], 0
        for m in marks:
            r = atlas.execute("SELECT state, revision FROM crops WHERE id=?", (m["crop"],)).fetchone()
            state = r and states.get(r[0])
            if r is None or state not in ("pending", "seen", "hard"):
                left += 1
            elif state != m["state"]:
                problems.append(f"{m['crop']}: state {state}, marked {m['state']}")
            elif not atlas.execute("SELECT 1 FROM events WHERE crop=? AND revision=? AND kind=?",
                                   (m["crop"], r[1], code[m["state"]])).fetchone():
                problems.append(f"{m['crop']}: state {state} with no event setting it at revision {r[1]}")
        recorded = OUT / "history-load" / f"{name}-marks" / "left"
        if recorded.exists() and int(recorded.read_text()) != left:
            problems.append(f"{left} marked crops not pending or retired, the load left {recorded.read_text()}")
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

    t = Table(report, history, "assertions", listed)
    for r in stream("assertions", "*"):
        want = dict(r)
        want.pop("_rowid", None)
        want["crop"] = want["cut"] = None
        if r["predicate"] == "has_form":
            want["crop"] = crop(r["subject"])
            if want["crop"] is None:
                t.account("crop unknown")
                continue
            want["cut"] = -1 if -1 in {c for i, c in named[r["id"]] if i == want["crop"]} else 0
        t.compare(f"assertion {r['id']}", want, one("SELECT * FROM assertions WHERE id=?", r["id"]))
    t.done()

    t = Table(report, history, "current_claims", listed)
    for r in stream("current_claims", "*"):
        want = dict(r)
        want.pop("_rowid", None)
        version = want.pop("crop_version")
        want["crop"] = want["cut"] = None
        if r["predicate"] == "has_form":
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

    for name_, key in (("assertion_actions", "id"), ("ledger_submissions", "id"), ("forms", "id")):
        t = Table(report, history, name_, listed)
        for r in stream(name_, "*"):
            want = dict(r)
            want.pop("_rowid", None)
            t.compare(f"{name_} {r[key]}", want, one(f'SELECT * FROM "{name_}" WHERE {key}=?', r[key]))
        t.done()

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

    # Accounts: every user an actor or favourite names has an account.
    accounts = HERE / "accounts.sqlite"
    loaded = atlas.execute("SELECT 1 FROM imports WHERE stamp LIKE ? LIMIT 1", (f"{name}.rows-%",)).fetchone()
    where = "the local copy" if loaded else "history.sqlite"
    if not accounts.exists():
        report.skip("every actors.user and favourites.user names an account: no accounts.sqlite")
    else:
        db, acc = atlas if loaded else history, sqlite3.connect(f"file:{accounts}?mode=ro", uri=True)
        problems = [f"{table_}.user {user}" for table_, column in (("actors", "key"), ("favourites", "crop"))
                    for _k, user in db.execute(f"SELECT {column}, user FROM {table_} WHERE user IS NOT NULL")
                    if not acc.execute('SELECT 1 FROM "user" WHERE id=?', (user,)).fetchone()]
        report.check(f"every actors.user and favourites.user in {where} names an account", problems)


def main():
    report = Report()
    name = translation()
    check_crops(report, name)
    check_history(report, name)
    path = OUT / f"check-{time.strftime('%Y%m%dT%H%M%S')}.txt"
    path.write_text("\n".join(report.lines) + "\n")
    print(f"{'all checks pass' if not report.failed else f'{report.failed} checks fail'}; report in {path}", flush=True)
    sys.exit(1 if report.failed else 0)


if __name__ == "__main__":
    main()
