"""Move the character reference data onto the new layout (decision record 0007).

    ATLAS3_DIR=work/atlas-v3 python3 scripts/atlas3/reference.py          # translate, report, send nothing
    ATLAS3_DIR=work/atlas-v3 python3 scripts/atlas3/reference.py --send   # and load it
    ATLAS3_DIR=work/atlas-v3 python3 scripts/atlas3/reference.py --only components [--send]
    python3 scripts/atlas3/reference.py --check https://glyphatlas.org      # ask the site the PROBES

Reads the reference input (`glyph_atlas.reference_input`, which `export_graphemes.py`,
`export_character_variants.py` and `export_components_cloudflare.py` write) and writes two things.

R2 objects under `v3/reference/`, gzipped JSON, each read by key:

- `char/<code point>.json.gz` (code points joined by `-`): a card's `data` and `detail`, its grapheme
  id, its 異体字 edges from both ends with the families of the cards they name (`variant_families`, as
  in a summary shard), its derived forms with the component pairs their substitutions
  rest on, the graphemes it writes, and the writers of its family head with `shared` worked out.
  `ids` gives the grapheme id of every character the object names that has one. A visual group is
  keyed by its `visual_group` code (`codes`), and its samples name crops by integer id: a sample
  whose crop the mirror or `crops` lacks leaves the list, counted in the report.
- `summary/<shard>.json.gz`: every card's `data`, as `{family head: {code point: data}}`, in the shard
  of its family head (SUMMARY_DIGITS), so a family's members are one read.
- `readings/<shard>.json.gz`: each kana reading of `data/vocab/kanji-readings.json`
  (scripts/build_kanji_readings.py) with its kanji, most attested first, and the families of the first
  READING_CARDS of them, as `{reading: {"kanji": [...], "families": {head: {code point: data}}}}`, in
  the shard of the reading (SUMMARY_DIGITS). A kana search reads the one shard for the kanji it lists.
- `ids/<shard>.json.gz`: each character's ideographic descriptions and regional shapes, by the
  character itself, in the shard of the character (IDS_DIGITS).
- `names.json.gz`: every card's name and how often it occurs, for a search by name or code point.
  `names` is `[code point, name, occurrences]` for the cards whose name says more than
  `<prefix>IDEOGRAPH-<hex of its code point>`; `plain` gives the rest as `[prefix, code points,
  occurrences]`, code points as steps, the name being the prefix and the hex.
- `list/graphemes/<n>.json.gz` and `list/ligatures/<n>.json.gz`: the cards of families of several
  characters, and the ligatures, by code point, LIST_PAGE to an object.
- `components/<code point>.json.gz`: the characters that hold a component, for the component search,
  as `{"total": n, "groups": [[tier, size, code points, flags], ...]}` in tier, size, code point
  order. A group's code points are integers, each after the first given as the step from the one
  before; a flag is `n * 2 + direct`. Tier and size are a character's own, so they are written once
  for the group of characters that share them.
- `components/names.json.gz`: what a person may type for each component: `same`, the components typed
  as themselves, as one string, and `other`, `{typed: component}` for the rest.

The component objects must answer each of PROBES (日月 finds 明) before anything is sent, and an empty
`han_components` or `han_component_names` stops the load. `--only components` makes and sends those
objects alone: it reads neither D1 nor the local copies, keeps every other path of the manifest as R2
gives it, and reports to `out/reference-components-report.txt`. `--check URL` asks the site's
`/layers/suggest` for each probe after a load. The probe here is looser than the Worker's search,
which scans at most COMPONENT_SCAN characters of the rarest component, so `--check` is the authority.
Every `--send` takes `work/atlas-v3/out/reference.lock` in the main checkout (SEND_LOCK) from reading
the manifest to writing it back, and stops at once while another sender holds it.

A shard is the first hex digits of the SHA-256 of its key as UTF-8 text: the head's code point(s)
(`U+3042`) for a summary, the character itself (`好`) for the IDS.

D1 rows (`migrations-atlas/0004_reference.sql`) for what a query filters, joins or sorts on:
`component_variants`, `words`, `word_spellings` and the search's `names` (from `aliases`, each name
under its card's one code point); and the citations of the sources behind them, `variant_sources` and
`word_sources` in `site` (0005), whose other keys `books.py` writes. Cards name characters by `graphemes` id: a
card's character or family head the layout lacks is numbered as the publisher numbers one, and every grapheme takes its card's family head.
Graphemes are written as `publish.py` writes them, through `char_text`.

Only what differs is sent: the rows that differ from `out/atlas.sqlite`, as statements in parts
under `out/reference-load/reference-<generation>-<digest>/`, each confirmed by its stamp in `imports`
and replayed on the local copy, then the objects whose hash differs from the one `manifest.json.gz`
in R2 gives. The generation counts the loads before, so a load that returns to an earlier copy is
sent again. A load that stopped partway is finished first.

`manifest.json.gz` (`{path: hash}`, the first 16 hex digits of the SHA-256 of the gzipped bytes)
names every object under `v3/reference/` this load may have written, so the objects to delete are
decided from R2 itself. Before uploading, the load writes a manifest that adds the new paths with an
empty hash and keeps the old hash of a changed one; after uploading and deleting, the new manifest.
A load that stops between the two leaves every object it wrote listed, and its rerun sends them again.
Figures go to `out/reference-report.txt`. `--send` also applies migration 0004 (stamp
`migration:0004`) where the local copy lacks it.

`build.py --replace` numbers graphemes afresh: run this again after it.
"""

