"""Component variants: a component two attested variant pairs write differently, and what it predicts.

強 is ⿰弓𧈧 and 强 is ⿰弓虽 (`data/vocab/han-ids.tsv`), and the 異体字 graph gives the two as variants
(`data/vocab/kanji-variants.tsv`). They differ in one component, 𧈧 against 虽, and inside it in one
more, 厶 against 口, so the pair attests both substitutions. A substitution is kept once `THRESHOLD`
distinct pairs attest it (`attest`), and every character is then tried with each kept substitution at
every depth of its decomposition (`derive`): a result that is another character's sequence is a
derived variant of it, and one no character has is an unencoded form, written as its sequence. 還
(⿺辶睘) and 環 (⿰𤣩睘) are attested with 𮟃 (⿺辶𦊷) and 𤨔 (⿰𤣩𦊷), so 睘→𦊷 is kept, and 寰 (⿱宀睘)
gets ⿱宀𦊷.

Only pairs the graph says may be written for each other count (`refs.WRITTEN_FOR`), and only a
difference below the whole character: 雲 and 云 differ at the top and attest nothing. A substitution is
undirected. Derived variants are predictions: they never join a grapheme and a gallery never widens to
them, and each one names its substitution and the pairs behind it.

A description is a tree: a component is a string, a node `(operator, parts)`. Sequences marked
approximate (〾), those that subtract (㇯) and those with an unrepresentable part (？) are left out.
BabelStone's numbered unencoded components (`{25}`) are kept as parts, but no unencoded form holding
one is written, since nothing can display it.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field

from glyph_atlas.han_components import BINARY, TERNARY, TOKEN, UNARY

#: How many distinct attested pairs a substitution needs before it predicts anything, and how many
#: distinct contexts (see `Attested`) it must be seen in. One pair is a single dictionary's word for
#: two characters, and one position replicated across a thousand pairs is one claim, not a pattern.
#: See the build script's report for the distribution this was chosen against.
THRESHOLD = 2
#: How many levels below a character a substitution is looked for, in the pairs and in the derivation.
DEPTH = 4
SKIPPED = set("〾㇯？")
STROKES = set("一丨丶丿乀乁乙乚乛亅")
UNENCODED = re.compile(r"\{\d+\}")

Tree = str | tuple[str, tuple["Tree", ...]]


def parse(sequence: str) -> Tree:
    """A sequence without its region tag as a tree: `⿰弓𧈧` is `("⿰", ("弓", "𧈧"))`."""
    tokens = TOKEN.findall(re.sub(r"\([^)]*\)$", "", sequence))
    tree, at = _parse(tokens, 0)
    if at != len(tokens):
        raise ValueError(f"trailing parts in {sequence!r}")
    return tree


def _parse(tokens: list[str], at: int) -> tuple[Tree, int]:
    token = tokens[at]
    arity = 2 if token in BINARY else 3 if token in TERNARY else 1 if token in UNARY else 0
    if not arity:
        return token, at + 1
    parts, at = [], at + 1
    for _ in range(arity):
        part, at = _parse(tokens, at)
        parts.append(part)
    return (token, tuple(parts)), at


def text(tree: Tree) -> str:
    """A tree written back as a sequence."""
    return tree if isinstance(tree, str) else tree[0] + "".join(text(part) for part in tree[1])


@dataclass
class Descriptions:
    """Every character's usable sequences, read so that equal shapes are equal trees.

    A component is named by the unified ideograph for a radical form Unicode unifies with one (⺡ is
    氵, ⼈ is 人). A part whose sequence is some character's is that character (⿱一口 is 𠮛), so a
    description that spells a component out matches one that names it; the lowest code point of a
    shared sequence names it. `⿱X⿱YZ` and `⿱⿱XYZ` are both `⿳XYZ`, and the same for ⿰ and ⿲.
    """

    raw: dict[str, list[str]]
    unified: dict[str, str] = field(default_factory=dict)
    named: dict[str, str] = field(init=False)
    trees: dict[str, list[Tree]] = field(init=False)
    index: dict[str, set[str]] = field(init=False)

    def __post_init__(self):
        self.named = {}
        parsed: dict[str, list[Tree]] = {}
        for char, sequences in self.raw.items():
            for sequence in sequences:
                if SKIPPED & set(sequence):
                    continue
                tree = parse(sequence)
                if tree == char:
                    continue
                parsed.setdefault(char, []).append(tree)
        # Two passes: the names found in the first let the second collapse parts spelled out.
        for _ in range(2):
            named: dict[str, str] = {}
            for char in sorted(parsed, key=ord):
                for tree in parsed[char]:
                    shaped = self.whole(tree)
                    if not isinstance(shaped, str):
                        named.setdefault(text(shaped), char)
            self.named = named
        self.trees = {char: _unique([self.whole(tree) for tree in trees]) for char, trees in parsed.items()}
        self.index = defaultdict(set)
        for char, trees in self.trees.items():
            for tree in trees:
                self.index[text(tree)].add(char)

    def leaf(self, component: str) -> str:
        return self.unified.get(component, component)

    def part(self, tree: Tree) -> Tree:
        """A part of a description: its own parts read first, then named by the character it spells."""
        if isinstance(tree, str):
            return self.leaf(tree)
        shaped = self.whole(tree)
        return shaped if isinstance(shaped, str) else self.named.get(text(shaped), shaped)

    def whole(self, tree: Tree) -> Tree:
        """A character's own description: its parts read, the node itself never renamed."""
        if isinstance(tree, str):
            return self.leaf(tree)
        operator, parts = tree
        parts = tuple(self.part(part) for part in parts)
        return _flatten(operator, parts)

    def expansions(self, tree: Tree) -> list[Tree]:
        """What a part may be read as: a node itself, a character its own sequences or itself if none."""
        if isinstance(tree, str):
            return self.trees.get(tree) or [tree]
        return [tree]

    def characters(self, tree: Tree) -> set[str]:
        """The characters whose own description is `tree`, or which `tree` names."""
        if isinstance(tree, str):
            return {tree} if tree in self.raw else set()
        return set(self.index.get(text(tree), ()))


