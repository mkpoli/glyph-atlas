"""Translate the old database's review history, claims and crop facts into the new layout (decision record 0006).

    ATLAS3_DIR=work/atlas-v3 python3 scripts/atlas3/history.py

Reads the `source-*.sqlite` copies (`pull.py`) and the publisher's local copies (`out/mirror.sqlite`,
`out/atlas.sqlite`, `out/pages/`), never writes them, and writes:

- `out/history.sqlite`: the history, representation, undo and people tables, with `events`, `skips` and `codes` from
  `0001_init.sql`, holding the translated rows;
- `out/history-cuts.json`: per crop the old site recorded versions of, when it first recorded the current one
  (`cut_at`), its earlier version (cut -1) and the other encodings of its current cut, for the publisher to
  put in its bundle;
- `out/history-tombstones.json`: crops an event names that the mirror lacks, for the publisher to number;
- `out/history-marks.json`: the live crop marks, with the seen row each one rests on;
- `out/history-report.txt` (and stdout): rows in, out, dropped by rule and stopped, per table.

A claim on a crop (a predicate whose catalogue subject is a crop) takes the crop and cut beside its
subject, and a saved claim response on a crop names it by integer id and cut. A crop's text id
resolves only through the mirror, never through a redirect. A row that cannot be translated stops
the move: it is counted, up to SHOWN are listed, and the run exits 1.

Where the old events keep what (all 1,690 of 19-20 October 2026 are kind 'review'):
- the verdict, issue and note are top-level fields of the event JSON's `evidence` (a JSON string);
  `evidence.request` repeats them for most reviews, and quiz rounds carry them per answer too;
- `after_data` is the crop's record after the review: `label` and `state` always, never `style` or
  `quiz`. A review sets no style and no withheld flag, so an event's `style` is NULL; no event records
  quiz;
- `before_data.box` is the box the reviewer saw, which names the event's cut;
- the label the reviewer was shown is `evidence.label` in a round and `evidence.snapshot.character.label`
  otherwise (`shown`), the character named is `evidence.suggested_character` (`named`), and the reading
  the crop showed, in records from before readings left them, is `evidence.snapshot.character.reading`,
  and the reading the review saved, as its correction names it, `evidence.correction.reading`.

Corpus glyphs the old site held a `units` row for become `corpus_rows`, numbered in rowid order, and
the finding of an admin's flag in a record's `flag` becomes a `corpus_flags` row.
"""

from __future__ import annotations

import gzip
import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime

from build import HERE, OUT, PAGE, ROOT, char_text

SHOWN = 20
SCHEMA = ROOT / "apps/cloudflare/migrations-atlas"
# The predicates whose subject is a crop, as the ledger's catalogue names them: their claims take the
# crop and cut beside their subject.
CROP_PREDICATES = frozenset(p for p, spec in json.loads((ROOT / "data/ledger.json").read_text())["predicates"].items()
                            if spec["subject"] == "crop")
# The migrations whose tables hold translated rows: the history, and the representations forms name.
MIGRATIONS = ("0003_history.sql", "0008_representations.sql", "0009_undo.sql")


def ms(value):
    """ISO time -> Unix milliseconds."""
    if value is None:
        return None
    return round(datetime.fromisoformat(value).timestamp() * 1000)


def legacy_issue(issue):
    """An issue as the old site's crops name it since its migration 0055: `reading` is `character`."""
    return "character" if issue == "reading" else issue or None


def box_of(value):
    """A box as an integer tuple (x, y, w, h), from JSON {"x":..}, a dict, or "x,y,w,h"."""
    if value is None or value == "":
        return None
    if isinstance(value, str):
        value = json.loads(value) if value.lstrip().startswith("{") else value.split(",")
    if isinstance(value, dict):
        value = [value.get(k) for k in "xywh"]
    if len(value) != 4 or any(v is None for v in value):
        return None
    return tuple(round(float(v)) for v in value)


