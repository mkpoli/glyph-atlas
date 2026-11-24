"""A character's 構形: its components and their arrangement, with no stroke design.

Two characters share a structure when they are built from the same components in the same
arrangement, however each component is drawn: 說 and 説 (兌 and 兑 are one component), 內 and 内,
煮 and 煑 (灬 is 火 at the bottom). 峰 (⿰山夆) and 峯 (⿱山夆) do not: the arrangement differs. In
漢字構形學 the first are 異寫字 and the second 異構字.

A structure is read from BabelStone's descriptions (`data/vocab/han-ids.tsv`):

- a part that spells a character is that character (⿰亻匕 is 化), and a ⿰/⿱ chain flattens
  (`refs.descriptions`);
- a radical form is the character it writes (`data/vocab/han-component-forms.tsv`): Unicode's
  equivalences anywhere (⺡ is 氵), CJKVI's radical variants in the position their row names (氵 at
  the left is 水). A CJKVI row with no position (甲 and 田, 才 and 手) indexes a radical and folds
  nothing;
- shapes the IRG lists as unifiable at level 1 are one component
  (`data/vocab/han-component-ucv.tsv`: 戶 户 戸), read as the first its entry names.

A character has one structure, that of its first description: Unicode unified its regional glyphs,
so the other descriptions (納 is ⿰糹内 in one region and ⿰糹內 in another) are spellings of the
same structure, and a written description that matches one names it (`for_ids`). A regional
difference Unicode unified cannot be told apart here. A character encoded twice joins the one whose
regional shape it is, where the 異体字 graph gives them as one character (𦤀 ⿱自大 has 臭's
⿱自犬). A character whose descriptions name no composition (七 is 〾⿻一乚, 𠇇 is ⿰亻？) is one component, itself; a written ⿰亻？ is 亻 beside a
part no character names, and stays apart from 𠇇.

The identity is the canonical text, the scheme and the policy, never the description as written. A
policy change makes new ids, so an id issued today keeps its meaning. The decision is
`docs/decisions/0012-structure-layer.md`.
"""

from __future__ import annotations

import hashlib
import itertools
import re
import unicodedata
from dataclasses import dataclass
from functools import cache
from typing import Any

from . import han_component_variants, han_components, refs
from .representation import Representation, described, is_sequence

#: The IDS source the derivation reads, its file date, and the canonicalization policy.
SCHEME = "babelstone-ids"
VERSION = "2025-06-27"
POLICY = "structure-v1"
#: The tables the policy reads besides the descriptions, with the digest of each. A table that changes
#: can change a label, so a new digest here goes with a new POLICY.
TABLES = {"han-component-forms.tsv": "5e461192b81ff385", "han-component-ucv.tsv": "627702f7732fe5e6"}

Tree = han_component_variants.Tree
text = han_component_variants.text
#: The positions a component form stands in, from `han-component-forms.tsv`'s detail.
POSITIONS = frozenset({"left", "right", "top", "bottom", "middle", "partial"})
#: The operators whose parts have a position, and each part's position.
SLOTS = {"⿰": ("left", "right"), "⿱": ("top", "bottom"),
         "⿲": ("left", "middle", "right"), "⿳": ("top", "middle", "bottom")}
#: The relation of the 異体字 graph that makes two characters one: the same character encoded twice
#: (Unihan's kZVariant, CJKVI's duplicates). A stated equivalence is not enough: yitizi's joins
#: 新字体 to 旧字体 (㠀 and 島). CJKVI's non-cognate gives two characters that only look alike, and
#: keeps them apart whatever else is stated (杮 and 柿).
SAME = frozenset({"z"})
#: Unicode's Seal block, and the source of the 異体字 graph that states each seal character's
#: equivalent.
SEAL = range(0x3D000, 0x3FC40)
SEAL_SOURCE = "unicode-ucd"
#: The Han values whose seal form is known to be built otherwise: 書 is 聿 over 者 in seal (說文繫傳 卷六 聿部,
#: 從聿者聲), and the figures of Sealie 1.3.1 draw 邃 with 穴 over the whole and
#: 𨘈 with 虍 over the rest.
SEAL_UNLIKE = frozenset("書邃𨘈")
APART = "non-cognate"
#: The level of the IRG's unifiable list this layer reads. A level-2 entry lists characters its
#: variation tells apart (壬 and 王: 任 and 仼).
LEVEL = "1"
#: The IRG's marks for any component (⿱⬚月 is 月 at the bottom; Ｘ and Ｙ), and the private-use shapes
#: its list draws with a figure; a shape holding one is not read.
OPEN = re.compile("[⬚Ａ-Ｚ\ue000-\uf8ff]")
#: Radical-variant rows this layer does not read. CJKVI gives 䒑 as 艸 at the top; at the top of 前, 羊
#: and 益 it is 丷 over 一 (IRG entry 23), so the row would make 前 a grass character.
UNREAD = frozenset({"䒑"})
#: How many times `canonical` re-reads a tree before giving up; a reading settles within four.
PASSES = 8
#: How many ways of writing a part's components `_spelled` tries before leaving the part spelled out.
SPELLINGS = 64
#: The variation selectors a written description may carry after a component; a structure has no
#: stroke design, so they are dropped.
SELECTORS = re.compile("[\ufe00-\ufe0f\U000e0100-\U000e01ef]")


