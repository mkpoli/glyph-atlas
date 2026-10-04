"""Component variants: a component two attested variant pairs write differently, and what it predicts.

強 is ⿰弓𧈧 and 强 is ⿰弓虽 (`data/vocab/han-ids.tsv`), and the 異体字 graph gives the two as variants
(`data/vocab/kanji-variants.tsv`). They differ in one component, 𧈧 against 虽, and inside it in one
more, 厶 against 口, so the pair attests both substitutions. A substitution is kept once `THRESHOLD`
distinct pairs attest it (`attest`, `kept`) and at least `AGREEMENT` of the pairs of characters it
predicts are pairs the graph already gives (`agreeing`). Every character is then tried with up to
`STEPS` substitutions, each in a part of its own, at every depth of its decomposition (`derive`): a
result that is another character's sequence is a derived variant of it, and one no character has is
an unencoded form, written as its sequence. 嗚 (⿰口烏) gets 呜 through 烏→乌, and ⿰厶烏 through 口→厶.

What feeds it is the build script's business: pairs the graph says may be written for each other
(`refs.WRITTEN_FOR`, simplifications included: a gallery keeps those apart, an attestation does not),
and only a difference below the whole character: 雲 and 云 differ at the top and attest nothing. A
substitution is undirected. Derived variants are predictions: they never join a grapheme and a
gallery never widens to them, and each one names its substitution and the pairs behind it.

A description is a tree: a component is a string, a node `(operator, parts)`. Sequences marked
approximate (〾), those that subtract (㇯) and those with an unrepresentable part (？) are left out.
BabelStone's numbered unencoded components (`{25}`) are kept as parts, but no unencoded form holding
one is written, since nothing can display it.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field, replace

from glyph_atlas.han_components import BINARY, TERNARY, TOKEN, UNARY

#: How many distinct attested pairs a substitution needs before it predicts anything, and how many
#: distinct contexts (see `Attested`) it must be seen in. One pair is a single dictionary's word for
#: two characters, and one position replicated across a thousand pairs is one claim, not a pattern.
#: See the build script's report for the distribution this was chosen against.
THRESHOLD = 2
#: Of the pairs of encoded characters a substitution predicts, the share the 異体字 graph must already
#: give as written variants for the substitution to be kept (`agreeing`). 口 against 氵 has fourteen
#: attesting pairs (唾 and 涶, 噥 and 濃, …) and predicts about 1,500 pairs, of which the graph states
#: only those fourteen: it holds where 口 and 氵 are the semantic part of words of speech and of
#: liquid, and nowhere else. 扌 against 木 is such a swap of meaning too; from 0.3 up the ones that
#: predict most are interchanges of writing (厶 and 口, 宀 and 宂, 厂 and 广).
AGREEMENT = 0.3
#: How many levels below a character a substitution is looked for, in the pairs and in the derivation.
DEPTH = 4
SKIPPED = set("〾㇯？")
#: The ideographs of one stroke: every character whose Unihan kTotalStrokes is 1 (Unicode 18.0.0,
#: Unihan_IRGSources.txt). With the CJK Strokes block they are the strokes, and a swap of one of them
#: against anything is no component.
STROKES = set("一丨丶丿乀乁乙乚乛亅𠃉𠃊𠃋𠃌𠃍𠃎𠃑𠄌𠄎𡿨𪛙𬼂\U0002F802")
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

    A tree keeps the token its sequence writes (色 is ⿱⺈巴), and two trees are compared through
    `fold`, which reads a radical form Unicode unifies with an ideograph as that ideograph (⺈ is 刀,
    ⺡ is 氵). A substitution found between ⺈ and 𠂉 is therefore a substitution of ⺈, and the
    derivation never applies it to a 刀 the sources wrote as 刀. A part whose sequence is some
    character's is that character (⿱一口 is 𠮛), so a description that spells a component out matches
    one that names it; the lowest code point of a shared sequence names it. `⿱X⿱YZ` and `⿱⿱XYZ` are
    both `⿳XYZ`, and the same for ⿰ and ⿲.

    `own` holds each character's descriptions as its sequences write them. `trees` adds the shape each
    takes when a named part's own sequence flattens into its parent (`flattened`): 溪 is ⿰氵奚, so 鸂
    (⿰溪鳥) also reads ⿲氵奚鳥 and meets 㶉 (⿲氵奚鸟). `regions` gives the source regions of each tree
    of `trees`, in the same order.
    """

    raw: dict[str, list[str]]
    unified: dict[str, str] = field(default_factory=dict)
    named: dict[str, str] = field(init=False)
    own: dict[str, list[Tree]] = field(init=False)
    trees: dict[str, list[Tree]] = field(init=False)
    regions: dict[str, list[frozenset[str]]] = field(init=False)
    index: dict[str, set[str]] = field(init=False)
    _folded: dict[Tree, Tree] = field(init=False, default_factory=dict, repr=False)

    def __post_init__(self):
        self.named = {}
        parsed: dict[str, list[tuple[Tree, frozenset[str]]]] = {}
        for char, sequences in self.raw.items():
            for sequence in sequences:
                if SKIPPED & set(sequence):
                    continue
                tree = parse(sequence)
                if tree == char:
                    continue
                parsed.setdefault(char, []).append((tree, regions(sequence)))
        # Passes until no name changes: the names one pass finds let the next collapse parts that
        # are spelled out, and a chain of spellings needs a pass per link.
        for _ in range(8):
            named: dict[str, str] = {}
            for char in sorted(parsed, key=ord):
                for tree, _ in parsed[char]:
                    shaped = self.whole(tree)
                    if not isinstance(shaped, str):
                        named.setdefault(self.folded_text(shaped), char)
            if named == self.named:
                break
            self.named = named
        shapes = {char: _merged([(self.whole(tree), where) for tree, where in found])
                  for char, found in parsed.items()}
        self.own = {char: [tree for tree, _ in found] for char, found in shapes.items()}
        for char, found in shapes.items():
            found += [(flat, where) for tree, where in list(found)
                      for flat in self.flattened(tree, frozenset({char})) if text(flat) != text(tree)]
            shapes[char] = _merged(found)
        self.trees = {char: [tree for tree, _ in found] for char, found in shapes.items()}
        self.regions = {char: [where for _, where in found] for char, found in shapes.items()}
        self.index = defaultdict(set)
        for char, trees in self.trees.items():
            for tree in trees:
                self.index[self.folded_text(tree)].add(char)

    def leaf(self, component: str) -> str:
        return self.unified.get(component, component)

    def fold(self, tree: Tree) -> Tree:
        """`tree` with every leaf read through `unified`: the shape two trees are compared as."""
        if isinstance(tree, str):
            return self.leaf(tree)
        if tree in self._folded:
            return self._folded[tree]
        operator, parts = tree
        folded = (operator, tuple(self.fold(part) for part in parts))
        self._folded[tree] = folded
        return folded

    def folded_text(self, tree: Tree) -> str:
        """A tree written as the comparison reads it (⿱⺈巴 as ⿱刀巴)."""
        return text(self.fold(tree))

    def part(self, tree: Tree) -> Tree:
        """A part of a description: its own parts read first, then named by the character it spells."""
        if isinstance(tree, str):
            return tree
        shaped = self.whole(tree)
        return shaped if isinstance(shaped, str) else self.named.get(self.folded_text(shaped), shaped)

    def whole(self, tree: Tree) -> Tree:
        """A character's own description: its parts read, the node itself never renamed."""
        if isinstance(tree, str):
            return tree
        operator, parts = tree
        parts = tuple(self.part(part) for part in parts)
        return _flatten(operator, parts)

    def expansions(self, tree: Tree) -> list[Tree]:
        """What a part may be read as: a node itself, a character its own sequences or itself if none."""
        if isinstance(tree, str):
            return self.trees.get(tree) or [tree]
        return [tree]

    def flattened(self, tree: Tree, path: frozenset[str]) -> list[Tree]:
        """`tree` with every named part whose own sequence flattens into its parent spelled out.

        A ⿰ part spelled ⿰ inside a ⿰ parent, or ⿱ inside ⿱, flattens into ⿲ or ⿳; a part whose
        sequence would not flatten stays named, since `named` already matches it. A part with several
        such sequences gives one tree for each, and so does a sequence that flattens in turn and
        keeps the parent's operator. `path` holds the characters being spelled out, so a sequence
        that names its own character is never expanded into itself.
        """
        if isinstance(tree, str):
            return [tree]
        operator, parts = tree
        choices: list[list[Tree]] = []
        for part in parts:
            if isinstance(part, str):
                spellings = [] if part in path or operator not in FLATTENING or len(parts) != 2 else [
                    spelling for spelling in self.own.get(part, ())
                    if not isinstance(spelling, str) and spelling[0] == operator]
                options = [flat for spelling in spellings
                           for flat in (spelling, *self.flattened(spelling, path | {part}))
                           if flat[0] == operator]
                choices.append(_unique(options) or [part])
            else:
                choices.append(self.flattened(part, path))
        combos: list[tuple[Tree, ...]] = [()]
        for options in choices:
            combos = [combo + (option,) for combo in combos for option in options]
        return _unique([_flatten(operator, combo) for combo in combos])

    def characters(self, tree: Tree) -> set[str]:
        """The characters one of whose trees is `tree`, or the shape it takes with a named part
        spelled out (`flattened`: ⿰溪鸟 is 㶉's ⿲氵奚鸟), or which `tree` names."""
        if isinstance(tree, str):
            return {tree} if tree in self.raw else set()
        found = self.index.get(self.folded_text(tree))
        if found:
            return set(found)
        return {char for flat in self.flattened(tree, frozenset())
                for char in self.index.get(self.folded_text(flat), ())}