from __future__ import annotations

import argparse
import fcntl
import gzip
import hashlib
import json
import re
import shutil
import sqlite3
import subprocess
import sys
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

from build import OUT, ROOT, char_text
from load_history import built, deliver, row_statements, stamped
from publish import BUCKET, CF, R2_REMOTE, applied, lit, send, upload

from glyph_atlas import reference_input

SCHEMA = CF / "migrations-atlas" / "0004_reference.sql"
PREFIX = "v3/reference/"
MANIFEST = "manifest.json.gz"
LIST_PAGE = 500
# 4,096 summary shards keep one near 41 KB of JSON; the IDS, 1 MB in all, takes 256.
SUMMARY_DIGITS, IDS_DIGITS = 3, 2
# Rows per group of statements; a group is never split across parts.
GROUP = 500
SOURCES = reference_input.COLUMNS
# The site values reference.py owns; `books.py` writes the other keys of `site`.
SITE_KEYS = ("variant_sources", "word_sources")
# The tables reference.py owns, with their keys and the rows of them it owns, in the order their rows load.
TABLES = {
    "component_variants": (("a", "b", "count"), ("a", "b"), None),
    "words": (SOURCES["words"], ("id",), None),
    "word_spellings": (SOURCES["word_spellings"], ("word", "spelling", "source", "locator"), None),
    "names": (("text", "code_point", "rank"), ("text", "code_point"), None),
    "site": (("key", "value"), ("key",), f"key IN ({','.join(repr(k) for k in SITE_KEYS)})"),
}
SITE = "CREATE TABLE site(key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID;"
# A card's family head, as the cards were read from the site.
CARD_HEAD = "json_extract(data, '$.grapheme.code_point')"
SAMPLE_LISTS = ("representatives", "anchors")
# The Japanese readings a kana search finds kanji by (scripts/build_kanji_readings.py).
READINGS = ROOT / "data" / "vocab" / "kanji-readings.json"
# A kana search takes at most 49 kanji of a reading, 48 to show and one more for its count, skipping the
# ones the query names itself: those it takes are among the first 49.
READING_CARDS = 49
PLAIN_NAME = re.compile(r"^(.*IDEOGRAPH-)([0-9A-F]{4,6})$")
# Component searches the index must answer: the typed components, and one character they find.
PROBES = {"日月": "明", "匕矢": "彘"}
COMPONENTS = "components/"
# The lock every --send takes, full or --only components, from reading R2's manifest to writing it back:
# two senders would each drop the other's paths. It lies in the main checkout's work/atlas-v3/out
# whatever ATLAS3_DIR names, the worktree's checkout included; None finds it there.
SEND_LOCK = None


INPUT = None


def source(table):
    """The reference input, once its table is known to be written."""
    global INPUT
    if INPUT is None:
        INPUT = reference_input.reading()
    if INPUT.execute("SELECT 1 FROM written WHERE name=?", (table,)).fetchone() is None:
        sys.exit(f"no generator has written {table} into the reference input; {GENERATORS[table]} writes it")
    return INPUT


GENERATORS = {"characters": "export_graphemes.py --catalogue", "aliases": "export_graphemes.py --catalogue", "han_components": "export_components_cloudflare.py",
              "han_component_names": "export_components_cloudflare.py",
              **{t: "export_character_variants.py" for t in ("character_variants", "character_derived", "component_variants",
                                                            "grapheme_writers", "words", "word_spellings", "han_ids", "sources")}}


def shard(key, digits):
    return hashlib.sha256(key.encode()).hexdigest()[:digits]


def object_key(code_point):
    return code_point.replace(" ", "-")


def code_points(code_point):
    return tuple(int(p.removeprefix("U+"), 16) for p in code_point.split())


def code_point_of(text):
    """`U+6C34` for 水; code points joined by spaces for a text of several."""
    return " ".join(f"U+{ord(c):04X}" for c in text)


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def encode(text):
    return gzip.compress(text.encode(), mtime=0)


def substitution_key(left, right):
    """A substitution's own spelling, as refs._substitution writes it: the side of fewer code points
    first, then the smaller by code point."""
    if len(left) != len(right):
        return (left, right) if len(left) < len(right) else (right, left)
    return (left, right) if left <= right else (right, left)


class Graphemes:
    """Text to `graphemes.id`: the layout's, then the next ids in the order characters are first named,
    as `publish.py` numbers one."""

    def __init__(self, atlas):
        self.known = dict(atlas.execute("SELECT text, id FROM graphemes"))
        self.heads = dict(atlas.execute("SELECT id, head FROM graphemes"))
        self.next = (atlas.execute("SELECT max(id) FROM graphemes").fetchone()[0] or 0) + 1
        self.new = {}  # text -> id

    def __call__(self, text):
        text = char_text(text)
        if not text:
            return None
        if text in self.known:
            return self.known[text]
        if text not in self.new:
            self.new[text] = self.next
            self.next += 1
        return self.new[text]

    def get(self, text):
        text = char_text(text)
        return self.known.get(text, self.new.get(text))