@dataclass(frozen=True)
class Structure:
    """One canonical component tree: its id, its label, and the tree itself."""

    id: str
    label: str
    tree: Tree
    scheme: str = SCHEME
    version: str = VERSION
    policy: str = POLICY


@cache
def _folds() -> dict[tuple[str, str | None], str]:
    """The character a radical form writes, by (form, position); None is any position."""
    folds: dict[tuple[str, str | None], str] = {}
    for form, char, source, detail in han_components.read_rows("han-component-forms.tsv"):
        if source == "unicode-ucd":
            folds[(form, None)] = char
        elif form in UNREAD:
            continue
        elif source == "cjkvi-variants" and (position := detail.rsplit(" ", 1)[-1]) in POSITIONS:
            folds[(form, position)] = char
    return folds


def _radical(component: str, position: str | None) -> str:
    """The character a component writes at `position` (⺡ is 氵, and 氵 at the left is 水)."""
    seen: set[str] = set()
    while component not in seen:
        seen.add(component)
        written = _folds().get((component, position), _folds().get((component, None)))
        if written is None:
            break
        component = written
    return component


def _plain(tree: Tree) -> Tree:
    """`tree` as the sources write it: parts named and chains flattened, radical forms read."""
    def leaves(node: Tree, position: str | None = None) -> Tree:
        if isinstance(node, str):
            return _radical(node, position)
        operator, parts = node
        slots = SLOTS.get(operator, ())
        return operator, tuple(leaves(part, slots[index] if index < len(slots) else None)
                               for index, part in enumerate(parts))
    return leaves(refs.descriptions().whole(tree))


def _variants(a: str, b: str) -> bool:
    """Whether the 異体字 graph gives two characters as one step apart."""
    return any(other == b for other, _ in refs.variants(a))


@cache
def _variations() -> tuple[dict[str, Tree], frozenset[frozenset[str]]]:
    """What the IRG's list says at `LEVEL`: each unified shape, by its plain text, as the shape its
    class names first; and the pairs of characters it lists as encoded apart all the same.

    The list unifies shapes where they vary, and two of its shapes can be different characters
    elsewhere (主 and 龶 at the top of 青; 住 is not ⿰亻龶). Two characters of an entry are therefore
    one component only when the 異体字 graph also gives them as variants (戶 户 戸, 內 内); a
    description joins the entry's other shapes as listed. Entries that share a shape are one class,
    read as the first shape of the entry that first joined two of its shapes.
    """
    heads: dict[str, str] = {}
    trees: dict[str, Tree] = {}
    formed: dict[str, int] = {}
    apart: set[frozenset[str]] = set()

    def head(shape: str) -> str:
        while heads[shape] != shape:
            shape = heads[shape]
        return shape

    rows = han_components.read_rows("han-component-ucv.tsv")
    for number, (_, listed, level, components, *rest) in enumerate(rows):
        if listed != "unifiable" or level != LEVEL:
            continue
        for group in (rest[0].split(" ") if rest and rest[0] else ()):
            apart.update(frozenset({a, b}) for a in group for b in group if a != b)
        shapes: list[str] = []
        for component in components.split(" "):
            if OPEN.search(component) or han_component_variants.SKIPPED & set(component):
                continue
            tree = _plain(han_component_variants.parse(component))
            trees.setdefault(text(tree), tree)
            heads.setdefault(text(tree), text(tree))
            shapes.append(text(tree))
        for index, shape in enumerate(shapes):
            for earlier in shapes[:index]:
                if isinstance(trees[shape], str) and isinstance(trees[earlier], str) \
                        and not _variants(shape, earlier):
                    continue
                first, second = head(earlier), head(shape)
                if first == second:
                    continue
                if formed.get(second, number) < formed.get(first, number):
                    first, second = second, first
                heads[second] = first
                formed.setdefault(first, number)
    return {shape: trees[head(shape)] for shape in heads if head(shape) != shape}, frozenset(apart)


