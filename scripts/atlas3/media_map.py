"""The image key map: where each published image sits in its pack, as R2 shards.

    ATLAS3_DIR=work/atlas-v3 python3 scripts/atlas3/media_map.py <media.sqlite> [--dry-run]

An image's URL is `/atlas/media/<key>.webp`, its key the SHA-256 the exporter named it by. The map
holds one shard per first three hex digits of the key, `v3/media/<prefix>.json.gz`:
`{"objects": [pack, ...], "keys": {<rest of key>: [object index, offset, size]}}`, with a fourth
member for a content type other than image/webp. A key's place never changes: a run merges the keys
of a `media` table into the local copies under `out/media/` and uploads the shards that changed. The
publisher does the same with a catalogue's `media` table, and takes the images of withdrawn crops
out (`remove`), both only once its run is recorded as pending, so a run that stops before then
leaves the shards as they were.
"""

from __future__ import annotations

import argparse
import fcntl
import gzip
import json
import os
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

from build import OUT
from pack_names import addressed

PREFIX = "v3/media/"
WEBP = "image/webp"


def shard_path(prefix):
    return OUT / "media" / f"{prefix}.json.gz"


def read_shard(prefix):
    path = shard_path(prefix)
    if not path.exists():
        return {"objects": [], "keys": {}}
    return json.loads(gzip.decompress(path.read_bytes()))


def grow(rows):
    """The local shards that (key, object, offset, size, content_type) rows add keys to, as merged, and
    the rows whose keys are new. A key already mapped elsewhere, or a new key in a pack not named by its
    content, stops the run before any shard is written: a key names one image."""
    by_prefix = defaultdict(list)
    for row in rows:
        by_prefix[row[0][:3]].append(tuple(row))
    grown, fresh = {}, []
    for prefix, members in sorted(by_prefix.items()):
        shard = read_shard(prefix)
        objects = {name: i for i, name in enumerate(shard["objects"])}
        grew = False
        for key, obj, offset, size, content_type in members:
            if key[3:] not in shard["keys"] and not addressed(obj):
                sys.exit(f"image {key} is new and sits in {obj}, a numbered pack the bucket may hold with other bytes; "
                         "name the catalogue's packs by their content first (address_packs.py); nothing written")
            if obj not in objects:
                objects[obj] = len(shard["objects"])
                shard["objects"].append(obj)
            place = [objects[obj], offset, size] + ([] if content_type in (None, WEBP) else [content_type])
            old = shard["keys"].get(key[3:])
            if old is None:
                shard["keys"][key[3:]] = place
                grew = True
                fresh.append((key, obj, offset, size, content_type))
            elif [shard["objects"][old[0]], *old[1:]] != [obj, offset, size, *place[3:]]:
                sys.exit(f"image {key} is mapped to {shard['objects'][old[0]]} {old[1:]} and now to {obj} {offset} {size}; nothing written")
        if grew:
            grown[prefix] = shard
    return grown, fresh


def merge(rows, save=True):
    """Merge (key, object, offset, size, content_type) rows into the local shards and return the number
    of new keys and of shards they fall in."""
    grown, fresh = grow(rows)
    if save and grown:
        write(grown)
    return len(fresh), len(grown)


def write(shards):
    """Write changed shards to their local copies, each listed in `out/media/unsent` until `send` has
    uploaded it, so a run that stops between the two leaves it for the next."""
    (OUT / "media").mkdir(parents=True, exist_ok=True)
    with open(OUT / "media" / "unsent", "a") as unsent:
        unsent.write("".join(f"{prefix}\n" for prefix in shards))
    for prefix, shard in shards.items():
        # Whole or not at all: a cut-off shard uploaded later would stop every image in it.
        part = shard_path(prefix).with_suffix(".part")
        part.write_bytes(gzip.compress(json.dumps(shard, separators=(",", ":")).encode()))
        os.replace(part, shard_path(prefix))


def remove(keys):
    """Take image keys out of the local shards and return how many were mapped. A pack an image sat in
    stays in its shard's `objects`, so every other key keeps its index."""
    by_prefix = defaultdict(list)
    for key in keys:
        by_prefix[key[:3]].append(key)
    changed, gone = {}, 0
    for prefix, members in sorted(by_prefix.items()):
        shard = read_shard(prefix)
        found = [key for key in members if shard["keys"].pop(key[3:], None) is not None]
        if found:
            changed[prefix] = shard
            gone += len(found)
    if changed:
        write(changed)
    return gone


def send(upload):
    """Upload the shards listed in `out/media/unsent` with `upload(files, prefix)`, then clear the list."""
    unsent = OUT / "media" / "unsent"
    if not unsent.exists():
        return 0
    prefixes = sorted(set(unsent.read_text().split()))
    missing = [prefix for prefix in prefixes if not shard_path(prefix).exists()]
    if missing:
        sys.exit(f"out/media/unsent names shards that were never written ({', '.join(missing[:5])}); "
                 "merge their media table again, then remove them from the list")
    upload([shard_path(prefix) for prefix in prefixes], PREFIX)
    unsent.unlink()
    return len(prefixes)


def main():
    from publish import upload  # publish imports this module, so it is read here

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("media", type=Path, help="an SQLite file with a media(key, object, offset, size, content_type) table")
    parser.add_argument("--dry-run", action="store_true", help="count what would change and write nothing")
    args = parser.parse_args()
    lock = open(OUT / "mirror.lock", "w")  # noqa: SIM115 -- held until the process exits
    fcntl.flock(lock, fcntl.LOCK_EX)
    src = sqlite3.connect(f"file:{args.media}?mode=ro", uri=True)
    rows = src.execute("SELECT key, object, offset, size, content_type FROM media")
    new, shards = merge(rows, save=not args.dry_run)
    print(f"{new} new images in {shards} shards", flush=True)
    if args.dry_run:
        print("dry run: nothing written or uploaded", flush=True)
        return
    print(f"{send(upload)} shards uploaded", flush=True)


if __name__ == "__main__":
    main()