def visual_groups(detail, codes, crop_of, notes):
    """The card's detail with each visual group keyed by its code and each sample naming its crop."""
    if '"visual_analysis"' not in detail or '"groups"' not in detail:
        return detail
    card = json.loads(detail)
    groups = (card.get("visual_analysis") or {}).get("groups") or []
    if not groups:
        return detail
    for group in groups:
        code = codes.get(group.get("id"))
        if code is None:
            notes["visual groups with no visual_group code, kept by their id"] += 1
        else:
            group["code"] = code
            del group["id"]
        for key in SAMPLE_LISTS:
            if key not in group:
                continue
            kept = []
            for entry in group[key] or []:
                crop = crop_of(entry.get("id"))
                if crop is None:
                    notes["visual group samples whose crop is not published, left out"] += 1
                    continue
                kept.append({"crop": crop, **{k: v for k, v in entry.items() if k not in ("id", "image")}})
            group[key] = kept
    return compact(card)


def sort_key(code_point):
    """Text that orders as the code point tuples do."""
    return " ".join(f"{p:06X}" for p in code_points(code_point))


class Objects:
    """Takes each object as it is made: its hash goes to the manifest, its figures to the report, and
    its bytes, when the hash differs from the one R2's manifest gives, to the stage folder."""

    def __init__(self, held, stage):
        self.held, self.stage, self.manifest = held, stage, {}
        self.kinds = defaultdict(lambda: [0, 0, 0, 0, 0])  # objects, bytes, largest, to send, bytes to send

    def put(self, rel, text):
        data = encode(text)
        h = self.manifest[rel] = digest(data)
        kind = self.kinds[rel.split("/")[0] if "/" in rel else rel]
        kind[0] += 1
        kind[1] += len(data)
        kind[2] = max(kind[2], len(data))
        if self.held.get(rel) != h:
            kind[3] += 1
            kind[4] += len(data)
            if self.stage:
                path = self.stage / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)

    def keep(self, made):
        """Every path of R2's manifest outside `made` (a path prefix), kept as R2 gives it."""
        self.manifest.update({rel: h for rel, h in self.held.items() if not rel.startswith(made)})

    def changed(self):
        return sum(k[3] for k in self.kinds.values())

    def gone(self):
        return sorted(set(self.held) - set(self.manifest))


