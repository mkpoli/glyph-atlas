"""Check the new layout against a copy of the live tables, crop by crop, before the site switches to it.

    ATLAS3_DIR=work/atlas-v3 python3 scripts/atlas3/check.py

Reads the `source-*.sqlite` copies (`pull.py`) and the local copies the publisher keeps (`out/`), and
nothing remote: every publish is confirmed by its stamp in D1 and replayed on the local copy, so the
local copy is what D1 holds. Every check covers every crop; a failing one lists up to SHOWN examples
in `out/check-<stamp>.txt` and the run exits 1.

- Ids: every live crop, corpus glyph and retired crop has a number, and no number has two crops.
- Rows: each crop's character, state, style, withheld flag, production, category, visual group,
  revision, book, witness year and suspect flag equal its live record's; a corpus glyph's review
  columns come from its review overlay.
- Bundles: each collected crop's entry is its live record (less the fields its row holds), a corpus
  glyph's points at its live record, and a retired crop's is marked retired, names its replacement
  by the integer id of the first crop on its redirect chain, and has no row.
- Runs: the runs of two and three crops are the live ones, crop for crop, with their graphemes, hand
  and book.
- Images: every live image key is in the image key map, at its live place.
"""

from __future__ import annotations

import gzip
import json
import sqlite3
import sys
import time
from collections import Counter

from build import HERE, OUT, PAGE, bundle_entry, char_text, replacement

SHOWN = 20


def source(table):
    return sqlite3.connect(f"file:{HERE / f'source-{table}.sqlite'}?mode=ro", uri=True)


def latest(db, table, cols):
    # A keyset copy can hold a row twice when it was copied again; the last copy wins.
    return db.execute(f"SELECT {cols} FROM {table} WHERE _rowid IN (SELECT max(_rowid) FROM {table} GROUP BY id)")


class Report:
    def __init__(self):
        self.lines, self.failed = [], 0

    def check(self, name, problems):
        problems = list(problems)
        self.lines.append(("ok   " if not problems else f"FAIL ({len(problems)}) ") + name)
        self.lines += [f"     {p}" for p in problems[:SHOWN]]
        self.failed += bool(problems)
        print(self.lines[-1 - min(len(problems), SHOWN)], flush=True)


def main():
    atlas = sqlite3.connect(f"file:{OUT / 'atlas.sqlite'}?mode=ro", uri=True)
    runs_db = sqlite3.connect(f"file:{OUT / 'atlas-runs.sqlite'}?mode=ro", uri=True)
    mirror = sqlite3.connect(f"file:{OUT / 'mirror.sqlite'}?mode=ro", uri=True)
    report = Report()

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

    diffs = []
    for u in local:
        cid = id_of.get(u[0])
        if cid in rows and got(cid) != (w := want(*u[2:10], 0) + (u[11] or "unknown", years.get(u[11]), int(bool(json.loads(u[10]).get("suspect"))))):
            diffs.append(f"{u[0]} ({cid}): live {w}, new layout {got(cid)}")
    for c in corpus:
        cid, o = id_of.get(c[0]), overlay.get(c[0])
        if cid not in rows:
            continue
        book = c[5] or c[0].split("|")[0].split(":")[0]
        w = want((o[2] if o else None) or c[1], o[3] if o else "pending", (o[4] if o else None) or c[3], o[5] if o else 1,
                 c[2], None, c[4], o[9] if o else 0, 1) + (book, years.get(c[5]), 0)
        g = got(cid)
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
    def replaced_by(text):
        if text not in redirects:
            return None
        try:
            found = replacement(text, redirects, id_of.__contains__)
        except ValueError as loop:
            return f"a redirect loop ({loop})"
        return id_of[found] if found else f"{redirects[text]}, which has no crop"

    for u in sorted(retired, key=lambda u: id_of.get(u[0], -1)):
        cid = id_of.get(u[0])
        e = (entry(cid) or {}) if cid is not None else {}
        if cid is not None and (cid in rows or not e.get("retired") or e.get("id") != u[0] or e.get("replaced_by") != replaced_by(u[0])):
            wrong.append(f"{u[0]} ({cid}): retired on the live site; new layout row {cid in rows}, entry {e.get('id')} "
                         f"retired {e.get('retired')} replaced by {e.get('replaced_by')}, live {replaced_by(u[0])}")
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

    path = OUT / f"check-{time.strftime('%Y%m%dT%H%M%S')}.txt"
    path.write_text("\n".join(report.lines) + "\n")
    print(f"{'all checks pass' if not report.failed else f'{report.failed} checks fail'}; report in {path}", flush=True)
    sys.exit(1 if report.failed else 0)


if __name__ == "__main__":
    main()
