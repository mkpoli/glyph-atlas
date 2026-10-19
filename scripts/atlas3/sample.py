"""Compare random crops in the built layout with their source records, field by field."""
import gzip
import json
import os
import random
import sqlite3
import sys
from pathlib import Path

from build import PAGE, char_text

ROOT = Path(__file__).resolve().parents[2]
# Copies of the live tables and the build output live outside the repository.
HERE = Path(os.environ.get("ATLAS3_DIR", ROOT / "work/atlas-v3"))
OUT = HERE / "out"
n = int(sys.argv[1]) if len(sys.argv) > 1 else 200
atlas = sqlite3.connect(OUT / "atlas.sqlite"); atlas.row_factory = sqlite3.Row
mirror = sqlite3.connect(OUT / "mirror.sqlite")
units = sqlite3.connect(HERE / "source-units.sqlite"); units.row_factory = sqlite3.Row
corpus = sqlite3.connect(HERE / "source-corpus_units.sqlite"); corpus.row_factory = sqlite3.Row
codes = {(f, c): k for f, k, c in mirror.execute("SELECT field, key, code FROM codes")}
grapheme = dict(atlas.execute("SELECT id, text FROM graphemes"))
production = dict(atlas.execute("SELECT id, key FROM productions"))
book = dict(atlas.execute("SELECT id, key FROM books"))
page_key = dict(atlas.execute("SELECT id, key FROM pages"))
rev = dict(atlas.execute("SELECT id, rev FROM pages"))
bad = []


def check(text_id, want):
    cid = mirror.execute("SELECT id FROM ids WHERE text=?", (text_id,)).fetchone()[0]
    c = atlas.execute("SELECT * FROM crops WHERE id=?", (cid,)).fetchone()
    got = {"char": grapheme.get(c["char"]), "state": codes.get(("state", c["state"])), "style": codes.get(("style", c["style"])),
           "quiz": c["quiz"], "production": production.get(c["production"]), "book": book.get(c["book"]),
           "visual_group": codes.get(("visual_group", c["visual_group"]))}
    body = json.loads(gzip.decompress((OUT / "pages" / f"{cid // PAGE}-{rev[cid // PAGE]}.json.gz").read_bytes()))
    entry = next((e for e in body["crops"] if e["slot"] == cid % PAGE), None)
    got["bundle id"] = entry and entry["id"]
    got["page"] = page_key[cid // PAGE]
    for k, v in want.items():
        if got.get(k) != v:
            bad.append((text_id, k, v, got.get(k)))


ids = [r[0] for r in units.execute("SELECT id FROM units WHERE origin='local'")]
for text_id in random.sample(ids, n):
    r = units.execute("SELECT * FROM units WHERE id=? ORDER BY _rowid DESC LIMIT 1", (text_id,)).fetchone()
    data = json.loads(r["data"])
    check(text_id, {"char": char_text(r["character"]) or None, "state": r["state"], "style": r["style"], "quiz": r["quiz"],
                    "production": r["production"] or "unknown", "book": r["document"] or "unknown",
                    "visual_group": r["visual_group"] or None, "bundle id": text_id,
                    "page": data.get("page_id") or f"book:{r['document']}"})
ids = [r[0] for r in corpus.execute("SELECT id FROM corpus_units")]
for text_id in random.sample(ids, n):
    r = corpus.execute("SELECT * FROM corpus_units WHERE id=? ORDER BY _rowid DESC LIMIT 1", (text_id,)).fetchone()
    o = units.execute("SELECT * FROM units WHERE id=? AND origin='corpus' ORDER BY _rowid DESC LIMIT 1", (text_id,)).fetchone()
    check(text_id, {"char": char_text((o["character"] if o else None) or r["character"]) or None, "style": r["style"],
                    "production": r["production"] or "unknown", "bundle id": text_id})
print(f"{2 * n} crops compared, {len(bad)} differences")
for b in bad[:30]:
    print("  ", *b)
sys.exit(1 if bad else 0)
