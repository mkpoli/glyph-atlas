"""Write a publication's answers to R2: the counts the galleries, the browse page and the homepage show.

    ATLAS3_DIR=work/atlas-v3 python3 scripts/atlas3/answers.py [--dry-run]

publish.py runs it after every publish that sent something, so the counts follow each publish.

A publication bumps `generation` in glyph-atlas-v3 from G to G + 1, writes its answers under
`v3/pub/<pub>/`, and then moves `v3/pointer.json` to `{"pub", "through_gen": G}`. A count is the
answer plus the `tally` rows above `through_gen` (decision record 0005). Answers equal to the last
publication's (out/answers-published.json, a digest of every file less its `pub` and of the site values)
publish nothing: no bump, no object, no row.

The counts come from the local copy of glyph-atlas-v3, which every publish replays. Reviews on the
new layout are absorbed by reading their events; until that exists, a database holding events is
refused.

- `counts/<shard>.json.gz`: per character (grapheme id, in shard id % SHARDS), one cell per kind,
  style, state, withheld, witness decade and visual group, with its number of crops. Codes are the
  `codes` table's; a null is unknown.
- `counts/<shard>.json.gz` also holds `families`: the same cells for crops with no character, per
  family their source names (`crops.family`, grapheme id, in shard id % SHARDS).
- `unread.json.gz`: the same cells for crops with no character, per script category (-1 for none).
- `facets.json.gz`: the collected crops (kind 0) per character, book, production, category, review
  priority, state and withheld flag, -1 for an unknown value, with each character's text and grapheme key: the browse
  page's counts by character and book, and the review rounds' counts.
- `corpus.json.gz`: the corpus glyphs (kind 1) per character, production, state and withheld flag, with
  each character's text and grapheme key: the corpus counts by character and material.
- `books.json.gz`: each book's collected crops and corpus glyphs, which the statistics of the
  collection's dates count by period.
- `site` in glyph-atlas-v3: the collection's status (`collection`, read from the collection's working
  folders where they are) and the corpus summary (`corpus_index`, from work/corpus-index/index.json),
  which the status pages serve; a value whose source is not here stays as published.
- `gallery/`: the homepage sample. `sample/<k>.json.gz` holds the corpus glyphs whose shuffle falls
  below SAMPLE_RANGE (1 in 64) and `<group>/<k>.json.gz` the glyphs of a script the corpus holds few
  of (WHOLE), at most PER_CHARACTER of each character, spread evenly over its shuffles, so one
  common syllable cannot fill a group's page. Each glyph has its record copied out of its pack, so a
  page of the gallery is one or two objects where reading the records took a request each. Shard k of a pool
  holds the shuffles from k * range // shards up, by shuffle and id; `index.json.gz` names each pool's
  range, shards and glyphs by category.

A glyph's record is read once from its pack in R2 (an S3 ranged GET with the keys of the rclone
remote) and kept in out/records.sqlite under its place, which names it for good: packs are written
once and never changed.

The `tally` rows a review writes carry the crop's family and witness decade but no visual group, so
counts by visual group stand at the publication until the next one.
"""

from __future__ import annotations

import argparse
import fcntl
import gzip
import hashlib
import hmac
import json
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from publish import BUCKET, OUT, R2_REMOTE, applied, query, upload

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cloudflare_schema import category_of
from publish_collection_status import site_row, status

from glyph_atlas import registry
from glyph_atlas.schema import Licence

# The collection's working folders, which its status is read from.
COLLECTION = Path(__file__).resolve().parents[2] / "work/honkoku-collection"
# The corpus summary `/layers/summary` serves, as the corpus index build writes it.
CORPUS_INDEX = Path(__file__).resolve().parents[2] / "work/corpus-index/index.json"

SHARDS = 256


