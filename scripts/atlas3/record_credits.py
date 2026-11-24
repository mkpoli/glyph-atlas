"""Write the corpus glyphs' records whose credit a reader should see otherwise, and the catalogue that moves
the glyphs to them.

    ATLAS3_DIR=work/atlas-v3 python3 scripts/atlas3/record_credits.py <folder> --books hl: --list   # the packs to fetch
    ATLAS3_DIR=work/atlas-v3 python3 scripts/atlas3/record_credits.py <folder> --books hl: --packs <dir>
    rclone copy <folder>/packs r2:glyph-atlas/packs --immutable
    python3 scripts/atlas3/publish.py <folder>/records.sqlite --dry-run
    python3 scripts/atlas3/publish.py <folder>/records.sqlite

A corpus glyph's record is in a pack named by its content, so it is written again in a new pack. The
record is the published one with its `attribution` and `text_attribution` as `glyph_atlas.credit_line`
gives them, and nothing else changed; a record whose credits stay is left where it is. `--books` is the
start of the book keys. `--packs` is a folder with the packs `--list` names. The publish's
`record_places` names each glyph's new place and the place it replaces, and stops where the glyph's record
has been published since.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import sqlite3
import sys
from pathlib import Path

from glyph_atlas.credit_line import credit_line

OUT = Path(os.environ.get("ATLAS3_DIR", Path(__file__).resolve().parents[2] / "work/atlas-v3")) / "out"
PACK = 32 * 1024**2


def placed(books: str) -> list[tuple[str, list]]:
    """Each published corpus glyph of the books whose keys start with `books`, and where its record is."""
    atlas = sqlite3.connect(f"file:{OUT / 'atlas.sqlite'}?mode=ro", uri=True)
    found = []
    for pid, rev in atlas.execute("SELECT p.id, p.rev FROM pages p JOIN books b ON b.id = p.book "
                                  "WHERE p.rev > 0 AND substr(b.key, 1, ?) = ? ORDER BY p.id", (len(books), books)):
        bundle = json.loads(gzip.decompress((OUT / "pages" / f"{pid}-{rev}.json.gz").read_bytes()))
        found += [(e["id"], e["record_place"]) for e in bundle["crops"] if e.get("record_place") and not e.get("retired")]
    return found


def rewritten(record: dict) -> dict | None:
    """`record` with the credits a reader sees, or None where it has them."""
    new = dict(record)
    for field in ("attribution", "text_attribution"):
        if field in record and record[field] != (credit := credit_line(record[field])):
            new[field] = credit
    return None if new == record else new


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("output", type=Path)
    parser.add_argument("--books", required=True, help="the start of the book keys")
    parser.add_argument("--packs", type=Path, help="the folder the named packs were fetched into")
    parser.add_argument("--list", action="store_true", help="name the packs the records are in, and write nothing")
    args = parser.parse_args(argv)
    found = placed(args.books)
    if args.list:
        for name in sorted({place[0] for _, place in found}):
            print(name.removeprefix("packs/"))
        return
    if args.packs is None:
        parser.error("--packs names the folder with the packs --list names")
    if (args.output / "records.sqlite").exists():
        sys.exit(f"{args.output / 'records.sqlite'} exists; the catalogue is written into a new folder")
    (args.output / "packs").mkdir(parents=True, exist_ok=True)
    rows, buffer, pending = [], bytearray(), []

    def close():
        if buffer:
            name = f"packs/{hashlib.sha256(buffer).hexdigest()}.bin"
            (args.output / name).write_bytes(buffer)
            rows.extend((i, name, offset, size, *old) for i, offset, size, old in pending)
            buffer.clear()
            pending.clear()
    by_pack: dict[str, list] = {}
    for i, place in found:
        by_pack.setdefault(place[0], []).append((i, place))
    for name, entries in sorted(by_pack.items()):
        with (args.packs / name.removeprefix("packs/")).open("rb") as pack:
            for i, (held, offset, size) in sorted(entries, key=lambda e: e[1][1]):
                pack.seek(offset)
                record = json.loads(pack.read(size))
                if record["id"] != i:
                    sys.exit(f"{name} at {offset} holds {record['id']}, not {i}")
                if (new := rewritten(record)) is None:
                    continue
                raw = json.dumps(new, ensure_ascii=False, separators=(",", ":")).encode()
                if len(buffer) + len(raw) > PACK:
                    close()
                pending.append((i, len(buffer), len(raw), (held, offset, size)))
                buffer.extend(raw)
    close()
    db = sqlite3.connect(args.output / "records.sqlite")
    db.execute("CREATE TABLE record_places(id TEXT PRIMARY KEY, object TEXT NOT NULL, offset INTEGER NOT NULL, size INTEGER NOT NULL, "
               "old_object TEXT NOT NULL, old_offset INTEGER NOT NULL, old_size INTEGER NOT NULL)")
    db.executemany("INSERT INTO record_places VALUES(?, ?, ?, ?, ?, ?, ?)", rows)
    db.commit()
    print(f"{len(rows)} of {len(found)} records in {len({r[1] for r in rows})} packs -> {args.output}", flush=True)


if __name__ == "__main__":
    main()
