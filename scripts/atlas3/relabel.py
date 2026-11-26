"""Write the catalogue that files published crops under another character, from their published records.

    python3 scripts/atlas3/relabel.py <relabels.json> <catalogue.sqlite>
    python3 scripts/atlas3/publish.py <catalogue.sqlite> --dry-run
    python3 scripts/atlas3/publish.py <catalogue.sqlite>

`relabels.json` is a list of `{"id", "label", "other"}`: a crop's text id, the character it is filed
under and the one it goes to. Each crop is written as the `units` row its entry and its row in
out/atlas.sqlite make, under the new character and one revision on. The runs holding it are written under its new character and family (`unit_ngrams`),
with the crops that start them listed as they stand. Nothing is cut again, so a page need not be
exported for the few of its crops a relabel touches.

A crop is left out, and listed, when the site holds it under another character than `label`, no longer
pending or in a style a reviewer gave it, when the site does not hold the new character, or when the new
character is neither Han nor kana. Its script, category and 字母 are the new character's. The catalogue names the revision of each
crop and page it was made from (`based_on`), and the publisher refuses it once one of them has moved on.
The relabel goes to the crop's store as well, or its next export leaves the crop behind the site's
revision. Every file but the catalogue is only read here.
"""
import argparse
import gzip
import json
import sqlite3
import sys
from pathlib import Path

from build import OUT, PAGE
from publish import SUSPECT, graphemes_of, logical_sql

from glyph_atlas import refs
from glyph_atlas.review.atlas import script_of_identity
from glyph_atlas.schema import Script

COLUMNS = ("id", "origin", "character", "family", "visual_group", "production", "category", "state", "revision",
           "quiz", "document", "style", "data")
BASED = ("id", "revision", "page_rev")
RUNS = ("size", "graphemes", "text", "hand_order", "first", "second", "third", "document", "vertical")
# The category of a crop filed under a character of each script, as an export gives it.
CATEGORIES = {Script.HAN: "kanji", Script.HIRAGANA: "kana", Script.KATAKANA: "kana", Script.HENTAIGANA: "kana"}


def script_of(character):
    """The script of a character, that of its base whatever marks it carries (ツ゚)."""
    return Script(script_of_identity(character))


