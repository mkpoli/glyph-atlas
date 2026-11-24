"""Write a publication's answers to R2: the counts the galleries, the browse page and the homepage show.

    ATLAS3_DIR=work/atlas-v3 python3 scripts/atlas3/answers.py [--dry-run]
    ATLAS3_DIR=work/atlas-v3 python3 scripts/atlas3/answers.py --seed [--dry-run]

publish.py runs it after every publish that sent something, so the counts follow each publish.

A publication bumps `generation` in glyph-atlas-v3 from G to G + 1, writes its answers under
`v3/pub/<pub>/`, and then moves `v3/pointer.json` to `{"pub", "through_gen": G}`. A count is the
answer plus the `tally` rows above `through_gen` (decision record 0005). With no crop listed in
`touched`, answers equal to the last publication's (out/answers-published.json, a digest of every file
less its `pub` and of the site values) publish nothing: no bump, no object, no row.

The counts come from the local copy of glyph-atlas-v3, which every publish replays and which takes
the site's own changes here. The Worker lists every crop whose row it changes in `touched`, with the
generation of the change (migration 0018). The bump to G + 1 is a file D1 applies whole, which also
copies each crop listed through G into `absorbing`, as generation G left its row, and takes it off the
list; a crop changed after it carries G + 1, is listed again and waits for the next publication, its
change counted from `tally` meanwhile. The local copy then
takes each copied row whole (`publish.absorb`), with the revision the offline store counts for the
crop and whether it keeps a reviewer's style, as a publish's read-back takes a guarded crop's, the
base a forms decision moved it from (`form_bases`), which the publisher reads before it stages a
followed glyph, the graphemes those rows name and the codes D1 holds and the local copy lacks; a
crop D1 no longer holds leaves the local copy. `absorbing` is emptied once the local copy has committed, so a publication that
stopped after its bump is finished by the next run of this script or of publish.py, which applies what
`absorbing` still holds before anything else. The answers are written after that, from the
local copy as it then stands. A dry run reads how many crops are listed, writes the answers of the
local copy as it is, and changes neither database.

`--seed` lists, once, the crops the Worker changed before it kept the list (migration 0019, stamp
`migration:0019`): every crop an event of a generation above 0 names, and every corpus glyph a forms
decision moved (one with a `form_bases` row in D1 or in the local copy) whose base, character or
revision the two hold differently, or which only one of them holds. It runs after the Worker that lists touched crops is deployed, and needs migration
0018's tables; with `--dry-run` it prints how many crops each of the two would list and writes
nothing. A publication is refused until the seed's stamp is there, since its answers would count
none of those crops' changes.

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
  each character's text and grapheme key: the corpus counts by character and material. `filed` holds the
  crops with no character (both kinds) per family their source names, whose texts and keys stand beside
  the characters': what a grapheme's total counts beside its characters' crops.
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

from publish import (
    BUCKET,
    CF,
    CROP_COLS,
    LOGICAL,
    OUT,
    OWN_STYLE,
    R2_REMOTE,
    absorb,
    applied,
    lit,
    missing_only,
    query,
    send,
    upload,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cloudflare_schema import category_of
from publish_collection_status import site_row, status

# The collection's working folders, which its status is read from.
COLLECTION = Path(__file__).resolve().parents[2] / "work/honkoku-collection"
# The corpus summary `/layers/summary` serves, as the corpus index build writes it.
CORPUS_INDEX = Path(__file__).resolve().parents[2] / "work/corpus-index/index.json"

SHARDS = 256
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
WHOLE = ("hangul", "gugyeol", "tangut", "seal")
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
DATABASE = "glyph-atlas-v3"
# The migration that lists the crops changed before the Worker kept the list, and the stamp D1's
# `imports` holds once it has applied.
SEED_STAMP = "migration:0019"
SEED = CF / "migrations-atlas" / "0019_touched_seed.sql"
# A listed crop's row as `absorbing` keeps it: its columns by name, the revision the offline store counts
# for it (`logical`), whether it keeps a reviewer's style (`own_style`), and the character a forms
# decision moved it from (`base`, which may be null) where it holds one (`based`), each read from the crop `c`.
SNAPSHOT_ROW = ("json_object(" + ",".join(f"'{c}',c.{c}" for c in CROP_COLS)
                + f",'logical',{LOGICAL},'own_style',{OWN_STYLE},'based',EXISTS(SELECT 1 FROM form_bases b WHERE b.crop=c.id),"
                "'base',(SELECT b.char FROM form_bases b WHERE b.crop=c.id))")


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


def filed(atlas):
    return [list(r) for r in atlas.execute(
        "SELECT family, kind, count(*) FROM crops WHERE kind < 2 AND char IS NULL AND family IS NOT NULL GROUP BY 1, 2 ORDER BY 1, 2")]


def corpus(atlas):
    rows = [list(r) for r in atlas.execute(
        "SELECT char, coalesce(production, -1), state, quiz, count(*) FROM crops WHERE kind = 1 AND char IS NOT NULL "
        "GROUP BY 1, 2, 3, 4 ORDER BY 1, 2, 3, 4")]
    unnamed = filed(atlas)
    return rows, unnamed, characters(atlas, {r[0] for r in rows} | {r[0] for r in unnamed})


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


def bump(pub, through):
    """Bump the generation from `through` to the next and copy the listed crops, in one file D1 applies
    whole; whether this publication's stamp is there, which only the run that bumped holds. The file
    stamps the publication while the generation is `through`, and every other statement waits on that
    stamp: the bump, the copy of the row of every crop listed through `through` into `absorbing`, and
    those crops' removal from the list. The copy is taken with the bump, so it holds each crop as
    generation `through` left it: a write that lands after the file carries the next generation, lists
    its crop again, and is counted from the `tally` rows above `through` until the next publication takes
    it. No statement reads `changes()`, and each finds nothing left to do where it has run before."""
    stamp = f"pub:{pub}"
    stamped = f"EXISTS (SELECT 1 FROM imports WHERE stamp = {lit(stamp)})"
    path = OUT / "pub" / f"pointer-{pub}" / "bump.sql"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join([
        f"INSERT OR IGNORE INTO imports(stamp, at) SELECT {lit(stamp)}, unixepoch() FROM generation WHERE id = 0 AND gen = {through};",
        f"UPDATE generation SET gen = {through + 1} WHERE id = 0 AND gen = {through} AND {stamped};",
        (f"INSERT OR REPLACE INTO absorbing(crop, through, row) SELECT t.crop, {through}, "
         f"CASE WHEN c.id IS NULL THEN NULL ELSE {SNAPSHOT_ROW} END "
         f"FROM touched t LEFT JOIN crops c ON c.id = t.crop WHERE t.gen <= {through} AND {stamped};"),
        f"DELETE FROM touched WHERE gen <= {through} AND {stamped};"]) + "\n")
    return send(DATABASE, path, stamp)


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
    parser.add_argument("--dry-run", action="store_true",
                        help="write the answers of the local copy as it is; change neither database and upload nothing")
    parser.add_argument("--seed", action="store_true",
                        help="list the crops changed before the Worker listed them (migration 0019), once, and publish "
                             "nothing; with --dry-run, print how many it would list")
    args = parser.parse_args()
    lock = open(OUT / "mirror.lock", "w")  # noqa: SIM115 -- held until the process exits
    fcntl.flock(lock, fcntl.LOCK_EX)
    if args.seed:
        seed(args.dry_run)
    else:
        publication(args.dry_run)


# A followed glyph as the seed compares it: its base, the character a follow sets, and its revision,
# which a follow has raised since 2026-10-23. Glyphs one query reads from D1, and crops one statement
# of the seed lists (D1 refuses a statement over 100 KB).
FOLLOWED = "SELECT b.crop, b.char AS base, c.char, c.revision FROM form_bases b LEFT JOIN crops c ON c.id = b.crop"
FOLLOWED_CHUNK = 25000
SEED_ROWS = 4000


def moved(atlas):
    """The corpus glyphs a forms decision moved that D1 and the local copy (`atlas`) hold differently,
    in order, and how many D1 holds: a glyph with a `form_bases` row in either whose base, character or
    revision differs, or which only one of the two holds. A follow records no event and no revision of
    the store's, so the comparison is what finds it; a glyph both hold alike has not moved since the
    local copy took it."""
    local = {crop: tuple(rest) for crop, *rest in atlas.execute(FOLLOWED)}
    differing, held, last = set(), 0, -1
    while rows := query(DATABASE, f"{FOLLOWED} WHERE b.crop > {last} ORDER BY b.crop LIMIT {FOLLOWED_CHUNK}"):
        for r in rows:
            if local.pop(r["crop"], None) != (r["base"], r["char"], r["revision"]):
                differing.add(r["crop"])
        held, last = held + len(rows), rows[-1]["crop"]
    return sorted(differing | set(local)), held


def seed(dry_run):
    """List in `touched` the crops the Worker changed before it kept the list (migration 0019), once: the
    file goes to D1 whole with its stamp, and a database that holds the stamp is left as it is. It runs
    once the Worker that lists touched crops is deployed, so a crop changed before the deploy is found
    here and one changed after it is listed by the Worker. The reviewed crops are the migration's own
    statement; the followed glyphs are those `moved` finds, listed by id. A dry run prints how many
    crops each would list and writes nothing."""
    if not query(DATABASE, "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'touched'"):
        sys.exit(f"{DATABASE} has no `touched` to list crops in (migration 0018); run publish.py --migrate first")
    listed = "SELECT count(*) AS n FROM touched"
    if applied(DATABASE, SEED_STAMP):
        print(f"the seed is in place (stamp {SEED_STAMP}); `touched` lists {query(DATABASE, listed)[0]['n']} crops", flush=True)
        return
    followed, held = moved(sqlite3.connect(f"file:{OUT / 'atlas.sqlite'}?mode=ro", uri=True))
    if dry_run:
        reviewed = query(DATABASE, "SELECT count(DISTINCT crop) AS n FROM events WHERE gen > 0")[0]["n"]
        print(f"dry run: the seed would list {reviewed} crops named by events of a generation above 0 and {len(followed)} "
              f"followed glyphs the local copy holds differently ({held} hold a `form_bases` row in {DATABASE}), a crop "
              f"of both once; `touched` lists {query(DATABASE, listed)[0]['n']} now, and nothing was written", flush=True)
        return
    path = OUT / "publish" / "0019.sql"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(missing_only(SEED.read_text(), lambda table: set())
                    + "".join("INSERT OR IGNORE INTO touched(crop, gen) VALUES "
                              + ",".join(f"({crop},0)" for crop in followed[at:at + SEED_ROWS]) + ";\n"
                              for at in range(0, len(followed), SEED_ROWS))
                    + f"\nINSERT OR IGNORE INTO imports(stamp, at) VALUES({lit(SEED_STAMP)}, unixepoch());\n")
    if not send(DATABASE, path, SEED_STAMP):
        sys.exit("the seed did not apply; rerun")
    print(f"the seed applied (stamp {SEED_STAMP}); `touched` lists {query(DATABASE, listed)[0]['n']} crops", flush=True)


def written(atlas, folder, pub, read):
    """Write a publication's answers under `folder` from the local copy as it stands: its files by where
    they go (`counts`, `top`, `sampled`), the sample's pools, the site values, the digest of it all and a
    line of what was counted."""
    sampled, pool_sizes = gallery(atlas, folder, pub, read)
    shards = defaultdict(dict)
    for char, rows in cells(atlas, "char", "char IS NOT NULL").items():
        shards[char % SHARDS][char] = rows
    families = defaultdict(dict)
    for family, rows in cells(atlas, "family", "char IS NULL AND family IS NOT NULL").items():
        families[family % SHARDS][family] = rows
    counts = [write(folder / "counts" / f"{shard}.json.gz", {"pub": pub, "cell": CELL, "chars": shards.get(shard, {}),
                                                             "families": families.get(shard, {})})
              for shard in range(SHARDS)]
    unread = cells(atlas, "category", "char IS NULL")
    # A crop with no category is filed under -1, as `tally` writes unknown.
    top = [write(folder / "unread.json.gz", {"pub": pub, "cell": CELL,
                                              "categories": {str(-1 if k is None else k): v for k, v in unread.items()}})]
    browse, named = facets(atlas)
    top.append(write(folder / "facets.json.gz", {"pub": pub, "cell": FACET_CELL, "cells": browse, "chars": named}))
    glyphs, unnamed, glyph_chars = corpus(atlas)
    top.append(write(folder / "corpus.json.gz", {"pub": pub, "cell": CORPUS_CELL, "cells": glyphs, "filed": unnamed, "chars": glyph_chars}))
    top.append(write(folder / "books.json.gz", {"pub": pub, "cell": ["collected", "corpus"], "books": books(atlas)}))
    # The site's values the status pages show: the collection's progress and the corpus summary.
    # Each only where its source is: a run away from the collection's working folders (a Codespace) keeps
    # the published collection status rather than one read from no folders.
    site = {**({"collection": status()} if COLLECTION.is_dir() else {}),
            **({"corpus_index": json.loads(CORPUS_INDEX.read_text())} if CORPUS_INDEX.exists() else {})}
    counted = (f"{sum(len(s) for s in shards.values())} characters in {SHARDS} shards, "
               f"{sum(len(s) for s in families.values())} families of crops with no character, "
               f"{sum(len(v) for v in unread.values())} unread cells, {len(browse)} browse cells, {len(glyphs)} corpus cells, "
               f"gallery {', '.join(f'{k} {v['glyphs']}' for k, v in pool_sizes.items())} -> {folder}")
    return {"counts": counts, "top": top, "sampled": sampled, "pools": pool_sizes, "site": site,
            "digest": answers_digest(folder, site), "counted": counted}


def publication(dry_run, read=None):
    """Write and publish the answers; the caller holds out/mirror.lock (publish.py runs this last)."""
    mirror = sqlite3.connect(OUT / "mirror.sqlite")
    if mirror.execute("SELECT 1 FROM sqlite_master WHERE name = 'pending'").fetchone() and \
            mirror.execute("SELECT count(*) FROM pending").fetchone()[0]:
        sys.exit("a publish is unfinished; run publish.py to finish it first")
    # Until the seed has run, `touched` lacks the crops reviewed before the Worker listed them, and the
    # answers would count none of those reviews while the pointer moved past their `tally` rows.
    if not applied(DATABASE, SEED_STAMP):
        unseeded = (f"{DATABASE} has no stamp {SEED_STAMP}: the crops changed before the Worker listed them are not in "
                    "`touched`. Deploy the Worker that lists touched crops, then run answers.py --seed")
        if not dry_run:
            sys.exit(f"{unseeded}; nothing published")
        print(f"{unseeded}; a publication is refused until then", flush=True)
    pub = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    atlas = sqlite3.connect(OUT / "atlas.sqlite")
    folder = OUT / "pub" / pub
    read = read or r2_reader()
    last = OUT / "answers-published.json"
    published = json.loads(last.read_text()) if last.exists() else {}
    if dry_run:
        # The answers of the local copy as it is: the listed crops are counted, and nothing is taken.
        waiting = query(DATABASE, "SELECT (SELECT count(*) FROM touched) AS listed, (SELECT count(*) FROM absorbing) AS copied, "
                                  "(SELECT gen FROM generation WHERE id = 0) AS gen")[0]
        built = written(atlas, folder, pub, read)
        print(f"pub {pub} at generation {waiting['gen']}: {built['counted']}", flush=True)
        print(f"dry run: {waiting['listed']} crops are listed for the next publication and {waiting['copied']} wait in "
              f"`absorbing`; none was taken, nothing was uploaded and the generation was not bumped", flush=True)
        if not waiting["listed"] and not waiting["copied"] and published.get("digest") == built["digest"]:
            shutil.rmtree(folder)
            print(f"the answers equal publication {published['pub']}'s; a publication would publish nothing", flush=True)
        return
    # What a publication that stopped after its bump left in `absorbing` goes into the local copy first.
    absorb(atlas, mirror)
    built = None
    if not query(DATABASE, "SELECT crop FROM touched LIMIT 1"):
        # No crop is listed: the sample's records are read before the bump, so a failed read leaves the
        # generation where it was, and answers equal to the last publication's publish nothing.
        built = written(atlas, folder, pub, read)
        if published.get("digest") == built["digest"]:
            shutil.rmtree(folder)
            print(f"the answers equal publication {published['pub']}'s; nothing published", flush=True)
            return
    through = query(DATABASE, "SELECT gen FROM generation WHERE id = 0")[0]["gen"]
    # Reviews written after the bump carry the next generation, so the answers stand through G. The
    # bump's file stamps this publication and copies every crop the site changed through G, a crop listed
    # while the answers above were being written among them; only the run whose stamp is there made it.
    if not bump(pub, through):
        sys.exit(f"another publication bumped generation {through}; nothing published")
    # The local copy takes the copied crops, and the answers are then written from it as it stands with
    # them. A read of the sample that fails here leaves the bump in place, and the next run publishes
    # through the generation after it.
    if absorb(atlas, mirror) or built is None:
        if built:
            shutil.rmtree(folder)
        built = written(atlas, folder, pub, read)

    pointer = write(OUT / "pub" / f"pointer-{pub}" / "pointer.json", {"pub": pub, "through_gen": through})
    print(f"pub {pub} through generation {through}: {built['counted']}", flush=True)
    upload(built["counts"], f"{PUB_PREFIX}{pub}/counts/")
    upload(built["top"], f"{PUB_PREFIX}{pub}/")
    for name in built["pools"]:
        upload([f for f in built["sampled"] if f.parent.name == name], f"{PUB_PREFIX}{pub}/gallery/{name}/")
    upload([f for f in built["sampled"] if f.name == "index.json.gz"], f"{PUB_PREFIX}{pub}/gallery/")
    if built["site"]:
        query(DATABASE, "; ".join(site_row(k, v) for k, v in built["site"].items()))
    # The pointer moves last, once everything it names is in place.
    upload([pointer], POINTER_PREFIX)
    last.write_text(json.dumps({"pub": pub, "digest": built["digest"]}) + "\n")
    print(f"v3/pointer.json names {pub}", flush=True)


if __name__ == "__main__":
    main()
