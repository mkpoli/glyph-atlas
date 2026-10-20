"""A crop's model votes as `votes` holds them (migration 0006), from the export's `units.visual` and
`units.context`.

`visual` is what the crop models read in the crop's image (`read_crops` in export_cloudflare.py). It is
kept without what every row repeated: its engines are the `codes` entry `models` names (field 'models',
key their JSON), each vote and candidate names its engine by place in that list, and `status`,
`input_image` (the crop's own image) and `verified` (always false) are left out. `context` is the
nearby transcription's guesses (`context_guesses`), each candidate as [text, offset, before, after],
since every one names the same engine. A reading that is not ready is not stored.

`restore` gives back what the suggestions routes answered, less `input_image`.
"""

import json

CONTEXT_ENGINE = "Nearby transcription"
CONTEXT_KEYS = {"text", "engine", "offset", "before", "after"}


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def visual(text):
    """(engines key, stored text), or (None, None) when the models gave no reading."""
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
    return compact(v["engines"]), compact(kept)


def context(text):
    """Stored text, or None when the transcription gave no guess."""
    x = json.loads(text) if text else {}
    if x.get("status") != "ready":
        return None
    if x.get("basis") != "transcription" or any(set(c) != CONTEXT_KEYS or c["engine"] != CONTEXT_ENGINE for c in x["candidates"]):
        raise ValueError(f"a context reading of another shape: {text[:200]}")
    kept = {k: v for k, v in x.items() if k not in ("status", "basis", "candidates")}
    kept["candidates"] = [[c["text"], c["offset"], c["before"], c["after"]] for c in x["candidates"]]
    return compact(kept)


def restore(engines, stored_visual, stored_context):
    """What the routes answer from a `votes` row and its models' engines (both None for a crop with
    no row)."""
    unavailable = {"status": "unavailable", "candidates": []}
    out = {"visual": unavailable, "context": {**unavailable, "basis": "transcription"}}
    if stored_visual:
        v, names = json.loads(stored_visual), [e["name"] for e in engines]

        def named(items):
            return [{**c, "engine": names[c["engine"]]} if isinstance(c.get("engine"), int) else c for c in items]

        out["visual"] = {**v, "status": "ready", "engines": engines, "verified": False,
                         "candidates": named(v["candidates"]), "votes": named(v["votes"])}
    if stored_context:
        x = json.loads(stored_context)
        out["context"] = {**x, "status": "ready", "basis": "transcription",
                          "candidates": [{"text": t, "engine": CONTEXT_ENGINE, "offset": o, "before": b, "after": a}
                                         for t, o, b, a in x["candidates"]]}
    return out