def entry_of(atlas, cid):
    """A crop's entry in its page's published bundle."""
    (rev,) = atlas.execute("SELECT rev FROM pages WHERE id=?", (cid // PAGE,)).fetchone()
    body = json.loads(gzip.decompress((OUT / "pages" / f"{cid // PAGE}-{rev}.json.gz").read_bytes()))
    return next(e for e in body["crops"] if e["slot"] == cid % PAGE)


def crop_of(atlas, cid):
    """A crop's row in the local copy with the names its codes stand for, or None."""
    return atlas.execute(
        f"""SELECT c.*, {logical_sql(atlas)} AS logical, p.rev AS page_rev, g.text AS character, h.text AS family_text, b.key AS document, p.key AS page_key,
        (SELECT key FROM productions WHERE id=c.production) AS production_key,
        (SELECT key FROM codes WHERE field='visual_group' AND code=c.visual_group) AS visual_key,
        (SELECT key FROM codes WHERE field='category' AND code=c.category) AS category_key,
        (SELECT key FROM codes WHERE field='state' AND code=c.state) AS state_key,
        (SELECT key FROM codes WHERE field='style' AND code=c.style) AS style_key
        FROM crops c LEFT JOIN graphemes g ON g.id=c.char LEFT JOIN graphemes h ON h.id=c.family
        JOIN books b ON b.id=c.book JOIN pages p ON p.id=c.id/{PAGE} WHERE c.id=?""", (cid,)).fetchone()


def unit(atlas, text, crop, to=None):
    """A crop's `units` row as published, or with `to` (character, family) filed under that."""
    entry = {k: v for k, v in entry_of(atlas, crop["id"]).items() if k not in ("slot", "image_place", "context_image_place")}
    # A crop on no page stands on its book's: the publisher makes that page's key itself.
    page = {} if crop["page_key"] == f"book:{crop['document']}" else {"page_id": crop["page_key"]}
    # A suspect mark is of the label it was judged under.
    data = {**entry, **page, "suspect": not to and bool(crop["flags"] & SUSPECT)}
    category = crop["category_key"]
    if to:
        script, jibo = script_of(to[0]), refs.jibo_of_unit(" ".join(refs.to_code_points(to[0])))
        data = {**{k: v for k, v in data.items() if k != "jibo"}, "label": to[0], "script": str(script), **({"jibo": jibo} if jibo else {})}
        category = CATEGORIES[script]
    character, family = to or (crop["character"], crop["family_text"])
    return (text, "local", character, family, None if to else crop["visual_key"], crop["production_key"], category,
            crop["state_key"] or "pending", crop["logical"] + bool(to), crop["quiz"], crop["document"], crop["style_key"],
            json.dumps(data, ensure_ascii=False, separators=(",", ":")))


def styled(atlas, cid):
    """Whether a reviewer set a crop's style, which its row then holds in place of the published one."""
    return (atlas.execute("SELECT 1 FROM sqlite_master WHERE name='reviewer_styles'").fetchone() is not None
            and atlas.execute("SELECT 1 FROM reviewer_styles WHERE crop=?", (cid,)).fetchone() is not None)


def units(relabels, mirror, atlas):
    """The relabelled `units` rows, each crop's new character and family by its number, and the crops left out."""
    rows, moved, left = [], {}, {}
    for item in relabels:
        text, other = item["id"], item["other"]
        found = mirror.execute("SELECT id FROM ids WHERE text=?", (text,)).fetchone()
        crop = found and crop_of(atlas, found[0])
        to = atlas.execute("SELECT g.text, h.text FROM graphemes g JOIN graphemes h ON h.id=coalesce(g.head, g.id) WHERE g.text=?",
                           (other,)).fetchone()
        if not crop or crop["kind"] != 0:
            left[text] = "not a collected crop on the site"
        elif crop["character"] != item["label"]:
            left[text] = f"filed under {crop['character']}"
        elif item["label"] == other:
            left[text] = f"is filed under {other}"
        elif (crop["state_key"] or "pending") != "pending":
            left[text] = f"is {crop['state_key']}"
        elif to is None:
            left[text] = f"the site holds no {other}"
        elif script_of(other) not in CATEGORIES:
            left[text] = f"{other} is neither Han nor kana"
        elif styled(atlas, crop["id"]):
            left[text] = "has a reviewer's style"
        else:
            rows.append(unit(atlas, text, crop, tuple(to)))
            moved[crop["id"]] = tuple(to)
    return rows, moved, left


def ngrams(moved, mirror, atlas, runs):
    """Every run of each crop that starts a run holding a moved one, filed and written as moved, and those
    crops that are not themselves moved: the publisher rewrites the runs of the crops a catalogue holds."""
    cids = list(moved)
    firsts = sorted({r[0] for i in range(0, len(cids), 200) for marks in [",".join(map(str, cids[i:i + 200]))] for r in runs.execute(
        f"SELECT first FROM runs WHERE first IN ({marks}) OR second IN ({marks}) OR third IN ({marks})")})
    text_of = {}

    def name(cid):
        if cid is not None and cid not in text_of:
            (text_of[cid],) = mirror.execute("SELECT text FROM ids WHERE id=?", (cid,)).fetchone()
        return text_of.get(cid)
    out = []
    for first in firsts:
        for r in runs.execute("SELECT r.*, t.text FROM runs r LEFT JOIN run_text t ON t.first=r.first AND t.size=r.size WHERE r.first=?", (first,)):
            members = [m for m in (r["first"], r["second"], r["third"]) if m is not None]
            heads, written = graphemes_of(r["graphemes"]), graphemes_of(r["text"]) if r["text"] else None
            if any(len(parts) != len(members) for parts in (heads, written) if parts):
                sys.exit(f"the run of {name(first)} reads {r['graphemes']} as {r['text']}: not one grapheme a crop")
            for i, member in enumerate(members):
                if member in moved:
                    if written:
                        written[i] = moved[member][0]
                    if heads:
                        heads[i] = moved[member][1]
            (book,) = atlas.execute("SELECT key FROM books WHERE id=?", (r["book"],)).fetchone()
            out.append((r["size"], "".join(heads), "".join(written) if written else None, r["hand"], name(first), name(r["second"]),
                        name(r["third"]), book, 1))
    return out, [(name(first), first) for first in firsts if first not in moved]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("relabels", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.output.exists():
        sys.exit(f"{args.output} exists; a relabel is written into a new file")
    mirror, atlas, runs = (sqlite3.connect(f"file:{OUT / name}?mode=ro", uri=True)
                           for name in ("mirror.sqlite", "atlas.sqlite", "atlas-runs.sqlite"))
    atlas.row_factory = runs.row_factory = sqlite3.Row
    rows, moved, left = units(json.loads(args.relabels.read_text(encoding="utf-8")), mirror, atlas)
    listed, starts = ngrams(moved, mirror, atlas, runs)
    # A corpus glyph's record is its corpus's, so its runs wait for that corpus.
    kept = [unit(atlas, text, crop) for text, first in starts if (crop := crop_of(atlas, first)) and crop["kind"] == 0]
    if late := [text for text, first in starts if styled(atlas, first)]:
        sys.exit(f"{late[0]} starts a run of a listed crop and has a reviewer's style; publish that run with an export")
    based = []
    for text in [r[0] for r in rows + kept]:
        crop = crop_of(atlas, mirror.execute("SELECT id FROM ids WHERE text=?", (text,)).fetchone()[0])
        based.append((text, crop["revision"], crop["page_rev"]))
    db = sqlite3.connect(args.output)
    db.execute(f"CREATE TABLE units({','.join(COLUMNS)})")
    db.execute(f"CREATE TABLE based_on({','.join(BASED)})")
    db.executemany(f"INSERT INTO based_on VALUES({','.join('?' * len(BASED))})", based)
    db.execute(f"CREATE TABLE unit_ngrams({','.join(RUNS)})")
    db.executemany(f"INSERT INTO units VALUES({','.join('?' * len(COLUMNS))})", rows + kept)
    db.executemany(f"INSERT INTO unit_ngrams VALUES({','.join('?' * len(RUNS))})", listed)
    db.commit()
    print(f"{len(rows)} crops relabelled, {len(kept)} listed as they are for their runs, {len(listed)} runs -> {args.output}", flush=True)
    for text, why in sorted(left.items()):
        print(f"left out: {text}: {why}", flush=True)


if __name__ == "__main__":
    main()