def _flatten(operator: str, parts: tuple[Tree, ...]) -> Tree:
    three = {"⿱": "⿳", "⿰": "⿲"}.get(operator)
    if three and len(parts) == 2:
        first, second = parts
        if not isinstance(second, str) and second[0] == operator:
            return three, (first, *second[1])
        if not isinstance(first, str) and first[0] == operator:
            return three, (*first[1], second)
    return operator, parts


def _unique(items: list) -> list:
    seen, out = set(), []
    for item in items:
        key = text(item)
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


def substitutions(desc: Descriptions, a: str, b: str) -> dict[tuple[str, str], set[tuple[str, str] | None]]:
    """The components `a` and `b` differ in when they differ in exactly one, at every level, each with
    the substitution it was found inside (None at the top): 強 and 强 give 𧈧→虽, and 厶→口 inside it.
    Empty when they differ at the top or in more than one part. A single stroke is no component."""
    found: dict[tuple[str, str], set[tuple[str, str] | None]] = defaultdict(set)
    for x in desc.trees.get(a, ()):
        for y in desc.trees.get(b, ()):
            if text(x) != text(y):
                _single(desc, x, y, 0, None, found)
    return dict(found)


def _single(desc: Descriptions, x: Tree, y: Tree, depth: int, host, found) -> None:
    """Record the one part in which the nodes `x` and `y` differ, and the chain inside it."""
    if isinstance(x, str) or isinstance(y, str) or x[0] != y[0] or len(x[1]) != len(y[1]):
        return
    differ = [i for i, (p, q) in enumerate(zip(x[1], y[1], strict=True)) if text(p) != text(q)]
    if len(differ) != 1:
        return
    p, q = x[1][differ[0]], y[1][differ[0]]
    if stroke(p) or stroke(q):
        return
    sub = _ordered(text(p), text(q))
    found[sub].add(host)
    if depth + 1 < DEPTH:
        for inner_p in desc.expansions(p):
            for inner_q in desc.expansions(q):
                if text(inner_p) != text(inner_q):
                    _single(desc, inner_p, inner_q, depth + 1, sub, found)


def stroke(part: Tree) -> bool:
    """A single stroke: the CJK Strokes block and the ideographs that are one stroke (一, 丨, 乙, …)."""
    return isinstance(part, str) and (0x31C0 <= ord(part[0]) <= 0x31EF or part in STROKES)


def _ordered(p: str, q: str) -> tuple[str, str]:
    return (p, q) if (len(p), p) <= (len(q), q) else (q, p)


@dataclass(frozen=True)
class Attested:
    """A substitution with the pairs that attest it, each with the sources that state the pair, and
    the contexts it was seen in: each pair where it is a part of the character itself, and each
    enclosing substitution where it is inside one (𧈧→虽 for 厶→口 in 強 and 强). The contexts are
    what `THRESHOLD` counts, so 灬 against 一 in the thousand pairs of 魚 and 鱼 counts once."""

    a: str
    b: str
    pairs: tuple[tuple[str, str, tuple[str, ...]], ...]
    contexts: tuple[str, ...]

    @property
    def count(self) -> int:
        return len(self.contexts)


