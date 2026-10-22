"""The alias files: every text id known at the cutover, mapped to its integer crop id, as R2 shards.

    ATLAS3_DIR=work/atlas-v3 python3 scripts/atlas3/aliases.py [--send]

Old crop URLs name a text id. The map takes every text id in `out/mirror.sqlite` as it is, and every
source of `source-unit_redirects.sqlite` by following its redirects to the first text id the mirror
has. A chain that comes back to a text id it already passed stops the run before anything is written;
one that ends at a text id the mirror lacks is counted as gone and left out. Shard
`v3/aliases/<first three hex digits of the text id's SHA-256>.json.gz` holds `{text id: crop id}`.
The set is closed at the cutover: crops published later are linked by their integer id.

Without `--send` the run only counts. With it, each shard whose bytes changed is written under
`out/aliases/` and listed in `out/aliases/unsent` until it is uploaded, so a run after a publish
uploads only the shards its new crops changed, and a run with nothing new uploads none.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import sqlite3
import sys
from collections import defaultdict

from build import HERE, OUT

PREFIX = "v3/aliases/"
EXAMPLES = 5


def shard_name(text):
    return hashlib.sha256(text.encode()).hexdigest()[:3]


def resolve(mirror, redirects):
    """Map each redirect source the mirror lacks to (crop id, steps), or to None when its chain ends at a
    text id the mirror lacks. `mirror(text)` returns a crop id or None. Exits 1 on a cycle."""
    resolved, cycles = {}, []
    for source in redirects:
        if mirror(source) is not None:
            continue
        chain, at = [source], redirects[source]
        while True:
            crop = mirror(at)
            if crop is not None:
                resolved[source] = (crop, len(chain))
                break
            if at in chain:
                cycles.append(chain[chain.index(at):] + [at])
                break
            if at not in redirects:
                resolved[source] = None
                break
            chain.append(at)
            at = redirects[at]
    if cycles:
        examples = "; ".join(" -> ".join(cycle) for cycle in cycles[:EXAMPLES])
        sys.exit(f"{len(cycles)} redirect chains loop, nothing written: {examples}")
    return resolved


def build(mirror_db, redirects):
    """Return the shards {name: {text id: crop id}} and the counts of each kind of alias."""
    def mirror(text):
        row = mirror_db.execute("SELECT id FROM ids WHERE text=?", (text,)).fetchone()
        return row[0] if row else None

    resolved = resolve(mirror, redirects)
    shards = defaultdict(dict)
    direct = 0
    for text, crop in mirror_db.execute("SELECT text, id FROM ids"):
        shards[shard_name(text)][text] = crop
        direct += 1
    gone = sorted(source for source, found in resolved.items() if found is None)
    for source, found in resolved.items():
        if found is not None:
            shards[shard_name(source)][source] = found[0]
    counts = {
        "mirror": direct,
        "redirects": len(redirects),
        "redirects already in the mirror": len(redirects) - len(resolved),
        "redirected": len(resolved) - len(gone),
        "redirected through more than one step": sum(1 for found in resolved.values() if found and found[1] > 1),
        "gone": len(gone),
    }
    return shards, counts, gone


def encode(shard):
    return gzip.compress(json.dumps(dict(sorted(shard.items())), ensure_ascii=False, separators=(",", ":")).encode(),
                         mtime=0)


def write(shards):
    """Write each shard whose bytes differ from its copy under out/aliases/, through a `.part` file, after
    listing it in out/aliases/unsent; return the paths of every shard listed there."""
    folder = OUT / "aliases"
    folder.mkdir(parents=True, exist_ok=True)
    unsent = folder / "unsent"
    listed = set(unsent.read_text().split()) if unsent.exists() else set()
    for name, shard in sorted(shards.items()):
        path = folder / f"{name}.json.gz"
        data = encode(shard)
        if path.exists() and path.read_bytes() == data:
            continue
        if path.name not in listed:
            listed.add(path.name)
            with unsent.open("a") as f:
                f.write(path.name + "\n")
        part = path.with_suffix(".part")
        part.write_bytes(data)
        os.replace(part, path)
    return [folder / name for name in sorted(listed)]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--send", action="store_true", help="write the shards and upload them; without it, only count")
    args = parser.parse_args()
    mirror_db = sqlite3.connect(f"file:{OUT / 'mirror.sqlite'}?mode=ro", uri=True)
    source = sqlite3.connect(f"file:{HERE / 'source-unit_redirects.sqlite'}?mode=ro", uri=True)
    redirects = dict(source.execute("SELECT id, target FROM unit_redirects"))
    shards, counts, gone = build(mirror_db, redirects)
    for label, n in counts.items():
        print(f"{n:>10,} {label}", flush=True)
    if gone:
        print(f"gone, for example: {', '.join(gone[:EXAMPLES])}", flush=True)
    sizes = {name: len(encode(shard)) for name, shard in shards.items()}
    largest = max(sizes, key=sizes.get)
    print(f"{sum(len(s) for s in shards.values()):,} aliases in {len(shards):,} shards, {sum(sizes.values()):,} bytes; "
          f"largest {largest}.json.gz, {len(shards[largest]):,} aliases, {sizes[largest]:,} bytes", flush=True)
    if not args.send:
        print("dry run: nothing written or uploaded", flush=True)
        return
    from publish import upload

    paths = write(shards)
    upload(paths, PREFIX)
    (OUT / "aliases" / "unsent").unlink(missing_ok=True)
    print(f"{len(paths)} of {len(shards)} shards uploaded to {PREFIX}", flush=True)


if __name__ == "__main__":
    main()
