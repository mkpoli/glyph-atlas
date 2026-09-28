"""Seal a corpus export for adding to the site: content-addressed packs and ordered, chunked D1 SQL.

The input is `export_cloudflare_corpus.py`. With `--records-only` its records point only at images
an earlier publication already put in R2 and D1; without it the export also packed the crops of its
own units, and those media packs and `media` rows are sealed too. A `media` row is inserted only
when its key is new: the key hashes the crop's render specification (source file, box, context,
renderer version; `glyph_atlas.review.media`), so a row already in D1 shows the same crop.
Locally reviewed corpus glyphs are not carried as `units` rows here; a review reaches Cloudflare
through a full `seal_cloudflare.py` publication. The output holds `objects/` (one file per pack,
named by its SHA-256), `sql/NNN.sql` parts under D1's import size with the `media` rows first, and
`publication.json` listing both in upload order. `publish_cloudflare.sh` uploads every object
before it runs any SQL.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import os
import sqlite3
from pathlib import Path

from cloudflare_schema import CORPUS_COLUMNS, CORPUS_REFRESH, corpus_upsert

PART_BYTES = 90 * 1024**2


def seal(corpus: Path, output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    (output / "objects").mkdir()
    (output / "sql").mkdir()
    db = sqlite3.connect(corpus / "corpus.sqlite")
    names, objects = {}, []
    for table, pattern in (("media", "pack-*.bin"), ("corpus_units", "corpus-*.bin")):
        for path in sorted(corpus.glob(pattern)):
            extent = db.execute(f"SELECT max(offset+size) FROM {table} WHERE object=?", (path.name,)).fetchone()[0]
            if extent is None:
                continue
            if path.stat().st_size < extent:
                raise ValueError(f"Incomplete pack {path.name}")
            with path.open("rb") as handle:
                sha = hashlib.file_digest(handle, "sha256").hexdigest()
            os.link(path, output / "objects" / f"{sha}.bin")
            names[path.name] = f"packs/{sha}.bin"
            objects.append({"key": names[path.name], "file": f"objects/{sha}.bin", "sha256": sha,
                            "bytes": path.stat().st_size})
    missing = db.execute("SELECT DISTINCT object FROM media").fetchall()
    if unsealed := sorted(name for (name,) in missing if name not in names):
        raise ValueError(f"media rows name packs that are not in the export: {unsealed}")
    parts: list[str] = []
    handle, size = None, 0
    media = (f"INSERT OR IGNORE INTO media(key,object,offset,size,content_type) "
             f"VALUES({_quoted(key)},{_quoted(names[obj])},{offset},{length},{_quoted(kind)});"
             for key, obj, offset, length, kind in db.execute(
                 "SELECT key,object,offset,size,content_type FROM media ORDER BY key"))
    records = (corpus_upsert((*row[:5], names[row[5]], *row[6:]))
               for row in db.execute(f"SELECT {','.join(CORPUS_COLUMNS)} FROM corpus_units ORDER BY id"))
    for statement in itertools.chain(media, records):
        line = statement + "\n"
        if handle is None or size + len(line) > PART_BYTES:
            if handle:
                handle.close()
            parts.append(f"sql/{len(parts) + 1:03}.sql")
            handle, size = (output / parts[-1]).open("w"), 0
        handle.write(line)
        size += len(line.encode())
    # The last part names the glyphs reviewed since their rows were first published and counts them
    # per character, once every row is in; until then the earlier counts stand.
    if handle is None:
        parts.append(f"sql/{len(parts) + 1:03}.sql")
        handle = (output / parts[-1]).open("w")
    handle.write(CORPUS_REFRESH + "\n")
    handle.close()
    count = db.execute("SELECT count(*) FROM corpus_units").fetchone()[0]
    images = db.execute("SELECT count(*) FROM media").fetchone()[0]
    summary = {"corpus_units": count, "media": images, "objects": objects, "sql": parts}
    (output / "publication.json").write_text(json.dumps(summary, indent=1) + "\n")
    return {"corpus_units": count, "media": images, "objects": len(objects),
            "bytes": sum(o["bytes"] for o in objects), "sql_parts": len(parts)}


def _quoted(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("corpus", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(json.dumps(seal(args.corpus, args.output)), flush=True)