def translate(atlas, mirror, path, objects):
    """The D1 tables in `path`, each object handed to `objects`; also the graphemes to insert, the heads
    to change and the report's notes. Cards, edges and derived forms wait in a scratch database on
    disk, so the run holds one object at a time."""
    path.unlink(missing_ok=True)
    out = sqlite3.connect(f"file:{path}", uri=True)
    out.executescript(SCHEMA.read_text() + SITE)
    scratch_path = path.with_name(path.stem + "-scratch.sqlite")
    scratch_path.unlink(missing_ok=True)
    scratch = sqlite3.connect(scratch_path)
    scratch.executescript("""
      CREATE TABLE cards(code_point TEXT PRIMARY KEY, sort TEXT NOT NULL, char TEXT NOT NULL, name TEXT,
        head TEXT, family TEXT NOT NULL, family_char TEXT NOT NULL, shard TEXT NOT NULL, several INTEGER NOT NULL,
        ligature INTEGER NOT NULL, occurrences INTEGER NOT NULL, data TEXT NOT NULL);
      CREATE INDEX card_sort ON cards(sort);
      CREATE INDEX card_char ON cards(char);
      CREATE INDEX card_family ON cards(family, sort);
      CREATE TABLE edges(seq INTEGER PRIMARY KEY, char TEXT NOT NULL, other TEXT NOT NULL, edge TEXT NOT NULL);
      CREATE INDEX edge_char ON edges(char, seq);
      CREATE TABLE derived(char TEXT PRIMARY KEY, forms TEXT NOT NULL) WITHOUT ROWID;
    """)
    notes = defaultdict(int, {"visual groups with no visual_group code, kept by their id": 0,
                              "visual group samples whose crop is not published, left out": 0})
    g = Graphemes(atlas)

    codes = dict(atlas.execute("SELECT key, code FROM codes WHERE field='visual_group'"))

    def crop_of(text_id):
        found = mirror.execute("SELECT id FROM ids WHERE text=?", (text_id,)).fetchone()
        if found is None or not atlas.execute("SELECT 1 FROM crops WHERE id=?", (found[0],)).fetchone():
            return None
        return found[0]

    def card_rows():
        for code_point, character, name, data_text, head in source("characters").execute(
                f"SELECT code_point, character, name, data, {CARD_HEAD} FROM characters"):
            data = json.loads(data_text)
            family = data.get("grapheme") or {}
            char = char_text(character)
            family_point = family.get("code_point") or code_point
            yield (code_point, sort_key(code_point), char, name, head, family_point, family.get("char") or char,
                   shard(family_point, SUMMARY_DIGITS), int(len(family.get("members") or []) > 1),
                   int(data.get("ligature") is not None), int(data.get("occurrence_count") or 0), data_text)

    scratch.executemany("INSERT INTO cards VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", card_rows())

    # Graphemes are numbered in code point order of the cards, as the publisher would.
    light, heads = {}, {}
    for code_point, char, head, family_char in scratch.execute(
            "SELECT code_point, char, head, family_char FROM cards ORDER BY sort"):
        gid = g(char)
        if head:
            heads[gid] = g(head)
        light[code_point] = (char, family_char)

    def edge_rows():
        seq = 0
        for a, b, relation, src, detail, written, widens in source("character_variants").execute(
                "SELECT a, b, relation, source, detail, written, widens FROM character_variants ORDER BY a, b, relation, source"):
            a, b = char_text(a), char_text(b)
            yield seq, a, b, compact([b, relation, src, detail, written, widens])
            yield seq + 1, b, a, compact([a, relation, src, detail, written, widens])
            seq += 2

    scratch.executemany("INSERT INTO edges VALUES(?,?,?,?)", edge_rows())
    scratch.executemany("INSERT OR REPLACE INTO derived VALUES(?,?)",
                        ((char_text(a), forms) for a, forms in source("character_derived").execute("SELECT a, forms FROM character_derived")))
    scratch.commit()
    pairs = {(a, b): [count, json.loads(p)] for a, b, count, p in
             source("component_variants").execute("SELECT a, b, count, pairs FROM component_variants")}
    writes, writers = defaultdict(list), defaultdict(list)
    for writer, grapheme, own in source("grapheme_writers").execute("SELECT writer, grapheme, own FROM grapheme_writers"):
        writes[char_text(writer)].append((char_text(grapheme), own))
    for writer in writes:
        writes[writer].sort()
        for grapheme, own in writes[writer]:
            writers[grapheme].append([writer, int(bool(own) or len(writes[writer]) > 1)])
    for grapheme in writers:
        writers[grapheme].sort()

    for code_point, data_text, detail in source("characters").execute("SELECT code_point, data, detail FROM characters"):
        char, head = light[code_point]
        found = scratch.execute("SELECT forms FROM derived WHERE char=?", (char,)).fetchone()
        forms_text = found[0] if found else "[]"
        forms = json.loads(forms_text)
        edges = scratch.execute("SELECT other, edge FROM edges WHERE char=? ORDER BY seq", (char,)).fetchall()
        subs = sorted({substitution_key(was, became) for _other, routes in forms for route in routes for was, became in route})
        named = {char, head, *(other for other, _ in edges), *(w for w, _ in writes.get(char, [])),
                 *(w for w, _ in writers.get(head, [])), *(other for other, _ in forms)}
        ids = {c: i for c in sorted(named) if (i := g.get(c)) is not None}
        objects.put(f"char/{object_key(code_point)}.json.gz",
                    f'{{"grapheme":{g.get(char)},"data":{data_text},"detail":{visual_groups(detail, codes, crop_of, notes)},'
                    f'"variants":[{",".join(e for _, e in edges)}],'
                    f'"variant_families":{families_text(scratch, [other for other, _ in edges])},"derived":{forms_text},'
                    f'"substitutions":{compact([[a, b, *pairs[(a, b)]] for a, b in subs if (a, b) in pairs])},'
                    f'"writes":{compact([[w, own] for w, own in writes.get(char, [])])},'
                    f'"writers":{compact(writers.get(head, []))},"ids":{compact(ids)}}}')

    # A summary shard at a time, its families by head, each family's cards in code point order.
    key, families = None, {}
    for shard_key, family, code_point, data_text in scratch.execute(
            "SELECT shard, family, code_point, data FROM cards ORDER BY shard, family, sort"):
        if shard_key != key and families:
            objects.put(f"summary/{key}.json.gz", summary_text(families))
            families = {}
        key = shard_key
        families.setdefault(family, []).append(f"{compact(code_point)}:{data_text}")
    if families:
        objects.put(f"summary/{key}.json.gz", summary_text(families))
    for rel, text in reading_objects(scratch):
        objects.put(rel, text)
    structures = defaultdict(dict)
    for char, sequences, shapes in source("han_ids").execute("SELECT char, sequences, shapes FROM han_ids"):
        structures[shard(char, IDS_DIGITS)][char] = [json.loads(sequences), json.loads(shapes) if shapes else None]
    for key, chars in structures.items():
        objects.put(f"ids/{key}.json.gz", compact(dict(sorted(chars.items()))))
    objects.put("names.json.gz", names_list(scratch.execute("SELECT code_point, name, occurrences FROM cards ORDER BY sort")))
    for list_name, column in (("graphemes", "several"), ("ligatures", "ligature")):
        total = scratch.execute(f"SELECT count(*) FROM cards WHERE {column}").fetchone()[0]
        rows = scratch.execute(f"SELECT data FROM cards WHERE {column} ORDER BY sort")
        for n in range(0, max(total, 1), LIST_PAGE):
            items = [r[0] for r in rows.fetchmany(LIST_PAGE)]
            objects.put(f"list/{list_name}/{n // LIST_PAGE}.json.gz", f'{{"total":{total},"offset":{n},"items":[{",".join(items)}]}}')
    scratch.close()
    scratch_path.unlink()

    put_components(objects)

    out.executemany("INSERT INTO component_variants VALUES(?,?,?)", [(a, b, v[0]) for (a, b), v in pairs.items()])
    # The names a search looks a term up by, each under its card's one code point as an integer
    # (migration 0001). A name for a card of several code points has no row: the search finds such a
    # card by its own text and code points, which are all the names it has.
    out.execute("CREATE TABLE names(text TEXT, code_point INTEGER, rank INTEGER, PRIMARY KEY(text, code_point)) WITHOUT ROWID")
    out.executemany("INSERT INTO names VALUES(?,?,?)", (
        (query, int(code_point.removeprefix("U+"), 16), rank) for query, code_point, rank in source("aliases").execute(
            "SELECT query, code_point, rank FROM aliases") if code_point.startswith("U+") and " " not in code_point))
    for table in ("words", "word_spellings"):
        cols = SOURCES[table]
        out.executemany(f'INSERT INTO "{table}" VALUES({",".join("?" * len(cols))})',
                        source(table).execute(f'SELECT {",".join(cols)} FROM "{table}"'))
    cited = dict(source("sources").execute("SELECT key, value FROM sources"))
    out.executemany("INSERT INTO site VALUES(?,?)", [(k, cited[k]) for k in SITE_KEYS if k in cited])
    for k in SITE_KEYS:
        if k not in cited:
            notes[f"site value {k} absent from the reference input"] += 1
    out.commit()

    changed = {gid: head for gid, head in heads.items() if gid in g.heads and g.heads[gid] != head}
    notes["graphemes of the layout whose family head the cards change"] = len(changed)
    notes["graphemes the layout lacks, numbered by this load"] = len(g.new)
    return out, g.new, {gid: heads.get(gid) for gid in g.new.values()}, changed, notes