FLATTENING = {"⿱": "⿳", "⿰": "⿲"}
#: A sequence's region tag (`(GHTJKP)`, `(GHTKP[B])`); `UCS2003` is one source, not five regions.
REGION = re.compile(r"\(([^)]*)\)$")
#: The regions of a sequence with no tag: it describes every region's glyph.
EVERYWHERE = frozenset({"*"})


def regions(sequence: str) -> frozenset[str]:
    """The source regions a sequence describes: `⿱⺈巴(GHTJKPV)` gives G, H, T, J, K, P and V.
    A bracketed letter (`(GHTKP[B])`) is BabelStone's annotation of the glyph, not a region the
    sequence is tagged for, and is left out; a tag of bracketed letters alone (`([G])`) is read as no
    tag."""
    tag = REGION.search(sequence)
    if not tag:
        return EVERYWHERE
    if tag[1] == "UCS2003":
        return frozenset({tag[1]})
    return frozenset(re.findall(r"[A-Z]", re.sub(r"\[[^\]]*\]", "", tag[1]))) or EVERYWHERE


def _overlap(x: frozenset[str], y: frozenset[str]) -> bool:
    return bool(x & y) or EVERYWHERE in (x, y)


def _merged(found: list[tuple[Tree, frozenset[str]]]) -> list[tuple[Tree, frozenset[str]]]:
    """Each tree once, in first-seen order, with the regions of every sequence that writes it."""
    by: dict[str, tuple[Tree, frozenset[str]]] = {}
    for tree, where in found:
        key = text(tree)
        by[key] = (tree, by[key][1] | where) if key in by else (tree, where)
    return list(by.values())


