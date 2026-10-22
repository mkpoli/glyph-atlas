"""Name an export's packs by their content, as the site's packs are named: `packs/<sha256>.bin`.

    python3 scripts/atlas3/address_packs.py <export>/corpus.sqlite [--dry-run]

The exporters number their packs (`pack-10001.bin`, `corpus-0001.bin`), and the bucket's root already
holds packs of earlier publications under such numbers, so an upload under the exporter's names would
put new bytes under rows the site still reads. Each pack the catalogue's `media` and `corpus_units`
rows name moves to `packs/<its SHA-256>.bin` beside the catalogue, and the rows follow. The rows change,
and each move is recorded in `addressed`, in one transaction before any file moves, so a run stopped
between the two finishes the moves on the next. Then

    rclone copy <export>/packs r2:glyph-atlas/packs --immutable

uploads them, before `publish.py` names them in the image key map and the page bundles.
"""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

from glyph_atlas import export_lock

ADDRESSED = re.compile(r"packs/[0-9a-f]{64}\.bin")
TABLES = ("media", "corpus_units")


def crossing(db, table) -> list[str]:
    """Packs in which two rows share bytes: a pack written twice under one name."""
    found, last = set(), (None, 0)
    for obj, offset, size in db.execute(f"SELECT object, offset, size FROM {table} ORDER BY object, offset"):
        if obj == last[0] and offset < last[1]:
            found.add(obj)
        last = (obj, max(offset + size, last[1]) if obj == last[0] else offset + size)
    return sorted(found)


def digest(path: Path, attempts=6) -> str:
    """The pack's SHA-256. A read of /mnt (9P) fails with ENOMEM while the kernel lacks a free 64 KiB
    block for the request; that passes, so the read is tried again."""
    for attempt in range(attempts):
        try:
            with path.open("rb") as handle:
                return hashlib.file_digest(handle, "sha256").hexdigest()
        except OSError as error:
            if error.errno != errno.ENOMEM or attempt == attempts - 1:
                raise
            time.sleep(2 ** attempt)
    raise AssertionError("unreachable")


def address(catalogue: Path, *, dry_run=False) -> dict:
    root = catalogue.parent
    lock = export_lock.hold(root)  # noqa: F841 -- no exporter writes while the packs move
    db = sqlite3.connect(f"file:{catalogue}?mode=ro", uri=True) if dry_run else sqlite3.connect(catalogue)
    if not dry_run:
        db.execute("CREATE TABLE IF NOT EXISTS addressed(original TEXT PRIMARY KEY, object TEXT NOT NULL)")
    known = db.execute("SELECT 1 FROM sqlite_master WHERE name='addressed'").fetchone()
    tables = [t for t in TABLES if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone()]
    # A move recorded but not made: the rows already name the new object. A recorded move is pending
    # only until its file has moved, so a pack number the exporter hands out again is never taken for one.
    finished = 0
    for original, obj in db.execute("SELECT original, object FROM addressed").fetchall() if known else []:
        source, target = root / original, root / obj
        if source.exists() and not dry_run:
            if f"packs/{digest(source)}.bin" != obj:
                sys.exit(f"{original} does not hold the bytes recorded for {obj}; refusing to move it")
            target.parent.mkdir(exist_ok=True)
            if target.exists():
                source.unlink()
            else:
                source.rename(target)
            finished += 1
    if known and not dry_run:
        with db:
            db.execute("DELETE FROM addressed")
    # Two packs of the same bytes share one address, so only numbered packs can hold another run's rows.
    if bad := [o for t in tables for o in crossing(db, t) if not ADDRESSED.fullmatch(o)]:
        sys.exit(f"packs hold bytes of two rows under one name: {', '.join(bad[:10])}")
    extents: dict[str, int] = {}
    for table in tables:
        for obj, end in db.execute(f"SELECT object, max(offset + size) FROM {table} GROUP BY object"):
            extents[obj] = max(end, extents.get(obj, 0))
    pending = sorted(o for o in extents if not ADDRESSED.fullmatch(o))
    for obj in pending:
        path = root / obj
        if not path.exists():
            sys.exit(f"{obj} is named by the catalogue but is not beside it")
        if path.stat().st_size < extents[obj]:
            sys.exit(f"{obj} is {path.stat().st_size} bytes, shorter than its rows reach ({extents[obj]})")
    names = {} if dry_run else {obj: f"packs/{digest(root / obj)}.bin" for obj in pending}
    # Every row and the record of every move change in one transaction, before any file moves.
    if names:
        with db:
            db.executemany("INSERT OR REPLACE INTO addressed VALUES(?, ?)", names.items())
            for table in tables:
                db.execute(f"UPDATE {table} SET object=a.object FROM addressed a WHERE {table}.object=a.original")
    for obj, name in names.items():
        target = root / name
        target.parent.mkdir(exist_ok=True)
        if target.exists():
            (root / obj).unlink()
        else:
            (root / obj).rename(target)
    moved = len(names)
    if names:
        with db:
            db.execute("DELETE FROM addressed")
    named = {o for t in tables for o, in db.execute(f"SELECT DISTINCT object FROM {t}")}
    sizes = [(root / o).stat().st_size for o in named if (root / o).exists()]
    counts = {"moved": moved, "finished": finished, "to_move": len(pending) if dry_run else 0,
              "objects": len(named), "bytes": sum(sizes), "missing": len(named) - len(sizes),
              "rows": {t: db.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in tables}}
    db.close()
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("catalogue", type=Path)
    parser.add_argument("--dry-run", action="store_true", help="check the packs and count them, move nothing")
    args = parser.parse_args()
    print(json.dumps(address(args.catalogue, dry_run=args.dry_run)))


if __name__ == "__main__":
    main()
