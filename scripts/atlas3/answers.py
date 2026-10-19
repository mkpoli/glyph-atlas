"""Write a publication's answers to R2: the counts the galleries show, per character.

    ATLAS3_DIR=work/atlas-v3 python3 scripts/atlas3/answers.py [--dry-run]

A publication bumps `generation` in glyph-atlas-v3 from G to G + 1, writes its answers under
`v3/pub/<pub>/`, and then moves `v3/pointer.json` to `{"pub", "through_gen": G}`. A gallery's count
is the answer plus the `tally` rows above `through_gen` (decision record 0005).

The counts come from the local copy of glyph-atlas-v3, which every publish replays. Reviews on the
new layout are absorbed by reading their events; until that exists, a database holding events is
refused.

- `counts/<shard>.json.gz`: per character (grapheme id, in shard id % SHARDS), one cell per kind,
  style, state, withheld, witness decade and visual group, with its number of crops. Codes are the
  `codes` table's; a null is unknown.
- `unread.json.gz`: the same cells for crops with no character, per script category.
"""

from __future__ import annotations

import argparse
import fcntl
import gzip
import json
import sqlite3
import sys
import time
from collections import defaultdict

from publish import OUT, query, upload

SHARDS = 256
PUB_PREFIX = "v3/pub/"
POINTER_PREFIX = "v3/"
CELL = ("kind", "style", "state", "quiz", "decade", "visual_group", "n")
# Floor division, so a year before 1 CE falls in its own decade: SQLite's division truncates.
DECADE = "CASE WHEN year IS NULL THEN NULL WHEN year < 0 THEN (year - 9) / 10 * 10 ELSE year / 10 * 10 END"


def cells(atlas, key):
    out = defaultdict(list)
    for row in atlas.execute(f"SELECT {key}, kind, style, state, quiz, {DECADE}, visual_group, count(*) FROM crops "
                             f"WHERE kind < 2 AND char IS {'NOT ' if key == 'char' else ''}NULL GROUP BY 1, 2, 3, 4, 5, 6, 7"):
        out[row[0]].append(list(row[1:]))
    return out


def write(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()))
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="write the answers locally and change nothing remote")
    args = parser.parse_args()

    lock = open(OUT / "mirror.lock", "w")  # noqa: SIM115 -- held until the process exits
    fcntl.flock(lock, fcntl.LOCK_EX)
    mirror = sqlite3.connect(OUT / "mirror.sqlite")
    if mirror.execute("SELECT 1 FROM sqlite_master WHERE name = 'pending'").fetchone() and \
            mirror.execute("SELECT count(*) FROM pending").fetchone()[0]:
        sys.exit("a publish is unfinished; run publish.py to finish it first")
    if query("glyph-atlas-v3", "SELECT count(*) AS n FROM events")[0]["n"]:
        sys.exit("glyph-atlas-v3 holds review events, and absorbing them is not built yet; nothing published")

    pub = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    if args.dry_run:
        through = query("glyph-atlas-v3", "SELECT gen FROM generation WHERE id = 0")[0]["gen"]
    else:
        # Reviews written after the bump carry the next generation, so the answers stand through G.
        through = query("glyph-atlas-v3", "UPDATE generation SET gen = gen + 1 WHERE id = 0 RETURNING gen")[0]["gen"] - 1

    atlas = sqlite3.connect(OUT / "atlas.sqlite")
    folder = OUT / "pub" / pub
    shards = defaultdict(dict)
    for char, rows in cells(atlas, "char").items():
        shards[char % SHARDS][char] = rows
    files = [write(folder / "counts" / f"{shard}.json.gz", {"pub": pub, "cell": CELL, "chars": shards.get(shard, {})})
             for shard in range(SHARDS)]
    unread = cells(atlas, "category")
    files.append(write(folder / "unread.json.gz", {"pub": pub, "cell": CELL, "categories": {str(k): v for k, v in unread.items()}}))
    pointer = write(OUT / "pub" / f"pointer-{pub}" / "pointer.json", {"pub": pub, "through_gen": through})
    characters = sum(len(s) for s in shards.values())
    print(f"pub {pub} through generation {through}: {characters} characters in {SHARDS} shards, "
          f"{sum(len(v) for v in unread.values())} unread cells -> {folder}", flush=True)
    if args.dry_run:
        print("dry run: nothing uploaded; the generation was not bumped", flush=True)
        return
    upload([f for f in files if f.parent.name == "counts"], f"{PUB_PREFIX}{pub}/counts/")
    upload([folder / "unread.json.gz"], f"{PUB_PREFIX}{pub}/")
    # The pointer moves last, once everything it names is in place.
    upload([pointer], POINTER_PREFIX)
    print(f"v3/pointer.json names {pub}", flush=True)


if __name__ == "__main__":
    main()