def source(table):
    db = sqlite3.connect(f"file:{HERE / f'source-{table}.sqlite'}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    return db


def rows(table, key, order=None):
    """A source table's rows; a keyset copy can hold a row twice, and the last copy wins."""
    db = source(table)
    cols = [r[1] for r in db.execute(f'PRAGMA table_info("{table}")')]
    where = f' WHERE _rowid IN (SELECT max(_rowid) FROM "{table}" GROUP BY {key})' if "_rowid" in cols else ""
    return list(db.execute(f'SELECT * FROM "{table}"{where}' + (f" ORDER BY {order}" if order else "")))


class Report:
    def __init__(self):
        self.tables, self.notes, self.stopped = {}, [], 0

    def table(self, name, n_in):
        t = self.tables[name] = {"in": n_in, "out": 0, "dropped": Counter(), "stopped": Counter(), "examples": []}
        return t

    def drop(self, name, reason, n=1):
        self.tables[name]["dropped"][reason] += n

    def stop(self, name, reason, example):
        t = self.tables[name]
        t["stopped"][reason] += 1
        self.stopped += 1
        if len(t["examples"]) < SHOWN:
            t["examples"].append(f"{reason}: {example}")

    def note(self, line, examples=()):
        self.notes.append(line)
        self.notes += [f"     {e}" for e in list(examples)[:SHOWN]]

    def lines(self):
        out = []
        for name, t in self.tables.items():
            dropped = sum(t["dropped"].values())
            stopped = sum(t["stopped"].values())
            out.append(f"{name}: in {t['in']}, out {t['out']}, dropped {dropped}, stopped {stopped}")
            out += [f"     dropped ({n}) {reason}" for reason, n in t["dropped"].items() if n]
            out += [f"     stopped ({n}) {reason}" for reason, n in t["stopped"].items()]
            out += [f"       {e}" for e in t["examples"]]
        out += ["", "notes:"] + [f"  {n}" for n in self.notes]
        out.append("")
        out.append(f"{self.stopped} rows stop the move" if self.stopped else "no row stops the move")
        return out


def statements(path):
    return [s.strip() for s in re.split(r";\s*\n", path.read_text()) if s.strip()]


def create(db):
    """Create the base event tables, the history and form anchors, and the undo and people fields."""
    for s in statements(SCHEMA / "0001_init.sql"):
        body = "\n".join(line for line in s.splitlines() if not line.startswith("--"))
        m = re.match(r"CREATE (?:TABLE (\w+)|INDEX \w+ ON (\w+))", body)
        if m and (m[1] or m[2]) in ("events", "skips", "codes", "actor_stats"):
            db.execute(body)
    db.executescript((SCHEMA / "0013_forms.sql").read_text())
    for migration in MIGRATIONS:
        db.executescript((SCHEMA / migration).read_text())
    for s in statements(SCHEMA / "0010_people.sql"):
        body = "\n".join(line for line in s.splitlines() if not line.startswith("--"))
        m = re.match(r"CREATE (?:UNIQUE )?INDEX \w+ ON (\w+)", body)
        # An index on crops belongs to the layout, which this file does not hold.
        if not (m and not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (m[1],)).fetchone()):
            db.execute(body)


# The columns of a translated event, in the order history rows give them.
EVENT_COLUMNS = ("id", "crop", "revision", "cut", "gen", "actor", "submission", "at", "kind", "label", "state", "style", "quiz",
                 "x", "y", "w", "h", "note", "issue", "text", "reverses", "was_state", "was_issue", "was_x", "was_y",
                 "was_w", "was_h", "kept_issue", "shown", "named", "reading", "saved_reading", "legacy")