def families_text(scratch, chars):
    """`{family head: {code point: data}}` of the families of the cards of `chars`, each family whole."""
    heads = []
    for char in chars:
        found = scratch.execute("SELECT family FROM cards WHERE char=?", (char,)).fetchone()
        if found and found[0] not in heads:
            heads.append(found[0])
    return summary_text({head: [f"{compact(code_point)}:{data}" for code_point, data in scratch.execute(
        "SELECT code_point, data FROM cards WHERE family=? ORDER BY sort", (head,))] for head in heads})


def reading_objects(scratch):
    """(path, JSON text) of the reading shards: each kana reading's kanji, most attested first, and the
    families of the first READING_CARDS of them, in the shard of the reading (SUMMARY_DIGITS)."""
    table = json.loads(READINGS.read_text())["readings"]
    shards = defaultdict(list)
    for reading in sorted(table):
        kanji = table[reading]
        shards[shard(reading, SUMMARY_DIGITS)].append(
            f'{compact(reading)}:{{"kanji":{compact(kanji)},"families":{families_text(scratch, kanji[:READING_CARDS])}}}')
    for key in sorted(shards):
        yield f"readings/{key}.json.gz", "{" + ",".join(shards[key]) + "}"


def summary_text(families):
    return "{" + ",".join(f'{compact(head)}:{{{",".join(entries)}}}' for head, entries in families.items()) + "}"


def names_list(cards):
    """The name list from (code point, name, occurrences) in code point order."""
    named, plain = [], {}
    for code_point, name, occurrences in cards:
        points = code_points(code_point)
        found = PLAIN_NAME.match(name or "")
        if len(points) == 1 and found and int(found[2], 16) == points[0] and len(found[2]) == len(f"{points[0]:04X}"):
            group = plain.setdefault(found[1], [found[1], [], [], 0])
            group[1].append(points[0] - group[3])
            group[2].append(occurrences)
            group[3] = points[0]
        else:
            named.append([code_point, name or "", occurrences])
    return compact({"names": named, "plain": [g[:3] for g in plain.values()]})


def component_list(rows):
    """One component's list from its (tier, size, code point, n, direct) rows."""
    groups = []
    for tier, size, point, n, direct in sorted(rows):
        if not groups or groups[-1][:2] != [tier, size]:
            groups.append([tier, size, [point], [n * 2 + direct]])
            last = point
            continue
        groups[-1][2].append(point - last)
        groups[-1][3].append(n * 2 + direct)
        last = point
    return compact({"total": len(rows), "groups": groups})


def component_lists():
    """(path, JSON text) of each component's list and of the names a person may type."""
    for table in ("han_components", "han_component_names"):
        if source(table).execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone() is None:
            sys.exit(f"{table} in the reference input is empty; {GENERATORS[table]} writes it")
    rows = source("han_components").execute(
        "SELECT component, tier, size, code_point, n, direct FROM han_components ORDER BY component")
    component, held = None, []
    for c, tier, size, point, n, direct in rows:
        if c != component and held:
            yield f"components/{object_key(code_point_of(component))}.json.gz", component_list(held)
            held = []
        component = c
        held.append((tier, size, code_points(point)[0], n, int(bool(direct))))
    if held:
        yield f"components/{object_key(code_point_of(component))}.json.gz", component_list(held)
    same, other = [], {}
    for query, c in source("han_component_names").execute("SELECT query, component FROM han_component_names ORDER BY query"):
        if query == c:
            same.append(query)
        else:
            other[query] = c
    yield "components/names.json.gz", compact({"same": "".join(same), "other": other})


def component_search(read, term):
    """The characters holding every component `term` names, as often as it names it, from the objects
    `read` gives by path (None when absent); None when the names object is absent."""
    names = read(f"{COMPONENTS}names.json.gz")
    if names is None:
        return None
    wanted = defaultdict(int)
    for typed in term:
        component = names["other"].get(typed) or (typed if typed in names["same"] else None)
        if component is None:
            return set()
        wanted[component] += 1
    found = None
    for component, n in wanted.items():
        listed = read(f"{COMPONENTS}{object_key(code_point_of(component))}.json.gz")
        if listed is None:
            return set()
        held = set()
        for _tier, _size, steps, flags in listed["groups"]:
            point = 0
            for k, (step, flag) in enumerate(zip(steps, flags, strict=True)):
                point = point + step if k else step
                if flag >> 1 >= n:
                    held.add(chr(point))
        found = held if found is None else found & held
    return found or set()


