"""The ledger's resolver (data/ledger.json) as the Worker runs it on glyph-atlas-v3 (`apps/cloudflare/src/ledger.ts`).

Crop evidence is live while it holds on the crop as it stands (`holds`): on the box it has, the redrawn one while it waits. The
crop's pixels and box are its page record's, which its caller gives in `shown`; a slot names its crop and the crop's effective
cut. A date slot's subject is a book, so both are NULL there.
"""

import json
import sys
from pathlib import Path

CATALOGUE = json.loads((Path(__file__).resolve().parents[2] / "data" / "ledger.json").read_text(encoding="utf-8"))
KEYS_PER_STATEMENT = 50
# The columns the resolver selects, in its order.
RESOLVED = "subject,predicate,scope,slot,status,object,value,members,supporting,claims,crop,cut,resolver,at"
SLOT_CROP = ("(SELECT a.crop FROM assertions a WHERE a.subject=k.subject AND a.predicate=k.predicate AND a.scope=k.scope "
             "AND a.slot=k.slot AND a.crop IS NOT NULL LIMIT 1)")
KEYS = ("SELECT DISTINCT json_extract(value,'$[0]'),json_extract(value,'$[1]'),json_extract(value,'$[2]'),"
        "json_extract(value,'$[3]') FROM json_each(?1)")
SHOWN = ("shown(crop,cut,pixels,box) AS (SELECT json_extract(value,'$[0]'),json_extract(value,'$[1]'),json_extract(value,'$[2]'),"
         "json_extract(value,'$[3]') FROM json_each(?2))")


def lines(name):
    """The SQL of one entry of the catalogue, without its comments."""
    return "\n".join(line for line in CATALOGUE[name] if not line.lstrip().startswith("--"))


def canonical(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def lit(text):
    return "'" + text.replace("'", "''") + "'"


def effective_cut(crop):
    return (f"(SELECT c.cut+EXISTS(SELECT 1 FROM reviews rv WHERE rv.crop=c.id AND rv.x IS NOT NULL) "
            f"FROM crops c WHERE c.id={crop})")


def v3_resolve(keys=KEYS):
    body = lines("resolve")
    for old, new in ((KEYS, keys), ("e.ref IS NOT (SELECT c.version FROM crop_now c WHERE c.unit=a.subject)", f"NOT {lines('holds')}"),
                     ("(SELECT c.version FROM crop_now c WHERE c.unit=k.subject) AS crop_version,",
                      f"{SLOT_CROP} AS crop, {effective_cut(SLOT_CROP)} AS cut,")):
        if body.count(old) != 1:
            sys.exit(f"data/ledger.json's resolver changed: {old}")
        body = body.replace(old, new)
    if "crop_now" in body:
        sys.exit("data/ledger.json's resolver names crop_now where it is not replaced")
    return f"{SHOWN},\n{body}"


def statement(sql, keys="[]", shown="[]"):
    return " ".join(line.strip() for line in sql.splitlines()).replace("?1", lit(keys)).replace("?2", lit(shown)) + ";"


def resolve_statements(slots):
    """The statements that resolve `slots` again in the database they run on, from its own rows: each
    slot's `current_claims` row removed, then written where a claim of it is live (`resolveSlots`)."""
    clear = ("DELETE FROM current_claims WHERE (subject,predicate,scope,slot) IN (SELECT json_extract(value,'$[0]'),"
             "json_extract(value,'$[1]'),json_extract(value,'$[2]'),json_extract(value,'$[3]') FROM json_each(?1))")
    write = f"INSERT INTO current_claims({RESOLVED}) WITH {v3_resolve()}"
    out = []
    for i in range(0, len(slots), KEYS_PER_STATEMENT):
        bound = canonical([list(k) for k in slots[i:i + KEYS_PER_STATEMENT]])
        out += [statement(sql, bound) for sql in (clear, write)]
    return out


def shown_as(crop, cut, record):
    """A crop's row of `shown`: its cut, and its record's pixels and box as a version ends (`cropNow`)."""
    pixels, box = record.get("image_sha256") or record.get("source_revision"), record.get("box")
    values = [] if box is None else [box.get(k) for k in "xywh"]
    whole = all(isinstance(v, int) for v in values)
    return [crop, cut, f"@{pixels}" if pixels else None, "@" + ",".join(map(str, values)) if pixels and whole else None]


def settle_statements(shown, chunk=200):
    """The statements that resolve every claim on these crops again, which a publication has cut again (`shown_as`). A
    grapheme claim that holds is dated now, so `reconcile` files its crop's runs."""
    keys = "SELECT DISTINCT a.subject,a.predicate,a.scope,a.slot FROM json_each(?2) j JOIN assertions a ON a.crop=json_extract(j.value,'$[0]')"
    crops = "crop IN (SELECT json_extract(value,'$[0]') FROM json_each(?2))"
    out = []
    for i in range(0, len(shown), chunk):
        bound = canonical(shown[i:i + chunk])
        out += [statement(sql, shown=bound) for sql in (
            f"DELETE FROM current_claims WHERE {crops}", f"INSERT INTO current_claims({RESOLVED}) WITH {v3_resolve(keys)}",
            f"UPDATE current_claims SET at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE {crops} AND predicate='has_grapheme'")]
    return out
