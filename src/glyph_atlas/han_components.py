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

# BabelStone's operators by how many operands each takes. ㇯ subtracts its second operand from its
# first (㇯鸟丶 is 乌); 〾 marks a sequence as approximate. `{n}` and ？ name no encoded character.
BINARY = set("⿰⿱⿴⿵⿶⿷⿸⿹⿺⿻⿼⿽㇯")
TERNARY = set("⿲⿳")
UNARY = set("⿾⿿〾")
TOKEN = re.compile(r"\{\d+\}|.")


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


def _parse(tokens: list[str], at: int = 0) -> tuple[tuple, int]:
    """One description from `tokens[at]`: a leaf, or an operator and its operands."""
    token = tokens[at]
    arity = 2 if token in BINARY else 3 if token in TERNARY else 1 if token in UNARY else 0
    if not arity:
        return ("leaf", token), at + 1
    operands, at = [], at + 1
    for _ in range(arity):
        operand, at = _parse(tokens, at)
        operands.append(operand)
    return (token, operands), at


def _tree(sequence: str) -> tuple:
    return _parse(TOKEN.findall(re.sub(r"\([^)]*\)$", "", sequence)))[0]


class _Part:
    """What one description holds: every component through every level under each of its names,
    counted; how many pieces it is built from; and the names at its own top level."""

    __slots__ = ("held", "size", "top")

    def __init__(self, held: Counter | None = None, size: int = 0, top: frozenset[str] = frozenset()):
        self.held, self.size, self.top = held or Counter(), size, top


# A few sequences lead back to the character they describe (水 through 氺); the path being expanded
# stops there. A description that stopped short is not kept, so each character's own is complete.
_described: dict[str, _Part] = {}


def _evaluate(node: tuple, char: str, path: frozenset[str]) -> tuple[_Part, bool]:
    kind, value = node
    if kind == "leaf":
        if value == char or value == "？" or value.startswith("{"):
            return _Part(size=1), False
        names = _names(value)
        held = Counter(names)
        if value in path:
            return _Part(held, 1, frozenset(names)), True
        inner, short = _describe(value, path | {char})
        return _Part(held + inner.held, 1 + inner.size, frozenset(names)), short
    parts, short = [], False
    for operand in value:
        part, stopped = _evaluate(operand, char, path)
        parts.append(part)
        short |= stopped
    if kind == "㇯":
        # The second operand is taken away from the first, and none of it is in the character.
        whole, taken = parts
        return _Part(whole.held - taken.held, max(whole.size - taken.size, 1), whole.top - taken.top), short
    held: Counter = Counter()
    for part in parts:
        held.update(part.held)
    return _Part(held, sum(p.size for p in parts), frozenset().union(*(p.top for p in parts))), short


def _describe(char: str, path: frozenset[str]) -> tuple[_Part, bool]:
    """A character's description: each sequence counted on its own, then the most any one holds of
    each component, so two regional forms (礻 and 示 in 礼) never add up to two."""
    if char in _described:
        return _described[char], False
    found, short = _Part(), False
    for sequence in _sequences().get(char, ()):
        part, stopped = _evaluate(_tree(sequence), char, path | {char})
        short |= stopped
        found = _Part(found.held | part.held, max(found.size, part.size), found.top | part.top)
    if not short:
        _described[char] = found
    return found, short


def components(char: str) -> Counter:
    """Every component of `char`, counted, under its own name and the characters it writes."""
    held = Counter(_describe(char, frozenset())[0].held)
    held.pop(char, None)
    return held


def direct(char: str) -> frozenset[str]:
    """The components a sequence names at its top level, under every name they go by: 日 and 月 for 明."""
    return _describe(char, frozenset())[0].top


def size(char: str) -> int:
    """How many pieces a character is built from, counted through every level."""
    return _describe(char, frozenset())[0].size


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