def _unified() -> dict[str, Tree]:
    return _variations()[0]


def _components(tree: Tree) -> Tree:
    """`tree` with every part the IRG unifies read as its class's first shape, innermost first."""
    if not isinstance(tree, str):
        tree = tree[0], tuple(_components(part) for part in tree[1])
    return _unified().get(text(tree), tree)


@cache
def _writings() -> dict[tuple[str, str | None], tuple[str, ...]]:
    """The shapes read as a component at a position, the other way round from `_radical` and
    `_unified`: 人 at the left may have been written 亻, and 兌 anywhere 兑."""
    found: dict[tuple[str, str | None], list[str]] = {}
    for (form, position), char in _folds().items():
        if position is not None:
            found.setdefault((char, position), []).append(form)
    for shape, head in _unified().items():
        if len(shape) == 1 and isinstance(head, str):
            found.setdefault((head, None), []).append(shape)
    return {key: tuple(forms) for key, forms in found.items()}


def _spelled(tree: Tree, top: bool = True) -> Tree:
    """`tree` with every part that spells a character once its components are written as the
    sources write them named as that character: ⿰人木 inside a description is 休, which BabelStone
    spells ⿰亻木. The description itself is never renamed."""
    if isinstance(tree, str):
        return tree
    operator, parts = tree
    parts = tuple(_spelled(part, False) for part in parts)
    if top:
        return operator, parts
    slots = SLOTS.get(operator, ())
    choices = [(part, *_writings().get((part, slots[index] if index < len(slots) else None), ()),
                *_writings().get((part, None), ())) if isinstance(part, str) else (part,)
               for index, part in enumerate(parts)]
    named = refs.descriptions().named
    for written in itertools.islice(itertools.product(*choices), 1, SPELLINGS):
        if (char := named.get(refs.descriptions().folded_text((operator, written)))) is not None:
            return char
    return operator, parts


def canonical(tree: Tree) -> Tree:
    """The structure a parsed description writes.

    Reading a component can make a part spell another character, which the next pass names; the
    reading repeats until its text is stable.
    """
    current = tree
    for _ in range(PASSES):
        following = _components(_plain(_spelled(current)))
        if text(following) == text(current):
            return following
        current = following
    raise ValueError(f"the reading of {text(tree)} does not settle")


def _identity(label: str) -> str:
    """The id of a structure: the canonical text, the scheme and the policy, hashed.

    The source's file date is provenance on the row: a new file that leaves a tree unchanged keeps
    its id.
    """
    material = f"structure\n{label}\n{SCHEME}\n{POLICY}"
    return "st:" + hashlib.sha256(material.encode()).hexdigest()[:32]


def from_tree(tree: Tree) -> Structure:
    """The structure a parsed description writes, as written: no character's spelling is looked up."""
    canonical_tree = canonical(tree)
    label = text(canonical_tree)
    return Structure(id=_identity(label), label=label, tree=canonical_tree)


def _descriptions(char: str) -> list[Tree]:
    """The character's descriptions that name a composition, those without an unencoded part first.

    An approximate, subtracting or unrepresentable description names none.
    """
    usable = [sequence for sequence in han_components._sequences().get(char, ())
              if not han_component_variants.SKIPPED & set(sequence)]
    usable.sort(key=lambda sequence: bool(han_component_variants.UNENCODED.search(sequence)))
    return [han_component_variants.parse(sequence) for sequence in usable]


def _whole(char: str) -> str:
    """The character whose structure `char` has: the one the IRG unifies it with (内 has 內's),
    unless the list gives the two as encoded apart (丷 and 八), or itself."""
    head = _unified().get(char, char)
    return head if isinstance(head, str) and frozenset({char, head}) not in _variations()[1] else char


def _own(char: str) -> Structure | None:
    """The structure a character's own descriptions give: its first description's, or that of the
    character the IRG unifies it with. A compatibility ideograph has its unified ideograph's (說
    U+F96F has 說's). A character whose descriptions name no composition is one component, itself
    (七, 永, 乌); one the source does not describe has none."""
    char = unicodedata.normalize("NFC", char)
    candidates = tuple(dict.fromkeys((_whole(char), char)))
    for candidate in candidates:
        if found := _descriptions(candidate):
            return from_tree(found[0])
    return next((from_tree(candidate) for candidate in candidates if candidate in han_components._sequences()), None)


