"""Seal public catalogue inputs into content-addressed objects and importable SQL."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
from pathlib import Path

from cloudflare_schema import CORPUS_REFRESH, category_of, schema
from export_cloudflare import encoded

IMMUTABLE = ("metadata", "characters", "aliases", "corpus_units", "media")


def reviewed_baselines(db, corpus):
    """Carry reviewed identities and outstanding issues into the published baseline."""
    from glyph_atlas.corpus.api import CorpusAPI
    from glyph_atlas.review.corpus_reviews import CorpusReviews
    # Sealing reads the review journal and never creates one: with no journal there is nothing to carry.
    if not Path("work/corpus-index/reviews.sqlite").is_file():
        return {"applied": 0, "stale": 0}
    api = CorpusAPI("work", "work/corpus-index", autobuild=False)
    reviews = CorpusReviews(api)
    latest, baseline = reviews.latest(), reviews.baseline()
    applied, stale = 0, 0
    for identity in latest.keys() | baseline.keys():
        row = db.execute("SELECT object,offset,size,shuffle FROM corpus_units WHERE id=?", (identity,)).fetchone()
        if not row:
            continue
        with (corpus / row[0]).open("rb") as source:
            source.seek(row[1])
            original = json.loads(source.read(row[2]))
        current = reviews.overlay(original, latest.get(identity))
        if current["state"] == "stale":
            stale += 1
            continue
        written = current.get("written_character")
        if written:
            from glyph_atlas import refs
            # A character written with a mark is several code points; `grapheme` takes the whole sequence.
            current["grapheme"] = refs.grapheme(" ".join(refs.to_code_points(written)))
        db.execute("INSERT OR REPLACE INTO units VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            identity, "corpus", written, current.get("reading"), current.get("grapheme"),
            (current.get("visual_group") or {}).get("id"), current.get("production") or "unknown",
            category_of(current.get("label")),
            # Dealt in Quick review, as the Worker decides, when its image may be served and it names a character.
            current["state"], current["revision"], int(bool(current.get("proxyable") and written)), 1, row[3],
            encoded(current), encoded(original), "{}", "{}"))
        applied += 1
    return {"applied": applied, "stale": stale}


def seal(catalogue: Path, corpus: Path, output: Path):
    output.mkdir(parents=True, exist_ok=False)
    objects = output / "objects"
    objects.mkdir()
    db = sqlite3.connect(output / "atlas.sqlite")
    schema(db)
    db.execute("ATTACH DATABASE ? AS local_source", (str(catalogue / "catalogue.sqlite"),))
    db.execute("ATTACH DATABASE ? AS corpus_source", (str(corpus / "corpus.sqlite"),))
    for table in ("metadata", "characters", "aliases", "units"):
        db.execute(f"INSERT INTO {table} SELECT * FROM local_source.{table}")
    db.execute("INSERT INTO media SELECT * FROM corpus_source.media")
    # Named columns: an export made before corpus rows carried their material is refused here.
    columns = "id,character,family,visual_group,shuffle,object,offset,size,production"
    db.execute(f"INSERT INTO corpus_units({columns}) SELECT {columns} FROM corpus_source.corpus_units")
    baselines = reviewed_baselines(db, corpus)
    # Earlier preparation may contain media for subsequently excluded sources.
    # Only references from the licence-filtered publication may enter its media index.
    referenced = set()
    for data, snapshot in db.execute("SELECT data,snapshot FROM units"):
        referenced.update(re.findall(r"/atlas/media/([0-9a-f]{64})\.webp", data + snapshot))
    db.executemany("INSERT OR IGNORE INTO media SELECT * FROM local_source.media WHERE key=?",
                   ((key,) for key in referenced))
    db.commit()
    manifest = []

    def finish(path):
        with path.open("rb") as handle:
            sha = hashlib.file_digest(handle, "sha256").hexdigest()
        name = sha + ".bin"
        path.rename(objects / name)
        manifest.append({"key": "packs/" + name, "file": "objects/" + name,
                         "sha256": sha, "bytes": (objects / name).stat().st_size})
        return "packs/" + name

    # Repack permitted media, removing both pointers and bytes for excluded crops.
    partial, target, part_index = None, None, 0
    updates = []
    all_media = db.execute("SELECT key,object,offset,size FROM media ORDER BY object,offset").fetchall()
    try:
        for key, original, offset, size in all_media:
            if target is None or target.tell() >= 32 * 1024**2:
                if target:
                    target.close()
                    name = finish(partial)
                    db.executemany("UPDATE media SET object=?,offset=? WHERE key=?", ((name, o, k) for k, o in updates))
                part_index += 1
                partial = output / f"media-{part_index}.tmp"
                target, updates = partial.open("wb"), []
            source = (corpus if (corpus / original).exists() else catalogue) / original
            with source.open("rb") as handle:
                handle.seek(offset)
                data = handle.read(size)
            if len(data) != size:
                raise ValueError("Incomplete media pack")
            updates.append((key, target.tell()))
            target.write(data)
        if target:
            target.close()
            name = finish(partial)
            db.executemany("UPDATE media SET object=?,offset=? WHERE key=?", ((name, o, k) for k, o in updates))
    finally:
        if target:
            target.close()
    # Record packs contain only public metadata. Content addressing prevents a later
    # publisher overwriting bytes still referenced by a reader's older revision.
    for original, extent in list(db.execute("SELECT object,max(offset+size) FROM corpus_units GROUP BY object")):
        source = corpus / original
        if source.stat().st_size < extent:
            raise ValueError("Incomplete corpus pack")
        partial = output / "records.tmp"
        os.link(source, partial)
        name = finish(partial)
        db.execute("UPDATE corpus_units SET object=? WHERE object=?", (name, original))
    db.commit()
    # Use SQLite's own SQL quoting. These imports cannot delete or overwrite the
    # online journal, and an already-reviewed unit survives repeated publication.
    max_statement = 0
    with (output / "catalogue.sql").open("w") as sql:
        for statement in db.iterdump():
            if not statement.startswith("INSERT INTO "):
                continue
            table = statement.split('"', 2)[1]
            if table in IMMUTABLE:
                statement = statement.replace("INSERT INTO", "INSERT OR REPLACE INTO", 1)
            elif table == "units":
                statement = statement.replace("INSERT INTO", "INSERT OR IGNORE INTO", 1)
            else:
                continue
            size = len(statement.encode())
            if size > 100_000:
                raise ValueError(f"D1 statement exceeds 100 KB in {table}")
            max_statement = max(max_statement, size)
            sql.write(statement + "\n")
        # Counted in D1 from the rows it now holds, which may include rows earlier publications left.
        sql.write(CORPUS_REFRESH + "\n")
    counts = {table: db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
              for table in ("units", "characters", "corpus_units", "media")}
    (output / "publication.json").write_text(encoded({"counts": counts, "objects": manifest, "review_baselines": baselines,
                                                     "max_statement_bytes": max_statement}) + "\n")
    db.close()
    print(encoded({"sealed": True, "counts": counts, "objects": len(manifest)}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("catalogue", type=Path)
    parser.add_argument("corpus", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    seal(args.catalogue, args.corpus, args.output)