def public_corpus_index(path: Path, restricted: set[str] | None = None) -> dict:
    """The corpus index summary without the corpora whose source is restricted: those stay off the site.

    Every other corpus is kept as the index wrote it. A dropped corpus's documents come off the total,
    and the characters are counted again from the index's `chars.parquet` over the corpora that remain.
    Its pages come off the total too; an index built before per-corpus page counts cannot say how many
    those are, so its summary goes out without a page total until the index is rebuilt.
    """
    summary = json.loads(path.read_text())
    if restricted is None:
        restricted = {s.id for s in registry.load() if s.licence is Licence.RESTRICTED}
    away = [c for c in summary.get("corpora", []) if c.get("corpus") in restricted]
    if not away:
        return summary
    import pyarrow.parquet as pq

    kept = [c for c in summary["corpora"] if c.get("corpus") not in restricted]
    names = {c["corpus"] for c in kept}
    chars = pq.read_table(path.parent / "chars.parquet", columns=["corpora"]).column("corpora").to_pylist()
    public = {**summary, "corpora": kept,
              "documents": summary["documents"] - sum(c.get("documents") or 0 for c in away),
              "characters": sum(1 for row in chars if names & set((row or "").split(",")))}
    if all("pages" in c for c in away if not c.get("skipped")):
        public["pages"] = summary["pages"] - sum(c.get("pages") or 0 for c in away)
    else:
        del public["pages"]
        print(f"{path} has no page count for {', '.join(c['corpus'] for c in away)}; the corpus summary goes out "
              "without a page total until the index is rebuilt", flush=True)
    return public
PUB_PREFIX = "v3/pub/"
POINTER_PREFIX = "v3/"
CELL = ("kind", "style", "state", "quiz", "decade", "visual_group", "n")
FACET_CELL = ("char", "book", "production", "category", "priority", "state", "quiz", "n")
CORPUS_CELL = ("char", "production", "state", "quiz", "n")
# Floor division, so a year before 1 CE falls in its own decade: SQLite's division truncates.
DECADE = "CASE WHEN year IS NULL THEN NULL WHEN year < 0 THEN (year - 9) / 10 * 10 ELSE year / 10 * 10 END"
# Shuffle is a 31-bit hash; the homepage sample is the corpus glyphs below 1 in 64 of it.
SHUFFLE_RANGE = 2**31
SAMPLE_RANGE = SHUFFLE_RANGE // 64
SAMPLE_SHARDS = 128
WHOLE = ("hangul", "gugyeol", "tangut")
# A WHOLE group's pool keeps this many glyphs of each character. With the ko.wikisource scans a
# Hangul pool of every glyph would hold 467,627, 23,815 of them 이; at 32 it holds 32,810, about the
# size of the 1-in-64 sample, and still holds each of its 2,247 characters.
PER_CHARACTER = 32
# A whole group's shard holds about this many glyphs.
WHOLE_SHARD = 256
# Record fields no listing shows: the inspector's and the image route's (`UNLISTED` in atlas3.ts).
UNLISTED = ("text", "line", "context_image", "context_box", "crop_box", "image_place", "context_image_place",
            "record_place", "slot")
# Records read from R2 at once, and how often one read is tried.
READS = 32
READ_ATTEMPTS = 4


def cells(atlas, key, where):
    out = defaultdict(list)
    for row in atlas.execute(f"SELECT {key}, kind, style, state, quiz, {DECADE}, visual_group, count(*) FROM crops "
                             f"WHERE kind < 2 AND {where} GROUP BY 1, 2, 3, 4, 5, 6, 7"):
        out[row[0]].append(list(row[1:]))
    return out


def grapheme_key(text):
    """A text's grapheme key as the Worker writes it: its code points, `U+306F U+3099`."""
    return " ".join(f"U+{ord(c):04X}" for c in text)


def characters(atlas, ids):
    """Each character's text and the key of the grapheme it is filed under: its family head's, or its own."""
    out = {}
    for char, text, head in atlas.execute(
            "SELECT g.id, g.text, coalesce(h.text, g.text) FROM json_each(?) j JOIN graphemes g ON g.id = j.value "
            "LEFT JOIN graphemes h ON h.id = g.head", (json.dumps(sorted(ids)),)):
        out[str(char)] = [text, grapheme_key(head)]
    return out