@cache
def seal_equivalents() -> dict[str, str]:
    """The Han character each small seal character takes its structure from: its kSEAL_MCJK in
    Unicode's SealSources.txt (the `small-seal` rows of the 異体字 graph from unicode-ucd), where no
    other seal character has the same one.

    The file calls the value the modern CJK equivalent and says nothing of structure. It can follow
    the seal form's composition (𽀅 has 叓, where 吏 is written today) and need not, so the structure
    is a proposal: the site marks it, and a reader's filing overrides it. SEAL_UNLIKE are the values
    known to differ. Where several seal characters share a value (折, 殺, 民), it cannot say which is
    built as it, so none takes it."""
    seals: dict[str, set[str]] = {}
    for row in refs.variant_edges():
        if row["relation"] == "small-seal" and row["source"] == SEAL_SOURCE:
            seal, han = (row["a"], row["b"]) if ord(row["a"][0]) in SEAL else (row["b"], row["a"])
            seals.setdefault(han, set()).add(seal)
    return {seal: han for han, found in seals.items() if len(found) == 1 and han not in SEAL_UNLIKE for seal in found}


def head(label: str) -> tuple[str, str] | None:
    """The id and label of the structure another layout of the same parts stands under: 辵 beside X
    (⿰辵禺, the seal 𽓻) or whole under it (⿺辵土, 𨑡) under ⿺辶X, 辶 being the form 辵 takes as 之繞.
    None for any other label. The Worker's `headOf` answers the same."""
    if label[:2] in {"⿰辵", "⿺辵"}:
        whole = "⿺辶" + label[2:]
    elif label[:2] == "⿲辵":
        whole = "⿺辶⿰" + label[2:]
    else:
        return None
    return _identity(whole), whole


def whole(structure: Structure) -> Structure | None:
    """The structure this one is another layout of (`head`), or None."""
    found = head(structure.label)
    return Structure(id=found[0], label=found[1], tree=han_component_variants.parse(found[1])) if found else None


def _seal(structure: Structure | None) -> Structure | None:
    """The structure a seal character is proposed from that of its Han equivalent: seal script has no
    之繞, and writes 辵 whole at the left of ⿺辶X."""
    if structure and structure.tree[0] == "⿺" and structure.tree[1][0] in {"辶", "辵"}:
        return from_tree(("⿰", ("辵", structure.tree[1][1])))
    return structure


def for_character(char: str) -> Structure | None:
    """The structure of a character: its own (`_own`), or that of the character it joins as one
    character encoded twice (`_joined`: 𦤀 has 臭's ⿱自犬). A small seal character is proposed that of
    its Han equivalent (`seal_equivalents`), with 辵 beside its phonetic where that has 之繞."""
    char = unicodedata.normalize("NFC", char)
    if han := seal_equivalents().get(char):
        return _seal(for_character(han))
    head = _joined().get(char) or _joined().get(_whole(char))
    return _own(head) if head else _own(char)


@cache
def _labels() -> tuple[dict[str, tuple[str, ...]], dict[str, str]]:
    """The characters whose own structure each canonical description is, lowest code point first,
    and the character each other canonical description spells: a first description before another,
    then the lowest code point. A character whose reading does not settle names nothing here."""
    owners: dict[str, list[str]] = {}
    first: dict[str, str] = {}
    other: dict[str, str] = {}
    for char in sorted(han_components._sequences(), key=ord):
        try:
            texts = [text(canonical(tree)) for tree in _descriptions(char)]
        except ValueError:
            continue
        if texts and _whole(char) == char:
            owners.setdefault(texts[0], []).append(char)
        for index, spelled in enumerate(texts):
            (other if index else first).setdefault(spelled, char)
    return ({label: tuple(chars) for label, chars in owners.items()},
            {spelled: char for spelled, char in (other | first).items() if spelled not in owners})


def _same(a: str, b: str) -> bool:
    """Whether the 異体字 graph gives `a` and `b` as one character (SAME) and never as two that only
    look alike (APART)."""
    relations = {row["relation"] for row in refs.variant_edges_of(a) if {row["a"], row["b"]} == {a, b}}
    return bool(relations & SAME) and APART not in relations


