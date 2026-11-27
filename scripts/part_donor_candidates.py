"""List, for each part no code point names, the drawings the site's composer could be given for it, and
characters to try them in.

A part is drawn from encoded characters: a description of it (`⿵𠆢一` for `{88}`, the top of 合), a single
character the tables confirm it as (亼), or a cut, a character less its other parts (`㇯合口`). Which of them
draws well is found by composing a character that holds the part with each one, that character's own glyph
excluded, and comparing it with that glyph (`apps/review/tools/part-donor-sheets.mjs`); the choices are kept
in `data/vocab/custom-part-donors.tsv`.

    uv run scripts/part_donor_candidates.py > candidates.json

Hosts come from BabelStone IDS and CHISE IDS as ids-input bundles them at the commit the parts table is
built from: a description naming one part once, with encoded characters besides.
"""

from __future__ import annotations

import json
import re
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_custom_parts

PART = re.compile(r"\{\d+\}|&[^;&\s]+;|#\([^)]*\)")
SUBTRACT = "㇯"
#: At most this many descriptions and cuts per part, and characters to try them in.
DESCRIPTIONS, CUTS, HOSTS = 2, 4, 3


def encoded(text: str) -> bool:
    """Only encoded characters and operators: no number, entity, chain, private-use character or the tables'
    mark for a part they cannot write (？)."""
    return bool(text) and "？" not in text and not PART.search(text) and not any(0xE000 <= ord(c) <= 0xF8FF or ord(c) >= 0xF0000 for c in text)


def han(char: str) -> bool:
    """A Han character or radical, not a symbol such as 〢."""
    return unicodedata.name(char, "").startswith(("CJK UNIFIED", "CJK RADICAL", "CJK COMPATIBILITY IDEOGRAPH", "KANGXI"))


def rank(char: str) -> int:
    """Commoner blocks first: the URO, then Extension A, then the rest."""
    point = ord(char)
    return 0 if 0x4E00 <= point <= 0x9FFF else 1 if 0x3400 <= point <= 0x4DBF else 2


def candidates() -> list[dict]:
    data = build_custom_parts.fetched()
    spelling, _ = build_custom_parts._spellings(data)
    # A drawing that is close only (`approximate`) is no candidate.
    parts = {key: {"names": names, "draws": [draw for draw in draws if encoded(draw) and draw not in approximate]}
             for key, names, draws, approximate, *_ in build_custom_parts.rows(data) if key not in spelling}
    owner = {name: key for key, part in parts.items() for name in part["names"]}
    cuts: dict[str, list[str]] = defaultdict(list)
    characters: dict[str, list[str]] = defaultdict(list)
    for component in data["components"]:
        if component.get("id") in parts:
            for cut in component.get("subtractions") or []:
                if encoded(cut) and cut not in cuts[component["id"]]:
                    cuts[component["id"]].append(cut)
            # A single Han character the tables confirm the part as (亼 for the top of 合) is a candidate too.
            for item in component.get("equivalents", []):
                text = item["text"]
                if item["status"] == "verified" and len(text) == 1 and encoded(text) and han(text) \
                        and text not in characters[component["id"]]:
                    characters[component["id"]].append(text)
    hosts: dict[str, dict[str, dict]] = defaultdict(dict)
    for char, text, table in build_custom_parts.descriptions():
        named = PART.findall(text)
        if len(char) == 1 and len(named) == 1 and named[0] in owner and encoded(text.replace(named[0], "")):
            hosts[owner[named[0]]].setdefault(char, {"char": char, "text": text, "name": named[0], "in": table})
    found = []
    for key, part in parts.items():
        ordered = sorted(cuts[key], key=lambda cut: (cut.count(SUBTRACT), rank(cut[cut.count(SUBTRACT)]), len(cut)))
        tried = list(dict.fromkeys(part["draws"][:DESCRIPTIONS] + characters[key] + ordered[:CUTS]))
        held = sorted(hosts[key].values(), key=lambda host: (rank(host["char"]), host["in"] != "babelstone", host["char"]))
        if tried and held:
            found.append({"key": key, "names": part["names"], "candidates": tried, "hosts": held[:HOSTS]})
    return found


if __name__ == "__main__":
    json.dump(candidates(), sys.stdout, ensure_ascii=False)
