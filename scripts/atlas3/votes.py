"""A crop's model votes as the page objects `v3/votes/<page>.json.gz` hold them, from the export's
`units.visual` and `units.context`.

`visual` is what the crop models read in the crop's image (`read_crops` in export_cloudflare.py). It is
kept without what every row repeated: its engines are the `codes` entry `models` names (field 'models',
key their JSON), each vote and candidate names its engine by place in that list, and `status`,
`input_image` (the crop's own image) and `verified` (always false) are left out. `context` is the
nearby transcription's guesses (`context_guesses`), each candidate as [text, offset, before, after],
since every one names the same engine. A reading that is not ready is not stored.

A page object maps each slot with a reading to `{cut, models, visual, context}`, `visual` and `context`
present only when ready, and `loaded` on an entry the cutover load (crop_facts.py) wrote. The reading
was made on cut `cut`; a reader takes it only while the crop's `crops.cut` is that cut. Objects sit
under the layout's build id (`v3/votes/<build>/`, the `codes` entry field 'build'), so a rebuilt layout,
which numbers crops afresh, never reads another build's. Objects are replaced whole; the local copy of
what R2 holds is `out/votes/`.

`restore` gives back what the suggestions routes answered, less `input_image`.
"""

import gzip
import json
import os

CONTEXT_ENGINE = "Nearby transcription"
CONTEXT_KEYS = {"text", "engine", "offset", "before", "after"}


def prefix(build):
    return f"v3/votes/{build}/"


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def visual(text):
    """(engines key, stored reading), or (None, None) when the models gave no reading."""
    v = json.loads(text) if text else {}
    if v.get("status") != "ready":
        return None, None
    names = [e["name"] for e in v["engines"]]

    def placed(items):
        return [{**c, "engine": names.index(c["engine"])} if c.get("engine") in names else c for c in items]

    if v.get("verified") not in (None, False):
        raise ValueError(f"a reading marked verified: {text[:200]}")
    kept = {k: x for k, x in v.items() if k not in ("status", "engines", "input_image", "verified")}
    kept["candidates"] = placed(v.get("candidates", []))
    kept["votes"] = placed(v.get("votes", []))
    return compact(v["engines"]), kept


def context(text):
    """Stored guesses, or None when the transcription gave no guess."""
    x = json.loads(text) if text else {}
    if x.get("status") != "ready":
        return None
    if x.get("basis") != "transcription" or any(set(c) != CONTEXT_KEYS or c["engine"] != CONTEXT_ENGINE for c in x["candidates"]):
        raise ValueError(f"a context reading of another shape: {text[:200]}")
    kept = {k: v for k, v in x.items() if k not in ("status", "basis", "candidates")}
    kept["candidates"] = [[c["text"], c["offset"], c["before"], c["after"]] for c in x["candidates"]]
    return kept


def entry(cut, models, stored_visual, stored_context, loaded=False):
    """A slot's entry, or None when neither reading is ready."""
    if stored_visual is None and stored_context is None:
        return None
    out = {"cut": cut, "models": models, **({"loaded": True} if loaded else {})}
    if stored_visual is not None:
        out["visual"] = stored_visual
    if stored_context is not None:
        out["context"] = stored_context
    return out


def restore(engines, found):
    """What the routes answer from a slot's entry (None for none) and its models' engines."""
    unavailable = {"status": "unavailable", "candidates": []}
    out = {"visual": unavailable, "context": {**unavailable, "basis": "transcription"}}
    if found and found.get("visual"):
        v, names = found["visual"], [e["name"] for e in engines]

        def named(items):
            return [{**c, "engine": names[c["engine"]]} if isinstance(c.get("engine"), int) else c for c in items]

        out["visual"] = {**v, "status": "ready", "engines": engines, "verified": False,
                         "candidates": named(v["candidates"]), "votes": named(v["votes"])}
    if found and found.get("context"):
        x = found["context"]
        out["context"] = {**x, "status": "ready", "basis": "transcription",
                          "candidates": [{"text": t, "engine": CONTEXT_ENGINE, "offset": o, "before": b, "after": a}
                                         for t, o, b, a in x["candidates"]]}
    return out


def encode(slots):
    """A page object's bytes: entries by slot, in slot order."""
    return gzip.compress(compact({str(s): slots[s] for s in sorted(slots)}).encode(), mtime=0)


def read(folder, page):
    """A page's entries by slot from a folder of page objects; empty when it has none."""
    path = folder / f"{page}.json.gz"
    return {int(s): e for s, e in json.loads(gzip.decompress(path.read_bytes())).items()} if path.exists() else {}


def write(folder, page, slots):
    """A page object under `folder`, through a `.part` file; a page left with no entry is an empty object."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{page}.json.gz"
    part = path.with_suffix(".part")
    part.write_bytes(encode(slots))
    os.replace(part, path)
    return path