def facets(atlas):
    rows = [list(r) for r in atlas.execute(
        "SELECT coalesce(char, -1), book, coalesce(production, -1), coalesce(category, -1), priority, state, quiz, count(*) "
        "FROM crops WHERE kind = 0 GROUP BY 1, 2, 3, 4, 5, 6, 7 ORDER BY 1, 2, 3, 4, 5, 6, 7")]
    return rows, characters(atlas, {r[0] for r in rows if r[0] != -1})


def corpus(atlas):
    rows = [list(r) for r in atlas.execute(
        "SELECT char, coalesce(production, -1), state, quiz, count(*) FROM crops WHERE kind = 1 AND char IS NOT NULL "
        "GROUP BY 1, 2, 3, 4 ORDER BY 1, 2, 3, 4")]
    return rows, characters(atlas, {r[0] for r in rows})


def books(atlas):
    return {str(book): [collected, glyphs] for book, collected, glyphs in atlas.execute(
        "SELECT book, sum(kind = 0), sum(kind = 1) FROM crops WHERE kind < 2 GROUP BY book ORDER BY book")}


def pools(atlas):
    """The homepage sample's glyphs, (id, shuffle, category), by pool: `sample` and each WHOLE group,
    which keeps PER_CHARACTER glyphs of each character spread evenly over its shuffles, so a seed
    anywhere in the range meets common characters as well as rare ones."""
    out, grouped = defaultdict(list), defaultdict(list)
    for crop, shuffle, char, text in atlas.execute(
            "SELECT c.id, c.shuffle, c.char, g.text FROM crops c LEFT JOIN graphemes g ON g.id = c.char WHERE c.kind = 1 "
            "ORDER BY c.shuffle, c.id"):
        category = category_of(text)
        if shuffle < SAMPLE_RANGE:
            out["sample"].append((crop, shuffle, category))
        if category in WHOLE:
            grouped[char].append((crop, shuffle, category))
    # A long run keeps the middle glyph of each of PER_CHARACTER equal stretches of its shuffle order; the
    # first of each would pile every common character's lowest glyph into shard 0.
    for glyphs in grouped.values():
        n = len(glyphs)
        out[glyphs[0][2]] += glyphs if n <= PER_CHARACTER else [
            glyphs[(2 * i + 1) * n // (2 * PER_CHARACTER)] for i in range(PER_CHARACTER)]
    for name in WHOLE:
        if name in out:
            out[name].sort(key=lambda g: (g[1], g[0]))
    return out


def places(atlas, crops):
    """Each crop's record place and source id, from its page's bundle in out/pages."""
    pages = defaultdict(set)
    for crop in crops:
        pages[crop >> 24].add(crop)
    revs = dict(atlas.execute("SELECT p.id, p.rev FROM json_each(?) j JOIN pages p ON p.id = j.value", (json.dumps(sorted(pages)),)))
    out = {}
    for page, wanted in pages.items():
        bundle = json.loads(gzip.decompress((OUT / "pages" / f"{page}-{revs[page]}.json.gz").read_bytes()))
        for entry in bundle["crops"]:
            crop = (page << 24) | entry["slot"]
            if crop in wanted:
                if not entry.get("record_place"):
                    sys.exit(f"crop {crop} ({entry.get('id')}) has no record place in its bundle")
                out[crop] = (tuple(entry["record_place"]), entry["id"])
    if missing := set(crops) - set(out):
        sys.exit(f"{len(missing)} sample glyphs are not in their page bundles: {sorted(missing)[:5]}")
    return out


def signed(method, url, headers, key, secret, region="auto", now=None):
    """`headers` with the AWS signature version 4 an S3 GET of `url` needs, its payload empty."""
    now = now or datetime.now(UTC)
    stamp, day = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
    parts = urllib.parse.urlsplit(url)
    empty = hashlib.sha256(b"").hexdigest()
    headers = {**{k.lower(): v for k, v in headers.items()}, "host": parts.netloc, "x-amz-date": stamp,
               "x-amz-content-sha256": empty}
    names = sorted(headers)
    canonical = "\n".join([method, parts.path or "/", parts.query, *(f"{k}:{headers[k].strip()}" for k in names), "",
                           ";".join(names), empty])
    scope = f"{day}/{region}/s3/aws4_request"
    text = "\n".join(["AWS4-HMAC-SHA256", stamp, scope, hashlib.sha256(canonical.encode()).hexdigest()])
    signing = f"AWS4{secret}".encode()
    for part in (day, region, "s3", "aws4_request"):
        signing = hmac.new(signing, part.encode(), hashlib.sha256).digest()
    signature = hmac.new(signing, text.encode(), hashlib.sha256).hexdigest()
    headers["authorization"] = f"AWS4-HMAC-SHA256 Credential={key}/{scope}, SignedHeaders={';'.join(names)}, Signature={signature}"
    return headers


def s3_reader(endpoint, key, secret):
    """A function that reads one byte range of an object in the site's bucket at an S3 endpoint."""
    def read(name, offset, size):
        url = f"{endpoint.rstrip('/')}/{BUCKET}/{urllib.parse.quote(name)}"
        headers = signed("GET", url, {"range": f"bytes={offset}-{offset + size - 1}"}, key, secret)
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as response:
            return response.read()
    return read


def r2_reader():
    """`s3_reader` for R2, with the endpoint and keys of the rclone remote."""
    dump = subprocess.run(["rclone", "config", "dump"], check=True, capture_output=True, text=True).stdout
    config = json.loads(dump)[R2_REMOTE]
    return s3_reader(config["endpoint"], config["access_key_id"], config["secret_access_key"])


def records(wanted, read):
    """The record text at each place in `wanted` ({place: source id}), from out/records.sqlite or, for a
    place it lacks, from R2 through `read(key, offset, size)`. A read is tried READ_ATTEMPTS times; bytes
    of another length, or a record of another id, stop the run."""
    cache = sqlite3.connect(OUT / "records.sqlite")
    cache.execute("CREATE TABLE IF NOT EXISTS records(object TEXT, offset INTEGER, size INTEGER, text TEXT NOT NULL, "
                  "PRIMARY KEY(object, offset, size)) WITHOUT ROWID")
    found = {}
    for place in wanted:
        row = cache.execute("SELECT text FROM records WHERE object=? AND offset=? AND size=?", place).fetchone()
        if row:
            found[place] = row[0]

    def fetch(place):
        for attempt in range(READ_ATTEMPTS):
            try:
                data = read(*place)
                break
            except OSError:
                if attempt == READ_ATTEMPTS - 1:
                    raise
                time.sleep(2 ** attempt)
        if len(data) != place[2]:
            sys.exit(f"read {len(data)} of {place[2]} bytes at {place[0]}:{place[1]}")
        text = data.decode(errors="replace")
        try:
            record = json.loads(text)
        except ValueError:
            record = None
        if not isinstance(record, dict) or record.get("id") != wanted[place]:
            sys.exit(f"the bytes at {place[0]}:{place[1]} are not the record of {wanted[place]}")
        return place, text

    missing = [p for p in wanted if p not in found]
    if missing:
        print(f"reading {len(missing)} sample records from R2", flush=True)
    with ThreadPoolExecutor(READS) as pool:
        for done, (place, text) in enumerate(pool.map(fetch, missing), 1):
            cache.execute("INSERT OR REPLACE INTO records VALUES(?,?,?,?)", (*place, text))
            found[place] = text
            if done % 1000 == 0:
                cache.commit()
    cache.commit()
    return found


def listed(text):
    record = json.loads(text)
    return {k: v for k, v in record.items() if k not in UNLISTED}


def gallery(atlas, folder, pub, read):
    """Write the homepage sample's shards and index under `folder`; the files written."""
    chosen = pools(atlas)
    every = {g[0] for glyphs in chosen.values() for g in glyphs}
    where = places(atlas, every)
    texts = records({where[c][0]: where[c][1] for c in every}, read)
    pools_index, files = {}, []
    for name in ("sample", *WHOLE):
        glyphs = chosen.get(name, [])
        span = SAMPLE_RANGE if name == "sample" else SHUFFLE_RANGE
        shards = SAMPLE_SHARDS if name == "sample" else max(1, -(-len(glyphs) // WHOLE_SHARD))
        pools_index[name] = {"range": span, "shards": shards, "glyphs": len(glyphs),
                             "categories": dict(sorted(Counter(g[2] for g in glyphs).items()))}
        split = defaultdict(list)
        for crop, shuffle, category in glyphs:
            split[shuffle * shards // span].append([crop, shuffle, category, listed(texts[where[crop][0]])])
        files += [write(folder / "gallery" / name / f"{k}.json.gz", {"pub": pub, "entries": split.get(k, [])})
                  for k in range(shards)]
    files.append(write(folder / "gallery" / "index.json.gz", {"pub": pub, "pools": pools_index}))
    return files, pools_index


def unabsorbed(through):
    """Whether glyph-atlas-v3 holds a review event of a generation from 1 to `through`."""
    return query("glyph-atlas-v3", f"SELECT count(*) AS n FROM events WHERE gen > 0 AND gen <= {through}")[0]["n"] > 0


def answers_digest(folder, site):
    """A digest of a publication's answers: every file under `folder` by its path, less its `pub`, and the
    site values."""
    digest = hashlib.sha256(json.dumps(site, ensure_ascii=False, sort_keys=True).encode())
    for path in sorted(folder.rglob("*.json.gz")):
        body = json.loads(gzip.decompress(path.read_bytes()))
        body.pop("pub", None)
        digest.update(f"{path.relative_to(folder)}\n".encode())
        digest.update(json.dumps(body, ensure_ascii=False, sort_keys=True).encode() + b"\n")
    return digest.hexdigest()


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
    publication(args.dry_run)


def publication(dry_run, read=None):
    """Write and publish the answers; the caller holds out/mirror.lock (publish.py runs this last)."""
    mirror = sqlite3.connect(OUT / "mirror.sqlite")
    if mirror.execute("SELECT 1 FROM sqlite_master WHERE name = 'pending'").fetchone() and \
            mirror.execute("SELECT count(*) FROM pending").fetchone()[0]:
        sys.exit("a publish is unfinished; run publish.py to finish it first")
    pub = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    atlas = sqlite3.connect(OUT / "atlas.sqlite")
    folder = OUT / "pub" / pub
    # The sample's records are read before the bump, so a failed read leaves the generation where it was.
    sampled, pool_sizes = gallery(atlas, folder, pub, read or r2_reader())
    shards = defaultdict(dict)
    for char, rows in cells(atlas, "char", "char IS NOT NULL").items():
        shards[char % SHARDS][char] = rows
    families = defaultdict(dict)
    for family, rows in cells(atlas, "family", "char IS NULL AND family IS NOT NULL").items():
        families[family % SHARDS][family] = rows
    files = [write(folder / "counts" / f"{shard}.json.gz", {"pub": pub, "cell": CELL, "chars": shards.get(shard, {}),
                                                            "families": families.get(shard, {})})
             for shard in range(SHARDS)]
    unread = cells(atlas, "category", "char IS NULL")
    # A crop with no category is filed under -1, as `tally` writes unknown.
    top = [write(folder / "unread.json.gz", {"pub": pub, "cell": CELL,
                                              "categories": {str(-1 if k is None else k): v for k, v in unread.items()}})]
    browse, named = facets(atlas)
    top.append(write(folder / "facets.json.gz", {"pub": pub, "cell": FACET_CELL, "cells": browse, "chars": named}))
    glyphs, glyph_chars = corpus(atlas)
    top.append(write(folder / "corpus.json.gz", {"pub": pub, "cell": CORPUS_CELL, "cells": glyphs, "chars": glyph_chars}))
    top.append(write(folder / "books.json.gz", {"pub": pub, "cell": ["collected", "corpus"], "books": books(atlas)}))
    # The site's values the status pages show: the collection's progress and the corpus summary.
    # Each only where its source is: a run away from the collection's working folders (a Codespace) keeps
    # the published collection status rather than one read from no folders.
    site = {**({"collection": status()} if COLLECTION.is_dir() else {}),
            **({"corpus_index": public_corpus_index(CORPUS_INDEX)} if CORPUS_INDEX.exists() else {})}
    digest = answers_digest(folder, site)
    last = OUT / "answers-published.json"
    published = json.loads(last.read_text()) if last.exists() else {}
    if published.get("digest") == digest:
        shutil.rmtree(folder)
        print(f"the answers equal publication {published['pub']}'s; nothing published", flush=True)
        return
    through = query("glyph-atlas-v3", "SELECT gen FROM generation WHERE id = 0")[0]["gen"]
    # Every review up to G is in the answers. None is absorbed yet, so a database holding one is refused,
    # before the bump and again after it, since a review can land at G until the bump. The history load
    # writes its events at generation 0, and the local copy holds them: they count as absorbed.
    if unabsorbed(through):
        sys.exit("glyph-atlas-v3 holds review events, and absorbing them is not built yet; nothing published")
    if not dry_run:
        # Reviews written after the bump carry the next generation, so the answers stand through G. The
        # bump names G and stamps this publication in the same statement, so a resent statement cannot
        # bump twice and only the run whose stamp is there made it.
        query("glyph-atlas-v3", f"UPDATE generation SET gen = {through + 1} WHERE id = 0 AND gen = {through}; "
                                f"INSERT INTO imports(stamp, at) SELECT 'pub:{pub}', unixepoch() WHERE changes() = 1")
        if not applied("glyph-atlas-v3", f"pub:{pub}"):
            sys.exit(f"another publication bumped generation {through}; nothing published")
    if unabsorbed(through):
        sys.exit("glyph-atlas-v3 holds review events, and absorbing them is not built yet; the pointer stays")

    pointer = write(OUT / "pub" / f"pointer-{pub}" / "pointer.json", {"pub": pub, "through_gen": through})
    characters_counted = sum(len(s) for s in shards.values())
    print(f"pub {pub} through generation {through}: {characters_counted} characters in {SHARDS} shards, "
          f"{sum(len(s) for s in families.values())} families of crops with no character, "
          f"{sum(len(v) for v in unread.values())} unread cells, {len(browse)} browse cells, {len(glyphs)} corpus cells, "
          f"gallery {', '.join(f'{k} {v['glyphs']}' for k, v in pool_sizes.items())} -> {folder}", flush=True)
    if dry_run:
        print("dry run: nothing uploaded; the generation was not bumped", flush=True)
        return
    upload([f for f in files if f.parent.name == "counts"], f"{PUB_PREFIX}{pub}/counts/")
    upload(top, f"{PUB_PREFIX}{pub}/")
    for name in pool_sizes:
        upload([f for f in sampled if f.parent.name == name], f"{PUB_PREFIX}{pub}/gallery/{name}/")
    upload([f for f in sampled if f.name == "index.json.gz"], f"{PUB_PREFIX}{pub}/gallery/")
    if site:
        query("glyph-atlas-v3", "; ".join(site_row(k, v) for k, v in site.items()))
    # The pointer moves last, once everything it names is in place.
    upload([pointer], POINTER_PREFIX)
    last.write_text(json.dumps({"pub": pub, "digest": digest}) + "\n")
    print(f"v3/pointer.json names {pub}", flush=True)


if __name__ == "__main__":
    main()