class Crops:
    """Integer ids, kinds, revisions and cuts of the crops history names."""

    def __init__(self, report):
        self.report = report
        self.mirror = sqlite3.connect(f"file:{OUT / 'mirror.sqlite'}?mode=ro", uri=True)
        self.atlas = sqlite3.connect(f"file:{OUT / 'atlas.sqlite'}?mode=ro", uri=True)
        self.ids = dict(self.mirror.execute("SELECT text, id FROM ids"))
        self.rev = dict(self.atlas.execute("SELECT id, rev FROM pages"))
        self.page_keys = dict(self.atlas.execute("SELECT id, key FROM pages"))
        self.current, self.pixels, self.earlier, self.versions, self.cut_at = {}, {}, {}, {}, {}
        self.overlay_box, self.encodings, self.text_of = {}, {}, {}

    def row(self, cid):
        return self.atlas.execute("SELECT kind, revision FROM crops WHERE id=?", (cid,)).fetchone()

    def corpus(self, cid):
        r = self.row(cid)
        return r is not None and r[0] == 1

    def load_cuts(self):
        """Each crop's recorded versions (crop_versions) against its current one: a collected crop's bundle
        entry box and image checksum, a corpus glyph's review overlay record (its published record is in a
        pack). A version under another box is the crop's earlier cut, -1. One under the current box with
        other pixels is another encoding of the current cut, which its version id still names
        (`encodings`). `cut_at` is when the old site first recorded the current version."""
        by_unit, no_box, unknown = defaultdict(list), Counter(), Counter()
        rows = source("crop_versions").execute("SELECT id, unit, pixels, box, image, at FROM crop_versions").fetchall()
        table = self.report.table("crop_versions", len(rows))
        # Every version becomes a cut or a version id evidence can name; none is written as a row.
        table["out"] = len(rows)
        for r in rows:
            cid = self.ids.get(r["unit"])
            if cid is None:
                unknown[r["unit"]] += 1
                continue
            # A corpus glyph's version has no box; evidence on it still names its version id.
            self.versions[r["id"]] = cid
            if box_of(r["box"]) is None:
                no_box["corpus glyph" if self.corpus(cid) else "collected crop"] += 1
                continue
            by_unit[cid].append((r["id"], box_of(r["box"]), r["pixels"], r["image"], r["at"]))
            self.text_of[cid] = r["unit"]
        self.report.note(f"crop_versions: rows with no box {dict(no_box)}; "
                         f"{sum(unknown.values())} rows of {len(unknown)} crops the mirror lacks", sorted(unknown)[:SHOWN])
        for pid in sorted({cid // PAGE for cid in by_unit}):
            path = OUT / "pages" / f"{pid}-{self.rev.get(pid)}.json.gz"
            entries = json.loads(gzip.decompress(path.read_bytes()))["crops"] if path.exists() else []
            for e in entries:
                cid = pid * PAGE + e["slot"]
                if cid in by_unit and box_of(e.get("box")) is not None:
                    self.current[cid] = box_of(e["box"])
                    self.pixels[cid] = e.get("image_sha256")
        # A corpus glyph the old site recorded versions of has a review overlay, whose record they name.
        text_of = {cid: self.text_of[cid] for cid in by_unit if cid not in self.current and self.corpus(cid)}
        if text_of:
            want = {t: cid for cid, t in text_of.items()}
            marks = ",".join("?" * len(want))
            for text, data in source("units").execute(
                    f"SELECT id, data FROM units WHERE origin='corpus' AND id IN ({marks}) ORDER BY _rowid", list(want)):
                d = json.loads(data)
                if box_of(d.get("box")) is not None:
                    self.overlay_box[want[text]] = box_of(d["box"])
                    self.pixels[want[text]] = d.get("image_sha256") or d.get("source_revision")
        no_current, many, orphan = [], [], []
        for cid, versions in by_unit.items():
            current = self.current.get(cid, self.overlay_box.get(cid))
            if current is None:
                no_current.append(cid)
                continue
            earlier = [v for v in versions if v[1] != current]
            here = [v for v in versions if v[1] == current]
            # A current version with no checksum is named by its box alone.
            now = here if self.pixels.get(cid) is None else [v for v in here if v[2] == self.pixels[cid]]
            others = [v for v in here if v not in now]
            times = [v[4] for v in now if v[4]]
            if times:
                self.cut_at[cid] = min(times)
            if others:
                self.encodings[cid] = [(v[2], v[3], v[4]) for v in sorted(others, key=lambda v: v[4] or "")]
            if len({v[1] for v in earlier}) > 1:
                many.append(cid)
            elif earlier:
                self.earlier[cid] = earlier[0]
                if len(earlier) == len(versions):
                    orphan.append(cid)
        self.report.note(f"crops with an earlier version: {len(self.earlier)}; with another encoding of their current "
                         f"cut: {len(self.encodings)}; with the time their current version was recorded: {len(self.cut_at)}")
        self.report.note(f"crop_versions crops with a box that neither their bundle entry nor a review overlay has: "
                         f"{len(no_current)}", no_current[:SHOWN])
        self.report.note(f"crops whose only version differs from their bundle box (cut -1 with no cut 0 row): {len(orphan)}", orphan)
        self.many = set(many)
        # Cut -1 holds one earlier box; a crop with two needs a rule before it can move.
        for cid in many:
            self.report.stop("crop_versions", "more than one earlier box", str(cid))

    def box(self, cid):
        """The crop's current box, from its bundle (crops crop_versions does not name are read here)."""
        if cid not in self.current:
            pid = cid // PAGE
            path = OUT / "pages" / f"{pid}-{self.rev.get(pid)}.json.gz"
            for e in json.loads(gzip.decompress(path.read_bytes()))["crops"] if path.exists() else []:
                self.current.setdefault(pid * PAGE + e["slot"], box_of(e.get("box")))
        return self.current.get(cid)

    def cut(self, cid, box):
        """Cut 0 for the current box (and every corpus glyph), -1 for the earlier one, else None. Another
        encoding under the current box is the current cut."""
        if self.corpus(cid):
            return 0
        box = box_of(box)
        if box is not None and box == self.box(cid):
            return 0
        if cid in self.many:
            return None
        if cid in self.earlier and box == self.earlier[cid][1]:
            return -1
        return None

    def older(self, cid):
        """The cut below the oldest recorded one: a box no recorded version has belongs to a version
        from before crop_versions kept them. None for a crop with more than one earlier version, whose
        cuts are not numbered: that stops the move."""
        if cid in self.many:
            return None
        return -2 if cid in self.earlier else -1

    def ref_cut(self, ref):
        """Crop evidence `id@pixels@box` -> (crop, cut) through the crop_versions row of that id."""
        cid = self.versions.get(ref)
        if cid is None:
            return None, None
        return cid, self.cut(cid, ref.rsplit("@", 1)[1])


def main():
    report = Report()
    crops = Crops(report)
    crops.load_cuts()
    path = OUT / "history.sqlite"
    path.unlink(missing_ok=True)
    out = sqlite3.connect(path)
    create(out)

    def insert(table, values):
        if values:
            out.executemany(f'INSERT INTO "{table}" VALUES({",".join("?" * len(values[0]))})', values)

    def cols(table):
        return [r[1] for r in out.execute(f'PRAGMA table_info("{table}")')]

    # Source rows.
    src = {t: rows(t, k, o) for t, k, o in [
        ("submissions", "id", "at, id"), ("events", "id", "at, id"), ("seen", "target, submission", None),
        ("skips", "target, submission", None), ("actors", "actor", None), ("favourites", "user_id, unit", None),
        ("assertions", "id", None), ("assertion_actions", "id", None),
        ("assertion_evidence", "assertion, kind, ref", None), ("assertion_premises", "assertion, premise", None),
        ("current_claims", "subject, predicate, scope, slot", None), ("ledger_submissions", "id", None),
        ("rejections", "submission", None), ("forms", "id", None), ("representations", "id", None),
        ("form_decisions", "seq", None),
        ("corpus_follow", "id", None), ("form_bases", "id", None), ("written_forms", "id", None), ("unit_suspects", "id", None),
        ("unit_shapes", "id", None), ("unit_marks", "id", None)]}

    # Actors: every actor text any table names, in key order.
    keys = {r["actor"] for t in ("actors", "submissions", "events", "skips", "form_decisions", "ledger_submissions",
                                 "assertion_actions") for r in src[t]}
    keys |= {r["asserted_by"] for r in src["assertions"]}
    known = {r["actor"]: r for r in src["actors"]}
    actor = {k: i for i, k in enumerate(sorted(keys), 1)}
    report.table("actors", len(src["actors"]))
    insert("actors", [(i, k, known[k]["user_id"] if k in known else None, known[k]["via"] if k in known else None,
                       ms(known[k]["at"]) if k in known else None) for k, i in actor.items()])
    report.tables["actors"]["out"] = len(actor)
    report.note(f"actors: {len(actor)} actor texts, {len(actor.keys() - known.keys())} with no source actors row")

    # Submissions, in (at, key) order.
    report.table("submissions", len(src["submissions"]))
    subs = sorted(src["submissions"], key=lambda r: (ms(r["at"]), r["id"]))
    sub = {r["id"]: i for i, r in enumerate(subs, 1)}
    undone = {sub[r["id"]] for r in subs if r["undone"]}
    sub_actor = {sub[r["id"]]: actor[r["actor"]] for r in subs}
    sub_at = {sub[r["id"]]: ms(r["at"]) for r in subs}
    insert("submissions", [(sub[r["id"]], r["id"], actor[r["actor"]], r["request"], r["response"], ms(r["at"]), r["undone"])
                           for r in subs])
    report.tables["submissions"]["out"] = len(subs)

    # Events.
    atlas_codes = {f: dict(crops.atlas.execute("SELECT key, code FROM codes WHERE field=?", (f,))) for f in ("state", "style")}
    graphemes = dict(crops.atlas.execute("SELECT text, id FROM graphemes"))
    report.table("events", len(src["events"]))
    parsed, tombstones = [], {}
    for r in src["events"]:
        before, after = json.loads(r["before_data"]), json.loads(r["after_data"])
        evidence = json.loads(json.loads(r["event"])["evidence"])
        cid = crops.ids.get(r["target"])
        if cid is None:
            if r["target"] not in tombstones:
                where = before.get("source") if isinstance(before.get("source"), dict) else {}
                page_id = before.get("page_id") or where.get("page_id")
                document = before.get("document") or where.get("document_id")
                # The record after the review: a tombstone shows the crop as it was last reviewed.
                tombstones[r["target"]] = {"text_id": r["target"], "page_key": page_id or f"book:{document}", "record": after,
                                           "document": document}
                if not before.get("page_id") and not before.get("document"):
                    report.note(f"tombstone {r['target']}: before_data has no page_id or document; page key "
                                f"{tombstones[r['target']]['page_key']} from before_data.source")
            # The event waits for its crop's number: publish history-tombstones.sqlite, then run again.
            report.stop("events", "tombstone not yet numbered", f"{r['id']} on {r['target']}")
            continue
        parsed.append((r, cid, before, after, evidence))
    # The kind and issue codes 0003 numbers.
    codes = {f: dict(out.execute("SELECT key, code FROM codes WHERE field=?", (f,))) for f in ("kind", "issue")}

    # Revisions: expected_revision + 1, unless a crop has two events at one revision.
    by_crop = defaultdict(list)
    for p in parsed:
        by_crop[p[1]].append(p)
    revision, resequenced = {}, []
    for cid, events in by_crop.items():
        wanted = [p[0]["expected_revision"] + 1 for p in events]
        if len(set(wanted)) < len(wanted):
            resequenced.append(cid)
            wanted = range(1, len(events) + 1)
            events = sorted(events, key=lambda p: (ms(p[0]["at"]), p[0]["id"]))
        for p, n in zip(events, wanted):
            revision[p[0]["id"]] = n
    report.note(f"events: {len(resequenced)} crops with two events at one expected revision, numbered by time instead",
                resequenced)
    retired_revision = {}
    unrowed = {p[1] for p in parsed if crops.row(p[1]) is None}
    if unrowed:
        # A retired crop has no row; its revision is its last source record's.
        texts = sorted(t for t, i in crops.ids.items() if i in unrowed)
        for text, revision_now in source("units").execute(
                f"SELECT id, revision FROM units WHERE id IN ({','.join('?' * len(texts))}) ORDER BY _rowid", texts):
            retired_revision[crops.ids[text]] = revision_now
        report.note(f"events: {len(unrowed)} crops with no crops row (retired); revision checked against the source units row")

    # Events are numbered in the order the old site wrote them: by time, and within one submission in
    # the order its request lists the crops (a round's answers, a batch's crops), as its rows went in.
    listed = {}
    for r in subs:
        input_ = json.loads(r["request"]).get("input") or {}
        crops_ = input_.get("answers") if isinstance(input_.get("answers"), list) else input_.get("crops")
        listed[r["id"]] = {c.get("id"): i for i, c in enumerate(crops_ or []) if isinstance(c, dict)}
    parsed.sort(key=lambda p: (ms(p[0]["at"]), sub.get(p[0]["submission"], 0),
                               listed.get(p[0]["submission"], {}).get(p[0]["target"], 0)))
    events, unrecorded = [], 0
    for r, cid, before, after, evidence in parsed:
        problems = []
        sub_id = sub.get(r["submission"])
        if sub_id is None:
            problems.append(f"submission {r['submission']} unknown")
        current = crops.row(cid)
        current_revision = current[1] if current else retired_revision.get(cid)
        # A tombstone with no record left on the old site has no revision to stay below.
        if current_revision is not None and revision[r["id"]] > current_revision:
            problems.append(f"revision {revision[r['id']]} above the crop's {current_revision}")
        cut = crops.cut(cid, before.get("box"))
        if cut is None:
            cut, unrecorded = crops.older(cid), unrecorded + 1
            if cut is None:
                problems.append("box matches no cut of a crop with more than one earlier version")
        label = char_text(after.get("label")) or None
        if label is not None and label not in graphemes:
            problems.append(f"label {label!r} has no grapheme")
        # The label the reviewer was shown: a round's in `evidence.label`, a single review's in its snapshot.
        shown = char_text(evidence.get("label") or ((evidence.get("snapshot") or {}).get("character") or {}).get("label")) or None
        if shown is not None and shown not in graphemes:
            problems.append(f"shown label {shown!r} has no grapheme")
        named = char_text(evidence.get("suggested_character")) or None
        # The reading the crop showed, while records still carried one.
        reading = ((evidence.get("snapshot") or {}).get("character") or {}).get("reading") or None
        if named is not None and named not in graphemes:
            problems.append(f"named character {named!r} has no grapheme")
        state = atlas_codes["state"].get(after.get("state"))
        if state is None:
            problems.append(f"state {after.get('state')!r} has no code")
        verdict = codes["kind"].get(evidence.get("verdict"))
        # The issue as the reviewer recorded it: the export hands `reading` on as it was chosen. Migration 0055
        # of the old site renamed the crops' `reading` to `character`, so what the crop held reads it so.
        issue = evidence.get("issue") or None
        if issue is not None and issue not in codes["issue"]:
            problems.append(f"issue {issue!r} has no code")
        if verdict is None:
            problems.append(f"verdict {evidence.get('verdict')!r} missing")
        box = box_of(after.get("box")) or (None,) * 4
        if problems:
            for p in problems:
                report.stop("events", p.split(" ")[0], f"{r['id']} on {r['target']} ({cid}): {p}")
            continue
        events.append((cid, revision[r["id"]], cut, 0, actor[r["actor"]], sub_id, ms(r["at"]), verdict,
                       graphemes.get(label) if label else None, state, None, after.get("quiz"), *box,
                       (evidence.get("note") or None) and evidence["note"][:2000],
                       codes["issue"].get(issue),
                       # Typed characters: `suggested_text`, or `suggested_reading` in reviews saved before it.
                       evidence.get("suggested_text") or evidence.get("suggested_reading") or None,
                       # What an undo restores (0009): no event reverses another yet; the state, issue and
                       # redrawn box before the review.
                       None, atlas_codes["state"].get(before.get("state")), codes["issue"].get(legacy_issue(before.get("issue"))),
                       *((box_of(before.get("box")) if before.get("box_pending") else None) or (None,) * 4),
                       # The issue a batch left a reported crop with: the event's answer names the character.
                       (codes["issue"].get(legacy_issue(after.get("issue")))
                        if legacy_issue(after.get("issue")) not in (None, legacy_issue(issue)) else None),
                       graphemes.get(shown) if shown else None, graphemes.get(named) if named else None, reading,
                       (evidence.get("correction") or {}).get("reading"), r["id"]))
    out.executemany(f'INSERT INTO events({",".join(EVENT_COLUMNS)}) VALUES({",".join("?" * len(EVENT_COLUMNS))})',
                    [(i, *e) for i, e in enumerate(events, 1)])
    report.tables["events"]["out"] = len(events)
    report.note("events: a review sets no style and no withheld flag, so both are NULL")
    report.note(f"events: codes {json.dumps(codes, ensure_ascii=False)}")

    # Seen marks and skips map to the cut whose box they name.
    report.table("seen", len(src["seen"]))
    seen = []
    for r in src["seen"]:
        cid = crops.ids.get(r["target"])
        cut = crops.cut(cid, r["box"]) if cid is not None else None
        if cid is not None and cut is None:
            cut, unrecorded = crops.older(cid), unrecorded + 1
        if cid is None or cut is None or r["submission"] not in sub:
            report.stop("seen", "crop unknown" if cid is None else "submission unknown" if r["submission"] not in sub
                        else "box matches no cut", f"{r['target']} ({cid}) {r['box']} image {r['image_sha256']} in "
                        f"{r['submission']} at {r['at']}; current box {crops.box(cid) if cid else None}")
            continue
        seen.append((sub[r["submission"]], cid, cut, ms(r["at"])))
    insert("seen", seen)
    report.tables["seen"]["out"] = len(seen)

    report.table("skips", len(src["skips"]))
    all_skips, newest = [], {}
    for r in src["skips"]:
        cid = crops.ids.get(r["target"])
        cut = crops.cut(cid, r["box"]) if cid is not None else None
        if cid is not None and cut is None:
            cut, unrecorded = crops.older(cid), unrecorded + 1
        if cid is None or cut is None or r["submission"] not in sub:
            report.stop("skips", "crop unknown" if cid is None else "submission unknown" if r["submission"] not in sub
                        else "box matches no cut", f"{r['target']} ({cid}) {r['box']} image {r['image_sha256']} in "
                        f"{r['submission']} at {r['at']}; current box {crops.box(cid) if cid else None}")
            continue
        s = (cid, actor[r["actor"]], cut, ms(r["at"]), sub[r["submission"]])
        all_skips.append(s)
        if (cid, s[1]) not in newest or s[3] > newest[(cid, s[1])][3]:
            newest[(cid, s[1])] = s
    report.drop("skips", "an older skip by the same actor", len(all_skips) - len(newest))
    report.note(f"{unrecorded} events, seen marks and skips name a box no recorded version has: "
                "the cut below the oldest recorded one")
    insert("skips", list(newest.values()))
    report.tables["skips"]["out"] = len(newest)

    # Marks: recomputed by the old triggers' rules, compared with the live ones.
    skippers = defaultdict(set)
    for cid, a, cut, _at, s in all_skips:
        if cut == 0 and s not in undone:
            skippers[cid].add(a)
    # A mark rests on the latest round that showed the crop; of rounds at one time, the highest
    # submission id, as load_history.py picks it.
    last_seen = {}
    for s, cid, cut, at in seen:
        if cut == 0 and s not in undone and (cid not in last_seen or (at, s) > last_seen[cid]):
            last_seen[cid] = (at, s)
    recomputed = {cid: "hard" for cid, a in skippers.items() if len(a) >= 2}
    recomputed.update({cid: "seen" for cid in last_seen if cid not in recomputed})
    report.table("unit_marks", len(src["unit_marks"]))
    live, marks = {}, []
    for r in src["unit_marks"]:
        cid = crops.ids.get(r["id"])
        if cid is None:
            report.stop("unit_marks", "crop unknown", r["id"])
            continue
        live[cid] = r["mark"]
        s = last_seen.get(cid, (None, None))[1]
        marks.append({"crop": cid, "state": r["mark"], "at": sub_at.get(s), "actor": sub_actor.get(s)})
    report.tables["unit_marks"]["out"] = len(marks)
    only_new = [f"{c}: {m}" for c, m in recomputed.items() if c not in live]
    only_live = [f"{c}: {m}" for c, m in live.items() if c not in recomputed]
    differ = [f"{c}: live {m}, recomputed {recomputed[c]}" for c, m in live.items() if c in recomputed and recomputed[c] != m]
    report.note(f"marks: recomputed {len(recomputed)}, live {len(live)}; recomputed only {len(only_new)}", only_new)
    report.note(f"marks: live only {len(only_live)}", only_live)
    report.note(f"marks: different {len(differ)}", differ)

    # Favourites.
    report.table("favourites", len(src["favourites"]))
    favourites = []
    for r in src["favourites"]:
        cid = crops.ids.get(r["unit"])
        if cid is None:
            report.stop("favourites", "crop unknown", r["unit"])
            continue
        favourites.append((r["user_id"], cid, ms(r["at"])))
    insert("favourites", favourites)
    report.tables["favourites"]["out"] = len(favourites)

    # The claim ledger.
    report.table("assertion_evidence", len(src["assertion_evidence"]))
    evidence_rows, evidence_cut = [], defaultdict(set)
    for r in src["assertion_evidence"]:
        cid = cut = None
        if r["kind"] == "crop":
            cid, cut = crops.ref_cut(r["ref"])
            if cid is None or cut is None:
                report.stop("assertion_evidence", "crop version unknown" if cid is None else "box matches no cut", r["ref"])
                continue
            evidence_cut[r["assertion"]].add((cid, cut))
        evidence_rows.append((r["assertion"], r["kind"], r["ref"], cid, cut, r["locator"]))
    insert("assertion_evidence", evidence_rows)
    report.tables["assertion_evidence"]["out"] = len(evidence_rows)

    report.table("assertions", len(src["assertions"]))
    assertions, assertions_out = [], []
    for r in src["assertions"]:
        cid = cut = None
        if r["predicate"] in CROP_PREDICATES:
            cid = crops.ids.get(r["subject"])
            if cid is None:
                report.stop("assertions", "crop unknown", f"{r['id']} on {r['subject']}")
                continue
            named = {c for c_id, c in evidence_cut[r["id"]] if c_id == cid}
            other = {c_id for c_id, _c in evidence_cut[r["id"]] if c_id != cid}
            if other:
                report.note(f"assertion {r['id']}: crop evidence names another crop {sorted(other)}")
            cut = -1 if -1 in named else 0
            if len(named) > 1:
                report.note(f"assertion {r['id']}: crop evidence names both cuts of {cid}")
        d = dict(r)
        d.update(crop=cid, cut=cut)
        assertions.append(tuple(d[c] for c in cols("assertions")))
        assertions_out.append((r["id"], r["subject"], r["predicate"], cid, cut))
    insert("assertions", assertions)
    report.tables["assertions"]["out"] = len(assertions)

    report.table("current_claims", len(src["current_claims"]))
    claims = []
    for r in src["current_claims"]:
        d = dict(r)
        version = d.pop("crop_version")
        d["crop"] = d["cut"] = None
        if r["predicate"] in CROP_PREDICATES:
            d["crop"] = crops.ids.get(r["subject"])
            if version:
                vcid, d["cut"] = crops.ref_cut(version)
                if vcid != d["crop"]:
                    d["cut"] = None
            else:
                d["cut"] = 0
            if d["crop"] is None or d["cut"] is None:
                report.stop("current_claims", "crop unknown" if d["crop"] is None else "crop version names no cut",
                            f"{r['subject']} {version}")
                continue
        elif version:
            report.stop("current_claims", "crop version on a claim not on a crop", f"{r['subject']} {r['predicate']} {version}")
            continue
        claims.append(tuple(d[c] for c in cols("current_claims")))
    insert("current_claims", claims)
    report.tables["current_claims"]["out"] = len(claims)

    # A saved response names its crop as the Worker's do, by integer id (`crop`) and the cut its claims
    # rest on (`cut`), beside the text id the old site saved, so a resent submission answers again.
    claim_on = {a[0]: (a[3], a[4]) for a in assertions_out}
    action_on = {r["id"]: r["assertion"] for r in src["assertion_actions"]}
    by_submission = defaultdict(set)
    for a in src["assertions"]:
        if a["submission"]:
            by_submission[a["submission"]].add(a["id"])
    for x in src["assertion_actions"]:
        if x["submission"]:
            by_submission[x["submission"]].add(action_on[x["id"]])
    report.table("ledger_submissions", len(src["ledger_submissions"]))
    saved = []
    for r in src["ledger_submissions"]:
        d = dict(r)
        response = json.loads(r["response"])
        cid = crops.ids.get(response.get("subject"))
        if cid is not None:
            cut = {claim_on[a][1] for a in by_submission[r["id"]] if claim_on.get(a, (None,))[0] == cid}
            response.update(crop=cid, cut=max(cut) if cut else None)
            d["response"] = json.dumps(response, ensure_ascii=False, separators=(",", ":"))
        saved.append(tuple(d[c] for c in cols("ledger_submissions")))
    insert("ledger_submissions", saved)
    report.tables["ledger_submissions"]["out"] = len(saved)

    for table in ("assertion_actions", "assertion_premises", "forms", "representations"):
        report.table(table, len(src[table]))
        insert(table, [tuple(r[c] for c in cols(table)) for r in src[table]])
        report.tables[table]["out"] = len(src[table])

    report.table("rejections", len(src["rejections"]))
    rejections = []
    for r in src["rejections"]:
        if r["submission"] not in sub:
            report.stop("rejections", "submission unknown", r["submission"])
            continue
        rejections.append((sub[r["submission"]], r["by"], r["reason"], ms(r["at"])))
    insert("rejections", rejections)
    report.tables["rejections"]["out"] = len(rejections)

    report.table("form_decisions", len(src["form_decisions"]))
    decisions, unresolved = [], []
    for r in src["form_decisions"]:
        # A decision lists the glyphs of its cluster when it was made; `units` keeps that list as it
        # was, and `crops` names those that still exist (no redirect is followed).
        units = json.loads(r["units"])
        missing = [u for u in units if u not in crops.ids]
        if missing:
            unresolved.append((r["seq"], len(missing), len(units)))
        d = dict(r)
        d["crops"] = json.dumps([crops.ids[u] for u in units if u in crops.ids])
        decisions.append(tuple(d[c] for c in cols("form_decisions")))
    insert("form_decisions", decisions)
    report.note(f"form_decisions: {len(unresolved)} decisions name {sum(n for _s, n, _of in unresolved)} crops that no longer "
                f"exist, kept in `units` and left out of `crops`; "
                f"{sum(1 for _s, n, of in unresolved if n == of)} of them only such crops",
                [f"{s}: {n} of {of}" for s, n, of in unresolved])
    report.tables["form_decisions"]["out"] = len(decisions)

    report.table("corpus_follow", len(src["corpus_follow"]))
    follow = []
    for r in src["corpus_follow"]:
        if r["id"] not in crops.ids:
            report.stop("corpus_follow", "crop unknown", r["id"])
            continue
        follow.append((crops.ids[r["id"]],))
    insert("corpus_follow", follow)
    report.tables["corpus_follow"]["out"] = len(follow)

    # A corpus glyph's character before a forms decision first moved it: a glyph the mirror lacks has no
    # crop to restore, and a character with no grapheme stops the load.
    report.table("form_bases", len(src["form_bases"]))
    bases = []
    for r in src["form_bases"]:
        if r["id"] not in crops.ids:
            report.drop("form_bases", "crop unknown")
            continue
        character = char_text(r["character"]) or None
        if character is not None and character not in graphemes:
            report.stop("form_bases", "character has no grapheme", f"{r['id']}: {character!r}")
            continue
        bases.append((crops.ids[r["id"]], graphemes.get(character) if character else None))
    insert("form_bases", bases)
    report.tables["form_bases"]["out"] = len(bases)


    # Corpus glyphs the old site held a row for, in the order the rows were written: the first review,
    # round, claim or flag that named each. A flag an admin set keeps its finding.
    corpus = list(source("units").execute(
        "SELECT id, max(_rowid) AS r, json_extract(data, '$.flag') AS flag FROM units WHERE origin = 'corpus' GROUP BY id ORDER BY r"))
    report.table("corpus_rows", len(corpus))
    corpus_rows, flags = [], []
    for r in corpus:
        cid = crops.ids.get(r["id"])
        if cid is None:
            report.stop("corpus_rows", "crop unknown", r["id"])
            continue
        corpus_rows.append((cid, len(corpus_rows) + 1))
        if r["flag"]:
            flag = json.loads(r["flag"])
            flags.append((cid, flag["finding"], flag.get("realigned"), flag.get("transcribed"), ms(flag["at"])))
    insert("corpus_rows", corpus_rows)
    report.tables["corpus_rows"]["out"] = len(corpus_rows)
    report.table("corpus_flags", len(flags))
    insert("corpus_flags", flags)
    report.tables["corpus_flags"]["out"] = len(flags)
    report.note(f"corpus_flags: {len(flags)} flags from the units records; each one's state and revision are the crop's own, "
                "and the old site wrote no event for a flag")

    report.table("written_forms", len(src["written_forms"]))
    written = []
    for r in src["written_forms"]:
        if r["target"] not in crops.ids:
            report.stop("written_forms", "crop unknown", f"{r['id']} on {r['target']}")
            continue
        d = dict(r)
        d["crop"] = crops.ids[r["target"]]
        written.append(tuple(d[c] for c in cols("written_forms")))
    insert("written_forms", written)
    report.tables["written_forms"]["out"] = len(written)

    # Script facts: those on crops that no longer exist are dropped.
    for table, target in (("unit_suspects", "suspects"), ("unit_shapes", "shapes")):
        report.table(table, len(src[table]))
        kept = []
        for r in src[table]:
            if r["id"] not in crops.ids:
                report.drop(table, "crop no longer exists")
                continue
            d = dict(r)
            d["crop"] = crops.ids[r["id"]]
            kept.append(tuple(d[c] for c in cols(target)))
        insert(target, kept)
        report.tables[table]["out"] = len(kept)
    out.commit()
    for table in ("events", "seen", "skips", "assertions", "assertion_evidence", "current_claims"):
        counts = dict(out.execute(f"SELECT cut, count(*) FROM {table} WHERE cut IS NOT NULL GROUP BY cut"))
        report.note(f"{table}: rows per cut {counts}")

    # `cut_at` and `at` are when the old site first recorded a version, which a citation of it states.
    cuts = {}
    for cid in sorted(crops.earlier.keys() | crops.cut_at.keys() | crops.encodings.keys()):
        cuts[crops.text_of[cid]] = {**({"cut_at": crops.cut_at[cid]} if cid in crops.cut_at else {}),
            "cuts": [{"cut": -1, "box": dict(zip("xywh", box)), "pixels": pixels, "image": image, "at": at}
                     for _vid, box, pixels, image, at in [crops.earlier[cid]]] if cid in crops.earlier else [],
            "encodings": [{"pixels": pixels, "image": image, "at": at} for pixels, image, at in crops.encodings.get(cid, [])]}
    (OUT / "history-cuts.json").write_text(json.dumps(cuts, ensure_ascii=False, separators=(",", ":")) + "\n")
    (OUT / "history-tombstones.json").write_text(json.dumps(list(tombstones.values()), ensure_ascii=False, indent=1) + "\n")
    # Each actor's totals, as the review batch keeps them from the cutover on and the ranking counts
    # them: verdicts only (a mark event is no review), and an undone submission's do not count.
    out.execute("INSERT INTO actor_stats SELECT a.id, (SELECT count(*) FROM events e JOIN submissions s ON s.id = e.submission "
                "WHERE e.actor = a.id AND s.undone = 0 AND e.kind IN (SELECT code FROM codes WHERE field = 'kind' "
                "AND key IN ('match', 'wrong', 'unsure'))), "
                "(SELECT count(*) FROM submissions s WHERE s.actor = a.id AND s.undone = 0), "
                "(SELECT max(at) FROM submissions s WHERE s.actor = a.id) FROM actors a")
    out.commit()
    # The same crops as a catalogue of retired crops, which publish.py numbers.
    catalogue = OUT / "history-tombstones.sqlite"
    catalogue.unlink(missing_ok=True)
    tomb = sqlite3.connect(catalogue)
    tomb.execute("CREATE TABLE units(id, origin, character, visual_group, production, category, state, revision, quiz, "
                 "document, style, data)")
    tomb.executemany("INSERT INTO units VALUES(?, 'retired', ?, NULL, ?, NULL, ?, 0, 1, ?, ?, ?)",
                     [(t["text_id"], t["record"].get("label"), t["record"].get("production"), t["record"].get("state"),
                       t["document"], t["record"].get("style"), json.dumps({**t["record"], "page_id": t["page_key"]}))
                      for t in tombstones.values()])
    tomb.commit()
    (OUT / "history-marks.json").write_text(json.dumps(marks, indent=1) + "\n")
    report.note(f"tombstones: {len(tombstones)}", list(tombstones))

    lines = report.lines()
    (OUT / "history-report.txt").write_text("\n".join(lines) + "\n")
    print("\n".join(lines), flush=True)
    sys.exit(1 if report.stopped else 0)


if __name__ == "__main__":
    main()