@cache
def _joined() -> dict[str, str]:
    """Characters encoded apart for one character, each by the character it joins. B joins A when B's
    own structure is a regional shape of A and `_same` holds: 𦤀 (⿱自大) is the J shape of 臭 and
    joins it, as 𠾖 (⿳吅大吅) joins 器. 柿 (⿰木市) is the K shape of 杮 and stays apart: the graph
    gives them as two characters that look alike. A group has the structure of its lowest code
    point among the characters whose shape another joins. A reading that does not settle joins
    nothing."""
    owners = _labels()[0]
    parent: dict[str, str] = {}
    shaped: set[str] = set()

    def find(char: str) -> str:
        while parent.setdefault(char, char) != char:
            char = parent[char] = parent[parent[char]]
        return char

    for char, sequences in han_components._sequences().items():
        try:
            own = _own(char) if len(sequences) > 1 else None
        except ValueError:
            continue
        for shape in refs.regional_shapes(char) if own else ():
            if set(shape["text"]) & han_component_variants.SKIPPED:
                continue
            try:
                label = text(canonical(han_component_variants.parse(SELECTORS.sub("", shape["text"]))))
            except ValueError:
                continue
            if label == own.label:
                continue
            for other in owners.get(label, ()):
                if other != char and _same(char, other):
                    shaped.add(char)
                    parent[find(other)] = find(char)
    groups: dict[str, list[str]] = {}
    for char in parent:
        groups.setdefault(find(char), []).append(char)
    return {char: min([c for c in group if c in shaped] or group, key=ord)
            for group in groups.values() if len(group) > 1 for char in group}


def encoded_shapes(char: str, shapes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """`shapes`, `char`'s regional shapes (`refs.regional_shapes`), each with `chars`, the characters
    that encode it again, where some do: those that join `char` (`_joined`) and whose own structure the
    shape writes. 臭's J shape ⿱自大 is 𦤀."""
    char = unicodedata.normalize("NFC", char)
    head = _joined().get(char)
    twins = [other for other, joined in _joined().items() if head and joined == head and other != char]
    if not twins:
        return shapes
    labels = {twin: own.label for twin in twins if (own := _own(twin))}
    found = []
    for shape in shapes:
        try:
            label = text(canonical(han_component_variants.parse(SELECTORS.sub("", shape["text"]))))
        except ValueError:
            label = None
        chars = [twin for twin, own in labels.items() if own == label]
        found.append({**shape, "chars": chars} if chars else shape)
    return found


def for_ids(sequence: str) -> Structure:
    """The structure a written description names, or ValueError when it names none.

    A description that is a character's own structure names it, or the structure that character
    joins (⿱自大 is 𦤀's, which has 臭's ⿱自犬); another description BabelStone
    gives for a character names that character's structure (the lowest code point's, where several
    characters hold it). A written description may keep `？` for a part no character names; an
    approximate or subtracting one names no composition.
    """
    if not described(sequence):
        raise ValueError("Not a well-formed ideographic description sequence.")
    if set(sequence) & (han_component_variants.SKIPPED - {"？"}):
        raise ValueError("An approximate or subtracting description names no structure.")
    written = from_tree(han_component_variants.parse(SELECTORS.sub("", sequence)))
    owners, spellings = _labels()
    if (owner := next(iter(owners.get(written.label, ())), None)) and owner in _joined():
        return for_character(owner) or written
    spelled = spellings.get(written.label)
    return for_character(spelled) or written if spelled else written


def for_name(name: str) -> Structure | None:
    """The structure of what a text names: a description's, or a character's with any variation
    selector dropped. A description that names none has none."""
    if is_sequence(name):
        try:
            return for_ids(unicodedata.normalize("NFC", name))
        except ValueError:
            return None
    return for_character(name[:1]) if name else None


def for_shape(name: str, of: str) -> Structure | None:
    """The structure of `name` as a regional shape of `of`: `of`'s own, since Unicode unified its
    regional glyphs (臭's J shape ⿱自大 is 臭's ⿱自犬, though written alone it is 𦤀's). ValueError
    when `name` is no regional shape of `of`."""
    if name not in {shape["text"] for shape in refs.regional_shapes(of)}:
        raise ValueError(f"{name} is no shape of {of}.")
    return for_name(of)


def for_representation(named: Representation) -> Structure | None:
    """The structure a representation names: a description's, a character's, an IVS's base's.

    An approximate or subtracting description, an MJ id, a GlyphWiki name, a private-use code point
    and an empty value name none.
    """
    if named.scheme == "ids":
        try:
            return for_ids(named.value)
        except ValueError:
            return None
    if named.scheme == "unicode":
        return for_character(named.value)
    if named.scheme == "ivs" and named.value:
        return for_character(named.value[0])
    return None