def probe_components(read):
    """Each probe whose character the objects do not find."""
    problems = []
    for term, char in PROBES.items():
        found = component_search(read, term)
        if found is None:
            problems.append(f"{term}: no {COMPONENTS}names.json.gz")
        elif char not in found:
            problems.append(f"{term}: {char} not among the {len(found)} characters found")
    return problems


def put_components(objects):
    """Hand the component objects to `objects`, once they answer every probe."""
    made = dict(component_lists())
    problems = probe_components(lambda rel: json.loads(made[rel]) if rel in made else None)
    if problems:
        sys.exit("the component index fails its probes; nothing sent:\n" + "\n".join(problems))
    for rel, text in made.items():
        objects.put(rel, text)


def check_site(base):
    """Ask the site's /layers/suggest for each probe; the probes it does not answer."""
    problems = []
    for term, char in PROBES.items():
        url = f"{base.rstrip('/')}/layers/suggest?q={urllib.parse.quote(term)}&limit=48"
        request = urllib.request.Request(url, headers={"user-agent": "glyph-atlas-check"})
        with urllib.request.urlopen(request, timeout=60) as answer:
            chars = [item.get("char") for item in json.load(answer).get("items", [])]
        if char not in chars:
            problems.append(f"{term}: {char} not among {''.join(c or '?' for c in chars) or 'no items'}")
    return problems


def has(db, table):
    return db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone() is not None


def remove_row(table, key, values):
    return f'DELETE FROM "{table}" WHERE {" AND ".join(f'"{c}" = {lit(v)}' for c, v in zip(key, values, strict=True))};'


def statements(ref, atlas, new, new_heads, changed):
    """Groups of statements that turn the local copy into the translation, the rows each table writes
    (statements plus their index entries), and the digest of the statements. The difference is taken
    in SQL with the local copy attached: its rows the translation lacks or changed, and the
    translation's rows it lacks or holds otherwise; a row's statements stay in one group."""
    groups, written, hashed = [], {}, hashlib.sha256()

    def add(rows):
        """Rows' statements in groups of at most GROUP rows; how many statements."""
        group, n = [], 0
        for i, r in enumerate(rows, 1):
            for statement in r:
                hashed.update(statement.encode() + b"\n")
            group += r
            n += len(r)
            if i % GROUP == 0:
                groups.append(group)
                group = []
        if group:
            groups.append(group)
        return n

    graphemes = [[f"INSERT INTO graphemes(id, text, head) VALUES({gid}, {lit(text)}, {lit(new_heads[gid])});"]
                 for text, gid in new.items()] + [[f"UPDATE graphemes SET head = {head} WHERE id = {gid};"]
                                                  for gid, head in sorted(changed.items())]
    written["graphemes"] = add(graphemes) * 2
    attached = has(atlas, "words") or has(atlas, "site")
    if attached:
        ref.execute("ATTACH ? AS held", (f"file:{OUT / 'atlas.sqlite'}?mode=ro",))
    for table, (cols, key, owned) in TABLES.items():
        names = ",".join(f'"{c}"' for c in cols)
        order = ",".join(f'"{c}"' for c in key)
        on = " AND ".join(f'h."{c}" = w."{c}"' for c in key)
        if has(atlas, table):
            mine = f'(SELECT * FROM held."{table}" WHERE {owned})' if owned else f'held."{table}"'
            gone = ref.execute(f'SELECT {order} FROM (SELECT {names} FROM {mine} EXCEPT SELECT {names} FROM main."{table}") h '
                               f'WHERE NOT EXISTS (SELECT 1 FROM main."{table}" w WHERE {on}) ORDER BY {order}')
            come = ref.execute(f'SELECT {names}, EXISTS (SELECT 1 FROM {mine} h WHERE {on}) FROM '
                               f'(SELECT {names} FROM main."{table}" EXCEPT SELECT {names} FROM {mine}) w ORDER BY {order}')
        else:
            gone, come = [], ref.execute(f'SELECT {names}, 0 FROM "{table}" ORDER BY {order}')
        rows = ([remove_row(table, key, r)] for r in gone)
        changes = (([remove_row(table, key, [r[cols.index(c)] for c in key])] if r[-1] else [])
                   + row_statements(table, list(cols), list(key), r[:-1]) for r in come)
        indexes = ref.execute("SELECT count(*) FROM pragma_index_list(?, 'main') WHERE origin <> 'pk'", (table,)).fetchone()[0]
        written[table] = (add(rows) + add(changes)) * (1 + indexes)
    if attached:
        ref.execute("DETACH held")
    return groups, written, hashed.hexdigest()[:12]


def loads(atlas):
    """The names of the reference loads the local copy holds a part of."""
    return {s.split(".")[0] for (s,) in atlas.execute("SELECT stamp FROM imports WHERE stamp LIKE 'reference-%'")}