def _flatten(operator: str, parts: tuple[Tree, ...]) -> Tree:
    three = FLATTENING.get(operator)
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
    Empty when they differ at the top or in more than one part. A single stroke is no component.

    Empty too when the two share a tree: one analysis says they are the same shape, so their other
    analyses show each character's own looseness rather than a way the two differ. Only trees of a
    common source region are compared when the two have any (免 is ⿱⺈… in G, H, T, K and P and
    ⿱{2}儿 in J, and 𭀠 is ⿱𠂉… in J, so 免's J tree is the one set against 𭀠); with none in
    common, every tree is. A shared tree counts whatever its regions.

    A flattened reading (`Descriptions.flattened`) brings a part's own parts to the top, so a
    difference the trees as written find inside a named part (奚 against 𢀖 inside 溪 against 渓)
    would also turn up at the top. It is recorded at the top only when the trees as written do not
    find it at all (鳥 against 鸟 in 鸂 ⿰溪鳥 and 㶉 ⿲氵奚鸟), so it stays the one position it is."""
    ours, theirs = desc.trees.get(a, ()), desc.trees.get(b, ())
    if {desc.folded_text(tree) for tree in ours} & {desc.folded_text(tree) for tree in theirs}:
        return {}
    compared = [(x, y) for x, rx in zip(ours, desc.regions.get(a, ()), strict=True)
                for y, ry in zip(theirs, desc.regions.get(b, ()), strict=True) if _overlap(rx, ry)]
    own = {text(tree) for tree in desc.own.get(a, ())} | {text(tree) for tree in desc.own.get(b, ())}
    found: dict[tuple[str, str], set[tuple[str, str] | None]] = defaultdict(set)
    flat: dict[tuple[str, str], set[tuple[str, str] | None]] = defaultdict(set)
    for x, y in compared or [(x, y) for x in ours for y in theirs]:
        _single(desc, x, y, 0, None, found if text(x) in own and text(y) in own else flat)
    for sub, hosts in flat.items():
        hosts = hosts - {None} if sub in found else hosts
        found[sub] |= hosts
    return dict(found)


def _single(desc: Descriptions, x: Tree, y: Tree, depth: int, host: tuple[str, str] | None,
            found: dict[tuple[str, str], set[tuple[str, str] | None]]) -> None:
    """Record the one part in which the nodes `x` and `y` differ, and the chain inside it."""
    if isinstance(x, str) or isinstance(y, str) or x[0] != y[0] or len(x[1]) != len(y[1]):
        return
    # Compared folded (⺈ against 刀 is no difference), recorded as written.
    folded_x, folded_y = desc.fold(x), desc.fold(y)
    differ = [i for i, (p, q) in enumerate(zip(folded_x[1], folded_y[1], strict=True)) if text(p) != text(q)]
    if len(differ) != 1:
        return
    p, q = x[1][differ[0]], y[1][differ[0]]
    if stroke(p) or stroke(q):
        return
    sub = ordered(text(p), text(q))
    found[sub].add(host)
    if depth + 1 < DEPTH:
        for inner_p in desc.expansions(p):
            for inner_q in desc.expansions(q):
                if desc.folded_text(inner_p) != desc.folded_text(inner_q):
                    _single(desc, inner_p, inner_q, depth + 1, sub, found)


def stroke(part: Tree) -> bool:
    """A single stroke: the CJK Strokes block and the ideographs that are one stroke (一, 丨, 乙, …)."""
    return isinstance(part, str) and (0x31C0 <= ord(part[0]) <= 0x31EF or part in STROKES)


def ordered(p: str, q: str) -> tuple[str, str]:
    """A substitution's own spelling: the shorter (then smaller) side first."""
    return (p, q) if (len(p), p) <= (len(q), q) else (q, p)


def adds(desc: Descriptions, a: str, b: str) -> bool:
    """Whether one side of the substitution `a`↔`b` is the other with a component added: 政 (⿰正攵)
    holds 正 whole, and 除 (⿰阝余) holds 余, where 口 and 厶 swap one shape for another."""
    for whole, inner in ((a, b), (b, a)):
        trees = desc.trees.get(whole, ()) if len(whole) == 1 else [desc.whole(parse(whole))]
        shape = desc.folded_text(desc.part(parse(inner)))
        if any(not isinstance(tree, str) and shape in {desc.folded_text(part) for part in tree[1]}
               for tree in trees):
            return True
    return False


@dataclass(frozen=True)
class Attested:
    """A substitution with the pairs that attest it, each with the sources that state the pair, and
    the contexts it was seen in: each pair where it is a part of the character itself, and each
    enclosing substitution where it is inside one (𧈧→虽 for 厶→口 in 強 and 强). The contexts are
    what `THRESHOLD` counts: pairs reaching a nested substitution through the same enclosing one are
    one position however many share it, so repetition across a family of pairs passes for nothing."""

    a: str
    b: str
    pairs: tuple[tuple[str, str, tuple[str, ...]], ...]
    contexts: tuple[str, ...]
    #: The contexts each pair of `pairs` was seen in, in the same order.
    contexts_of: tuple[tuple[str, ...], ...] = ()
    #: How many pairs of encoded characters it predicts and how many of those the graph gives as
    #: written variants, each attesting pair counted only where it is `held_out` (`agreeing`); zero
    #: until measured.
    predicted: int = 0
    agreed: int = 0

    @property
    def count(self) -> int:
        return len(self.contexts)

    def held_out(self, index: int) -> bool:
        """Whether the substitution passes `THRESHOLD` on its other pairs alone, so that it predicts
        the pair `pairs[index]` without having learned it from that pair."""
        rest = {context for at, contexts in enumerate(self.contexts_of) if at != index for context in contexts}
        return len(self.pairs) - 1 >= THRESHOLD and len(rest) >= THRESHOLD


def attest(desc: Descriptions, pairs: Iterable[tuple[str, str, Iterable[str]]]) -> dict[tuple[str, str], Attested]:
    """Every substitution the pairs attest, with the pairs behind it, each pair once."""
    found: dict[tuple[str, str], dict[tuple[str, str], set[str]]] = defaultdict(dict)
    contexts: dict[tuple[str, str], dict[tuple[str, str], set[str]]] = defaultdict(dict)
    for a, b, sources in pairs:
        sources = tuple(sources)  # read once per substitution below, so never a spent generator
        pair: tuple[str, str] = tuple(sorted((a, b), key=ord))
        for sub, hosts in substitutions(desc, a, b).items():
            found[sub].setdefault(pair, set()).update(sources)
            contexts[sub].setdefault(pair, set()).update("/".join(host or pair) for host in hosts)
    out = {}
    for sub, by in found.items():
        order = sorted(by, key=lambda pair: (ord(pair[0]), ord(pair[1])))
        out[sub] = Attested(
            *sub, tuple((p, q, tuple(sorted(by[p, q]))) for p, q in order),
            tuple(sorted(set().union(*contexts[sub].values()))),
            tuple(tuple(sorted(contexts[sub][pair])) for pair in order))
    return out


def stated(found: dict[tuple[str, str], Attested], a: str, b: str, source: str) -> Attested:
    """The substitution `a`↔`b` as a source states it outright: whatever pairs the graph attests it
    with, and the statement as a pair of its own (`a`:`b`, cited to `source`) in a context of its own.
    A stated substitution predicts whatever the threshold and the agreement say of it."""
    key = ordered(a, b)
    item = found.get(key) or Attested(*key, (), ())
    pair = tuple(sorted(key, key=ord))
    own = (*pair, (source,))
    pairs = sorted([*(p for p in item.pairs if p[:2] != pair), own], key=lambda p: (ord(p[0]), ord(p[1])))
    contexts_of = dict(zip((p[:2] for p in item.pairs), item.contexts_of or [()] * len(item.pairs), strict=True))
    context = "/".join(pair)
    contexts_of[pair] = (*contexts_of.get(pair, ()), context)
    return replace(item, pairs=tuple(pairs), contexts=tuple(sorted({*item.contexts, context})),
                   contexts_of=tuple(tuple(sorted(set(contexts_of.get(p[:2], ())))) for p in pairs))


def kept(found: dict[tuple[str, str], Attested]) -> list[Attested]:
    """What may predict: `THRESHOLD` distinct attesting pairs seen in `THRESHOLD` distinct contexts.

    Two pairs from one position (one enclosing substitution reached by both) are one claim twice,
    and one pair seen twice (at the top and inside the same decomposition) is one claim; either way
    nothing predicts. The pairs a substitution rests on are its own record either way.
    """
    return [item for item in found.values() if item.count >= THRESHOLD and len(item.pairs) >= THRESHOLD]


def predictions(desc: Descriptions, items: Iterable[Attested],
                chars: Iterable[str] | None = None) -> dict[tuple[str, str], set[tuple[str, str]]]:
    """The pairs of encoded characters each substitution of `items` predicts, from `chars` (every
    character of `desc.raw` when None, those with no description of their own included, so the bare
    pair of two components such as 火 and 灬 is a prediction too). Each pair is in code point order;
    the pairs that attest a substitution are among its predictions."""
    found: dict[tuple[str, str], set[tuple[str, str]]] = defaultdict(set)
    for form in derive(desc, equivalents(items), list(desc.raw) if chars is None else chars):
        if form.encoded:
            (was, became, _), = form.route
            found[ordered(was, became)].add(tuple(sorted((form.char, form.other), key=ord)))
    return found


def measured(item: Attested, predicted: dict[tuple[str, str], set[tuple[str, str]]],
             written: set[tuple[str, str]]) -> Attested:
    """`item` with how many pairs of encoded characters it predicts and how many of those the graph
    gives as written, each attesting pair counted only where it is held out (see `agreeing`)."""
    attesting = {(p, q): at for at, (p, q, _) in enumerate(item.pairs)}
    counted = {pair for pair in predicted.get((item.a, item.b), set())
               if pair not in attesting or item.held_out(attesting[pair])}
    return replace(item, predicted=len(counted), agreed=len(counted & written))


def agreeing(items: Iterable[Attested], predicted: dict[tuple[str, str], set[tuple[str, str]]],
             written: set[tuple[str, str]]) -> list[Attested]:
    """The substitutions at least `AGREEMENT` of whose predicted pairs are written pairs of the
    graph, each with those two counts. One that predicts mostly pairs no source states is a
    coincidence of meaning (口 and 氵, 日 and 木) rather than a way of writing.

    A pair that attests the substitution is always predicted and always written, so it is counted
    only when it is held out: when the other pairs alone pass `THRESHOLD`, and the substitution
    predicts it without having learned it from it (`Attested.held_out`). A substitution with two
    attesting pairs is measured on the other pairs it predicts, and one with three is measured on
    each of the three too. Scoring only the pairs beyond the attesting ones would leave almost
    nothing to agree with: a written pair that differs by the substitution is found by `attest` and
    is one of them."""
    out = []
    for item in items:
        item = measured(item, predicted, written)
        if item.predicted and item.agreed / item.predicted >= AGREEMENT:
            out.append(item)
    return out


#: How many substitutions one derived form may make at once, each in a part of its own: 疑 (⿰𠤕⿱龴疋,
#: 𠤕 ⿱匕矢) takes 矢→失 inside 𠤕 and 龴→コ beside it. Agreement is measured on one substitution.
STEPS = 2

#: A substitution as made: what the character has, what the form has instead, and how deep it was
#: made (1 for a part of the character's own sequence, 2 for a part of that part's sequence; 0 for the
#: whole character).
Step = tuple[str, str, int]


@dataclass(frozen=True)
class Derived:
    """A character and a form substitutions make of it: another character, or an unencoded form
    written as its sequence (`encoded` false). `route` holds the substitutions made, each in a part
    of its own, in the order of the parts; `sequence` is the form's description as derived."""

    char: str
    other: str
    encoded: bool
    route: tuple[Step, ...]
    sequence: str = ""


def equivalents(kept: Iterable[Attested]) -> dict[str, set[str]]:
    table: dict[str, set[str]] = defaultdict(set)
    for sub in kept:
        table[sub.a].add(sub.b)
        table[sub.b].add(sub.a)
    return table


def derive(desc: Descriptions, table: dict[str, set[str]], chars: Iterable[str] | None = None,
           steps: int = 1, made: Maker | None = None) -> Iterator[Derived]:
    """Each character of `chars` (all with a description when None) with up to `steps` substitutions
    of `table`, each made in a part of its own at any depth: in the character itself, in a part, or
    in a part of a part's own sequence. A form no character has comes only from the character's own
    descriptions (`Descriptions.own`), so a flattened reading never writes a second spelling of the
    same form, and a result of one character that no sequence spells is left out. A form reached by
    several routes comes once per route. A `made` kept across calls (with the same `desc`, `table`
    and `steps`) keeps what each part becomes, so a run over every character works each part once."""
    made = made or Maker(desc, table, steps)
    for char in desc.trees if chars is None else chars:
        seen: set[tuple[str, frozenset[Step]]] = set()
        for route, tree, own in made.whole(char):
            key = text(tree)
            found = desc.characters(tree)
            others = found - {char}
            if others:
                encoded = True
            elif found or not own or UNENCODED.search(key) or len(key) == 1:
                continue
            else:
                others, encoded = {key}, False
            for other in others:
                if (other, frozenset(route)) not in seen:
                    seen.add((other, frozenset(route)))
                    yield Derived(char, other, encoded, route, key)


class Maker:
    """The forms substitutions make of a character, with what a part becomes kept per part: 睘 turns
    into the same forms in 還, 環 and 寰."""

    def __init__(self, desc: Descriptions, table: dict[str, set[str]], steps: int = 1):
        self.desc, self.table, self.steps = desc, table, steps
        self.parts: dict[tuple[str, int, int], list[tuple[tuple[Step, ...], Tree]]] = {}

    def whole(self, char: str) -> Iterator[tuple[tuple[Step, ...], Tree, bool]]:
        """Each form of `char` with its route, and whether it was made from one of the character's
        own trees."""
        for became in sorted(self.table.get(char, ())):
            yield ((char, became, 0),), self.desc.whole(parse(became)), True
        own = {text(tree) for tree in self.desc.own.get(char, ())}
        for tree in self.desc.trees.get(char, ()):
            for route, replaced in self.replaced(tree, 0, self.steps):
                yield route, self.desc.whole(replaced), text(tree) in own

    def replaced(self, tree: Tree, depth: int, budget: int) -> list[tuple[tuple[Step, ...], Tree]]:
        """`tree` with one to `budget` substitutions made, each in a different part, at any depth."""
        if isinstance(tree, str):
            return []
        operator, parts = tree
        combos: list[tuple[tuple[Step, ...], tuple[Tree, ...]]] = [((), ())]
        for part in parts:
            options = self.part(part, depth + 1, budget)
            grown = []
            for route, done in combos:
                grown.append((route, (*done, part)))
                room = budget - len(route)
                grown += [((*route, *more), (*done, new)) for more, new in options if len(more) <= room]
            combos = grown
        return [(route, (operator, done)) for route, done in combos if route]

    def part(self, part: Tree, depth: int, budget: int) -> list[tuple[tuple[Step, ...], Tree]]:
        """A part replaced whole, or with substitutions inside it or inside its own sequence."""
        key = (text(part), depth, budget)
        if key in self.parts:
            return self.parts[key]
        found = [(((key[0], became, depth),), self.desc.part(parse(became)))
                 for became in sorted(self.table.get(key[0], ()))]
        if depth < DEPTH:
            for inner in self.desc.expansions(part):
                found += [(route, self.desc.part(new)) for route, new in self.replaced(inner, depth, budget)]
        self.parts[key] = found
        return found
