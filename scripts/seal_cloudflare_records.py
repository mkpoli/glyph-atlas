"""Seal a records-only corpus export: content-addressed record packs and ordered, chunked D1 SQL.

The input is `export_cloudflare_corpus.py --records-only`, whose records point only at images an
earlier publication already put in R2 and D1. Locally reviewed corpus glyphs are not carried as
`units` rows here; a review reaches Cloudflare through a full `seal_cloudflare.py` publication. The output holds `objects/` (one file per
record pack, named by its SHA-256), `sql/NNN.sql` parts under D1's import size, and
`publication.json` listing both in upload order. `publish_cloudflare.sh` uploads it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
from pathlib import Path

from cloudflare_schema import CORPUS_REFRESH

PART_BYTES = 90 * 1024**2


def seal(corpus: Path, output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    (output / "objects").mkdir()
    (output / "sql").mkdir()
    db = sqlite3.connect(corpus / "corpus.sqlite")
    if db.execute("SELECT count(*) FROM media").fetchone()[0]:
        raise ValueError("This export packed images; seal it with seal_cloudflare.py")
    names, objects = {}, []
    for path in sorted(corpus.glob("corpus-*.bin")):
        extent = db.execute("SELECT max(offset+size) FROM corpus_units WHERE object=?", (path.name,)).fetchone()[0]
        if extent is None:
            continue
        if path.stat().st_size < extent:
            raise ValueError(f"Incomplete record pack {path.name}")
        with path.open("rb") as handle:
            sha = hashlib.file_digest(handle, "sha256").hexdigest()
        os.link(path, output / "objects" / f"{sha}.bin")
        names[path.name] = f"packs/{sha}.bin"
        objects.append({"key": names[path.name], "file": f"objects/{sha}.bin", "sha256": sha,
                        "bytes": path.stat().st_size})
    parts: list[str] = []
    handle, size = None, 0
    rows = db.execute("SELECT id,character,family,visual_group,shuffle,object,offset,size,production FROM corpus_units ORDER BY id")
    for identity, character, family, group, shuffle, name, offset, length, production in rows:
        line = "INSERT OR REPLACE INTO corpus_units(id,character,family,visual_group,shuffle,object,offset,size,production) VALUES({},{},{},{},{},{},{},{},{});\n".format(
            *(_quote(v) for v in (identity, character, family, group)), shuffle, _quote(names[name]), offset, length,
            _quote(production))
        if handle is None or size + len(line) > PART_BYTES:
            if handle:
                handle.close()
            parts.append(f"sql/{len(parts) + 1:03}.sql")
            handle, size = (output / parts[-1]).open("w"), 0
        handle.write(line)
        size += len(line.encode())
    # The last part counts the glyphs per character once every row is in.
    if handle is None:
        parts.append(f"sql/{len(parts) + 1:03}.sql")
        handle = (output / parts[-1]).open("w")
    handle.write(CORPUS_REFRESH + "\n")
    handle.close()
    count = db.execute("SELECT count(*) FROM corpus_units").fetchone()[0]
    summary = {"corpus_units": count, "objects": objects, "sql": parts}
    (output / "publication.json").write_text(json.dumps(summary, indent=1) + "\n")
    return {"corpus_units": count, "objects": len(objects), "bytes": sum(o["bytes"] for o in objects), "sql_parts": len(parts)}


def _quote(value) -> str:
    return "NULL" if value is None else "'" + str(value).replace("'", "''") + "'"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("corpus", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(json.dumps(seal(args.corpus, args.output)), flush=True)