def attest(desc: Descriptions, pairs: Iterable[tuple[str, str, Iterable[str]]]) -> dict[tuple[str, str], Attested]:
    """Every substitution the pairs attest, with the pairs behind it, each pair once."""
    found: dict[tuple[str, str], dict[tuple[str, str], set[str]]] = defaultdict(dict)
    contexts: dict[tuple[str, str], set[str]] = defaultdict(set)
    for a, b, sources in pairs:
        pair = tuple(sorted((a, b), key=ord))
        for sub, hosts in substitutions(desc, a, b).items():
            found[sub].setdefault(pair, set()).update(sources)
            contexts[sub].update("/".join(host or pair) for host in hosts)
    return {
        sub: Attested(*sub, tuple((p, q, tuple(sorted(s))) for (p, q), s in sorted(by.items(), key=lambda i: (ord(i[0][0]), ord(i[0][1])))),
                      tuple(sorted(contexts[sub])))
        for sub, by in found.items()
    }


def kept(found: dict[str, Attested]) -> list[Attested]:
    """What predicts: `THRESHOLD` distinct attesting pairs seen in `THRESHOLD` distinct contexts.

    Two pairs from one position (one enclosing substitution reached by both) are one claim twice,
    and one pair seen twice (at the top and inside the same decomposition) is one claim; either way
    nothing predicts. The pairs a substitution rests on are its own record either way.
    """
    return [item for item in found.values() if item.count >= THRESHOLD and len(item.pairs) >= THRESHOLD]


@dataclass(frozen=True)
class Derived:
    """A character and a form one substitution makes of it: another character, or an unencoded form
    written as its sequence (`encoded` false)."""

    char: str
    other: str
    encoded: bool
    was: str
    became: str


def equivalents(kept: Iterable[Attested]) -> dict[str, set[str]]:
    table: dict[str, set[str]] = defaultdict(set)
    for sub in kept:
        table[sub.a].add(sub.b)
        table[sub.b].add(sub.a)
    return table


def derive(desc: Descriptions, table: dict[str, set[str]], chars: Iterable[str] | None = None) -> Iterator[Derived]:
    """Each character of `chars` (all with a description when None) with one substitution of `table`
    made at any depth: in the character itself, in a part, or in a part of a part's own sequence."""
    made = _Maker(desc, table)
    for char in desc.trees if chars is None else chars:
        seen: set[tuple[str, str, str]] = set()
        for was, became, tree in made.whole(char):
            key = text(tree)
            found = desc.characters(tree)
            others = found - {char}
            if others:
                encoded = True
            elif found or UNENCODED.search(key) or len(key) == 1:
                continue
            else:
                others, encoded = {key}, False
            for other in others:
                if (other, was, became) not in seen:
                    seen.add((other, was, became))
                    yield Derived(char, other, encoded, was, became)


class _Maker:
    """The forms one substitution makes of a character, with what a part becomes kept per part: 睘
    turns into the same forms in 還, 環 and 寰."""

    def __init__(self, desc: Descriptions, table: dict[str, set[str]]):
        self.desc, self.table = desc, table
        self.parts: dict[tuple[str, int], list[tuple[str, str, Tree]]] = {}

    def whole(self, char: str) -> Iterator[tuple[str, str, Tree]]:
        for became in self.table.get(char, ()):
            yield char, became, self.desc.whole(parse(became))
        for tree in self.desc.trees.get(char, ()):
            for was, became, replaced in self.replaced(tree, 0):
                yield was, became, self.desc.whole(replaced)

    def replaced(self, tree: Tree, depth: int) -> Iterator[tuple[str, str, Tree]]:
        """`tree` with one substitution made in one of its parts, at any depth below it."""
        if isinstance(tree, str):
            return
        operator, parts = tree
        for i, part in enumerate(parts):
            for was, became, new in self.part(part, depth + 1):
                yield was, became, (operator, (*parts[:i], new, *parts[i + 1:]))

    def part(self, part: Tree, depth: int) -> list[tuple[str, str, Tree]]:
        """A part replaced whole, or with a substitution inside it or inside its own sequence."""
        key = (text(part), depth)
        if key in self.parts:
            return self.parts[key]
        found = [(key[0], became, self.desc.part(parse(became))) for became in sorted(self.table.get(key[0], ()))]
        if depth < DEPTH:
            for inner in self.desc.expansions(part):
                found += [(was, became, self.desc.part(new)) for was, became, new in self.replaced(inner, depth)]
        self.parts[key] = found
        return found