def finish(atlas):
    """Deliver every load D1 or the local copy holds only part of. A load whose first part D1 applied
    is finished even when the local copy lacks every part, as when a run stopped between applying a
    part and replaying it; a folder D1 never started is a load not sent, and is removed."""
    started = loads(atlas)
    for run in sorted((OUT / "reference-load").glob("reference-*")):
        if not run.is_dir() or run.name.endswith(".building"):
            continue
        parts = sorted(run.glob("*.sql"))
        if all(atlas.execute("SELECT 1 FROM imports WHERE stamp=?", (f"{run.name}.{p.stem}",)).fetchone() for p in parts):
            continue
        if run.name in started or applied("glyph-atlas-v3", f"{run.name}.{parts[0].stem}"):
            print(f"finishing {run.name}", flush=True)
            deliver(run, atlas)
        else:
            shutil.rmtree(run)


def remove(paths):
    """Objects to delete under PREFIX; a resend is harmless."""
    names = "\n".join(paths) + "\n"
    for _ in range(4):
        done = subprocess.run(["rclone", "delete", f"{R2_REMOTE}:{BUCKET}/{PREFIX}", "--files-from-raw", "-"],
                              input=names, text=True, check=False)
        if done.returncode == 0:
            return
    sys.exit(f"deleting from {PREFIX} failed; rerun to finish")


def digest(data):
    return hashlib.sha256(data).hexdigest()[:16]


def fetch_manifest():
    """The manifest in R2, {path: hash}; empty before the first load."""
    listed = subprocess.run(["rclone", "lsf", f"{R2_REMOTE}:{BUCKET}/{PREFIX}", "--files-only", "--max-depth", "1"],
                            capture_output=True, text=True, check=False)
    if listed.returncode not in (0, 3):  # 3: the folder does not exist yet
        sys.exit(f"listing {PREFIX} failed: {listed.stderr.strip()}")
    if MANIFEST not in listed.stdout.split():
        return {}
    read = subprocess.run(["rclone", "cat", f"{R2_REMOTE}:{BUCKET}/{PREFIX}{MANIFEST}"], capture_output=True, check=False)
    if read.returncode:
        sys.exit(f"reading {PREFIX}{MANIFEST} failed: {read.stderr.decode().strip()}")
    data = read.stdout
    return json.loads(gzip.decompress(data) if data[:2] == b"\x1f\x8b" else data)


def put_manifest(manifest):
    stage = OUT / "reference-manifest"
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True)
    (stage / MANIFEST).write_bytes(encode(compact(dict(sorted(manifest.items())))))
    upload([stage / MANIFEST], PREFIX)
    shutil.rmtree(stage)


def ship(objects, stage, gone):
    """List what this load may write in the manifest, upload the staged objects folder by folder,
    delete the gone ones, then write the new manifest."""
    put_manifest({**objects.held, **{rel: "" for rel in objects.manifest if rel not in objects.held}})
    for folder in sorted({p.parent for p in stage.rglob("*.json.gz")}):
        prefix = str(folder.relative_to(stage))
        upload(sorted(folder.glob("*.json.gz")), PREFIX + ("" if prefix == "." else prefix + "/"))
    if gone:
        remove(gone)
    put_manifest(objects.manifest)
    shutil.rmtree(stage, ignore_errors=True)


def report(ref, written, objects, gone, notes, parts):
    """D1 rows and bytes on disk (tables with their indexes) and rows written; R2 objects and bytes by kind."""
    sizes = dict(ref.execute("SELECT coalesce(m.tbl_name, d.name), sum(d.pgsize) FROM dbstat d "
                             "LEFT JOIN sqlite_master m ON m.name = d.name GROUP BY 1"))
    lines = [f"{'D1 table':<22}{'rows':>11}{'bytes':>14}{'rows written':>14}"]
    total = [0, 0, 0]
    for table in ("graphemes", *TABLES):
        # Graphemes: the rows this load inserts or changes.
        n = ref.execute(f"SELECT count(*) FROM {table}").fetchone()[0] if table != "graphemes" else written.get(table, 0) // 2
        size = sizes.get(table, 0)
        lines.append(f"{table:<22}{n:>11,}{size:>14,}{written.get(table, 0):>14,}")
        for i, v in enumerate((n, size, written.get(table, 0))):
            total[i] += v
    lines.append(f"{'total':<22}{total[0]:>11,}{total[1]:>14,}{total[2]:>14,}")
    lines.append(f"{parts} parts; each also writes its stamp in imports (2 rows)")
    lines.append("")
    return "\n".join(lines) + "\n" + object_report(objects, gone, notes)


def object_report(objects, gone, notes):
    """R2 objects and bytes by kind, those to send, and the notes."""
    lines = [f"{'R2 objects':<22}{'objects':>11}{'bytes':>14}{'largest':>12}{'to send':>10}{'bytes':>14}"]
    for kind, k in sorted(objects.kinds.items()):
        lines.append(f"{kind:<22}{k[0]:>11,}{k[1]:>14,}{k[2]:>12,}{k[3]:>10,}{k[4]:>14,}")
    sums = [sum(k[i] for k in objects.kinds.values()) for i in range(5)]
    lines.append(f"{'total':<22}{sums[0]:>11,}{sums[1]:>14,}{'':>12}{sums[3]:>10,}{sums[4]:>14,}")
    lines.append(f"objects to delete: {len(gone):,}")
    lines += [f"{k}: {v:,}" for k, v in sorted(notes.items())]
    return "\n".join(lines) + "\n"


