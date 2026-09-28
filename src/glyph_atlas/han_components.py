"""Characters found by their components: 水骨, 氵骨 and 氵冖月 each find 滑.

A character's components are every character its Ideographic Description Sequences name, and theirs in
turn (`data/vocab/han-ids.tsv`): 滑 is ⿰氵骨 and 骨 is ⿳𭁟冖⺝, so 滑 holds 氵, 骨, 𭁟, 冖 and ⺝. Each
component also counts as the characters it writes (`data/vocab/han-component-forms.tsv`): ⺝ as 月, 氵
as 水. A character with several sequences (one per source region) holds what any of them names.

Components are counted, so 木木 asks for two of them and finds 林 and 森 but not 本. A query is two or
more characters, each an ideograph, a radical or a stroke; one character is a search for that character.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from functools import cache
from pathlib import Path

VOCAB = Path(__file__).resolve().parents[2] / "data" / "vocab"
# A term of more characters than this is not a component search (the Worker's COMPONENT_PARTS).
PARTS = 8
# How many of the rarest component's characters a search reads (the Worker's COMPONENT_SCAN).
SCAN = 3000

# Ideographic Description Characters and BabelStone's other operators (subtraction, mirror, rotation,
# the variation indicator) describe layout; the region list and `{n}` name no character of their own.
LAYOUT = re.compile(r"\([^)]*\)$|\{\d+\}|[⿰-⿿㇯〾？]")


def _read(name: str) -> list[list[str]]:
    """The rows of a vocab table, past its `#` header and its column names."""
    lines = [
        line
        for line in (VOCAB / name).read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    ]
    return [line.split("\t") for line in lines[1:]]


@cache
def _sequences() -> dict[str, tuple[str, ...]]:
    return {row[1]: tuple(row[2].split(" ")) for row in _read("han-ids.tsv")}


@cache
def _forms() -> dict[str, str]:
    return {row[0]: row[1] for row in _read("han-component-forms.tsv")}


@cache
def unified() -> dict[str, str]:
    """Radical characters Unicode unifies with an ideograph (⺡ is 氵); a query is read through these."""
    return {row[0]: row[1] for row in _read("han-component-forms.tsv") if row[2] == "unicode-ucd"}


def _names(component: str) -> list[str]:
    """The component and every character it writes, following the forms table (⺡, 氵, 水)."""
    names = [component]
    while (written := _forms().get(names[-1])) and written not in names:
        names.append(written)
    return names


# A few sequences lead back to the character they describe (水 through 氺); the path being expanded
# stops there. A closure that stopped short is not kept, so each character's own is complete.
_closures: dict[str, Counter] = {}


def _expand(char: str, path: frozenset[str]) -> tuple[Counter, bool]:
    if char in _closures:
        return _closures[char], False
    found: Counter = Counter()
    short = False
    for sequence in _sequences().get(char, ()):
        parts: Counter = Counter()
        for part in LAYOUT.sub("", sequence):
            if part == char:
                continue
            parts[part] += 1
            if part in path:
                short = True
                continue
            inner, stopped = _expand(part, path | {char})
            parts.update(inner)
            short |= stopped
        found |= parts
    if not short:
        _closures[char] = found
    return found, short


def _written(char: str) -> Counter:
    """The components as the sequences write them, counted: the most any one sequence names of each."""
    return _expand(char, frozenset())[0]


def components(char: str) -> Counter:
    """Every component of `char`, each counted under its own name and the characters it writes."""
    found: Counter = Counter()
    for part, n in _written(char).items():
        for name in _names(part):
            found[name] += n
    found.pop(char, None)
    return found


def query(term: str) -> Counter | None:
    """The components a term asks for, or None when it is not a component search."""
    chars = [unified().get(c, c) for c in unicodedata.normalize("NFKC", term.strip()) if not c.isspace()]
    if not 2 <= len(chars) <= PARTS or not all(_component_like(c) for c in chars):
        return None
    return Counter(chars)


def _component_like(char: str) -> bool:
    return (
        unicodedata.name(char, "").startswith(
            ("CJK UNIFIED IDEOGRAPH", "CJK COMPATIBILITY IDEOGRAPH", "CJK RADICAL", "CJK STROKE")
        )
        or char in _sequences()
        or char in _forms()
    )


@cache
def direct(char: str) -> frozenset[str]:
    """The components a sequence names at its top level, under every name they go by: 日 and 月 for 明."""
    return frozenset(
        name
        for sequence in _sequences().get(char, ())
        for part in LAYOUT.sub("", sequence)
        if part != char
        for name in _names(part)
    )


def size(char: str) -> int:
    """How many components a character is built from, counted through every level."""
    return sum(_written(char).values())


def rows():
    """(component, character, count, direct, tier, size) for every component of every character with a sequence."""
    for char in _sequences():
        top, rung, weight = direct(char), tier(char), size(char)
        for part, n in components(char).items():
            yield part, char, n, int(part in top), rung, weight


@cache
def _index() -> dict[str, dict[str, int]]:
    """Component -> {character: count}."""
    index: dict[str, dict[str, int]] = {}
    for part, char, n, *_rest in rows():
        index.setdefault(part, {})[char] = n
    return index


def tier(char: str) -> int:
    """0 for the CJK Unified Ideographs block, 1 for the rest of the BMP, 2 beyond it: the everyday
    characters come before the rare ones built the same way."""
    code = ord(char)
    return 0 if 0x4E00 <= code <= 0x9FFF else 1 if code <= 0xFFFF else 2


def rank(char: str, wanted) -> tuple[int, int, int, int]:
    """Characters that hold more of the asked components at their top level come first (明 before 胄 for
    日月), then the everyday ones, then the simpler ones."""
    return -len(direct(char) & set(wanted)), tier(char), size(char), ord(char)


def search(term: str) -> list[str]:
    """The characters holding every component `term` asks for, best first; [] when it asks for none.

    Like the hosted search, only SCAN of the rarest component's characters are considered, taken in the
    order its D1 key keeps them: tier, size, then the `U+` code point as text.
    """
    wanted = query(term)
    if not wanted:
        return []
    index = _index()
    lists = sorted(((index.get(part, {}), n) for part, n in wanted.items()), key=lambda item: len(item[0]))
    first, n = lists[0]
    scanned = sorted(first, key=lambda char: (tier(char), size(char), f"U+{ord(char):04X}"))[:SCAN]
    found = [
        char
        for char in scanned
        if first[char] >= n and all(chars.get(char, 0) >= m for chars, m in lists[1:])
    ]
    return sorted(found, key=lambda char: rank(char, wanted))