def migrate(atlas):
    """Migration 0004 on D1 and the local copy, once; a layout built with it already has its tables."""
    if has(atlas, "words"):
        return
    path = OUT / "reference-load" / "0004.sql"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(stamped(SCHEMA.read_text(), "migration:0004"))
    if not applied("glyph-atlas-v3", "migration:0004") and not send("glyph-atlas-v3", path, "migration:0004"):
        sys.exit("migration 0004 did not apply; rerun")
    atlas.executescript("BEGIN;\n" + path.read_text() + "COMMIT;\n")
    print("migration 0004 applied", flush=True)


def send_lock_path():
    """work/atlas-v3/out/reference.lock in the main checkout (the one git keeps its common directory in)."""
    if SEND_LOCK is not None:
        return SEND_LOCK
    common = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                            capture_output=True, text=True, check=False)
    root = Path(common.stdout.strip()).parent if common.returncode == 0 and common.stdout.strip() else ROOT
    return root / "work" / "atlas-v3" / "out" / "reference.lock"


def take_send_lock():
    """The send lock, held until the process exits; another sender holding it stops this one."""
    path = send_lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    held = open(path, "w")  # noqa: SIM115 -- held until the process exits
    try:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.exit(f"another reference.py --send holds {path}; rerun once it has finished")
    return held


def components_only(send):
    """Make the component objects and, with `send`, upload the changed ones; no D1 read or write."""
    OUT.mkdir(parents=True, exist_ok=True)
    lock = open(OUT / "mirror.lock", "w")  # noqa: SIM115 -- held until the process exits
    fcntl.flock(lock, fcntl.LOCK_EX)
    sending = take_send_lock() if send else None  # noqa: F841 -- held until the process exits
    held = fetch_manifest()
    if send and not held:
        sys.exit(f"R2 has no {PREFIX}{MANIFEST}; load the whole reference first")
    stage = OUT / "reference-components-send"
    shutil.rmtree(stage, ignore_errors=True)
    objects = Objects(held, stage if send else None)
    put_components(objects)
    objects.keep(COMPONENTS)
    gone = objects.gone()
    text = object_report(objects, gone, {"probes answered": len(PROBES)})
    (OUT / "reference-components-report.txt").write_text(text)
    print(text, flush=True)
    if not send:
        print("dry run: nothing sent", flush=True)
        return
    if objects.changed() or gone:
        ship(objects, stage, gone)
    print("component index loaded", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--send", action="store_true", help="load the rows into glyph-atlas-v3 and upload the objects")
    parser.add_argument("--only", choices=("components",), help="make (and with --send, upload) these objects alone")
    parser.add_argument("--check", metavar="URL", help="ask the site at URL for each probe; exit 1 on a miss")
    args = parser.parse_args()
    if args.check:
        problems = check_site(args.check)
        print("\n".join(problems) or f"{len(PROBES)} probes answered", flush=True)
        sys.exit(1 if problems else 0)
    if args.only == "components":
        components_only(args.send)
        return
    lock = open(OUT / "mirror.lock", "w")  # noqa: SIM115 -- held until the process exits
    fcntl.flock(lock, fcntl.LOCK_EX)
    mirror = sqlite3.connect(f"file:{OUT / 'mirror.sqlite'}?mode=ro", uri=True)
    if has(mirror, "pending") and mirror.execute("SELECT 1 FROM pending").fetchone():
        sys.exit("a publish is unfinished; finish it with publish.py first")
    atlas = sqlite3.connect(OUT / "atlas.sqlite") if args.send else \
        sqlite3.connect(f"file:{OUT / 'atlas.sqlite'}?mode=ro", uri=True)
    if args.send:
        sending = take_send_lock()  # noqa: F841 -- held until the process exits
        build = mirror.execute("SELECT value FROM meta WHERE key='build'").fetchone()[0]
        if not applied("glyph-atlas-v3", f"build:{build}"):
            sys.exit(f"glyph-atlas-v3 was not loaded from build {build}; the local copy does not describe it")
        if not has(atlas, "site"):
            sys.exit("the local copy lacks site (migration 0005); books.py --send applies it")
        migrate(atlas)
        finish(atlas)

    stage = OUT / "reference-send"
    shutil.rmtree(stage, ignore_errors=True)
    objects = Objects(fetch_manifest(), stage if args.send else None)
    ref, new, new_heads, changed_heads, notes = translate(atlas, mirror, OUT / "reference.sqlite", objects)
    groups, written, rows_digest = statements(ref, atlas, new, new_heads, changed_heads)
    gone = objects.gone()
    run = None
    if groups:
        run = OUT / "reference-load" / f"reference-{len(loads(atlas)) + 1}-{rows_digest}"
        built(run, "rows", groups)
    parts = len(list(run.glob("*.sql"))) if run else 0
    text = report(ref, written, objects, gone, notes, parts)
    (OUT / "reference-report.txt").write_text(text)
    print(text + (f"{parts} parts -> {run}" if run else "the local copy already holds these rows"), flush=True)
    if not args.send:
        print("dry run: nothing sent", flush=True)
        return
    if run:
        deliver(run, atlas)
    if objects.changed() or gone:
        ship(objects, stage, gone)
    print("reference data loaded", flush=True)


if __name__ == "__main__":
    main()
