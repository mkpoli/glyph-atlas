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
  (`data/vocab/han-component-ucv.tsv`: 戶 户 戸), read as the first the entry names.

A character has one structure, that of its first description: Unicode unified its regional glyphs,
so the other descriptions (納 is ⿰糹内 in one region and ⿰糹內 in another) are spellings of the
same structure, and a written description that matches one names it (`for_ids`). A regional
difference Unicode unified cannot be told apart here.

The identity is the canonical text, the scheme and the policy, never the description as written. A
policy change makes new ids, so an id issued today keeps its meaning. The decision is
`docs/decisions/0012-structure-layer.md`.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from functools import cache

from . import han_component_variants, han_components, refs
from .representation import Representation, described

#: The IDS source the derivation reads, its file date, and the canonicalization policy.
SCHEME = "babelstone-ids"
VERSION = "2025-06-27"
POLICY = "structure-v1"

Tree = han_component_variants.Tree
text = han_component_variants.text
#: The positions a component form stands in, from `han-component-forms.tsv`'s detail.
POSITIONS = frozenset({"left", "right", "top", "bottom", "middle", "partial"})
#: The operators whose parts have a position, and each part's position.
SLOTS = {"⿰": ("left", "right"), "⿱": ("top", "bottom"),
         "⿲": ("left", "middle", "right"), "⿳": ("top", "middle", "bottom")}
#: The level of the IRG's unifiable list this layer reads. A level-2 entry lists characters its
#: variation tells apart (壬 and 王: 任 and 仼).
LEVEL = "1"
#: The IRG's mark for any component (⿱⬚月 is 月 at the bottom), and the private-use shapes its list
#: draws with a figure; an entry holding either is not read.
OPEN = re.compile("[⬚-]")
#: How many times `canonical` re-reads a tree before giving up; a reading settles within four.
PASSES = 8


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
def _unified() -> dict[str, Tree]:
    """Each shape the IRG unifies at `LEVEL`, by its plain text, as the first shape of its class.

    The list unifies shapes where they vary, and two of its shapes can be different characters
    elsewhere (主 and 龶 at the top of 青; 住 is not ⿰亻龶). Two characters of an entry are therefore
    one component only when the 異体字 graph also gives them as variants (戶 户 戸, 內 内); a
    description joins the entry's first shape as listed. Entries that share a shape are one class,
    read as the earliest entry's first shape.
    """
    heads: dict[str, str] = {}
    trees: dict[str, Tree] = {}

    def head(shape: str) -> str:
        while heads.setdefault(shape, shape) != shape:
            shape = heads[shape]
        return shape

    for _, listed, level, components in han_components.read_rows("han-component-ucv.tsv"):
        if listed != "unifiable" or level != LEVEL:
            continue
        shapes: list[str] = []
        for component in components.split(" "):
            if OPEN.search(component) or han_component_variants.SKIPPED & set(component):
                continue
            tree = _plain(han_component_variants.parse(component))
            trees.setdefault(text(tree), tree)
            shapes.append(text(tree))
        for index, shape in enumerate(shapes):
            for earlier in shapes[:index]:
                if isinstance(trees[shape], str) and isinstance(trees[earlier], str) \
                        and not _variants(shape, earlier):
                    continue
                first, second = head(earlier), head(shape)
                if first != second:
                    heads[second] = first
    return {shape: trees[head(shape)] for shape in heads if head(shape) != shape}


def _components(tree: Tree) -> Tree:
    """`tree` with every part the IRG unifies read as its class's first shape, innermost first."""
    if not isinstance(tree, str):
        tree = tree[0], tuple(_components(part) for part in tree[1])
    return _unified().get(text(tree), tree)


def canonical(tree: Tree) -> Tree:
    """The structure a parsed description writes.

    Reading a component can make a part spell another character, which the next pass names; the
    reading repeats until its text is stable.
    """
    current = tree
    for _ in range(PASSES):
        following = _components(_plain(current))
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


def for_character(char: str) -> Structure | None:
    """The structure of a character: its first description's, or that of the shape the IRG unifies
    it with (内 has 內's). A character with no description that names a composition has none."""
    head = _unified().get(char, char)
    for candidate in (head, char) if isinstance(head, str) else (char,):
        if found := _descriptions(candidate):
            return from_tree(found[0])
    return None


@cache
def _spellings() -> dict[str, str]:
    """The character each canonical description spells: a first description before another, then the
    lowest code point."""
    first: dict[str, str] = {}
    other: dict[str, str] = {}
    for char in sorted(han_components._sequences(), key=ord):
        for index, tree in enumerate(_descriptions(char)):
            (other if index else first).setdefault(text(canonical(tree)), char)
    return other | first


def for_ids(sequence: str) -> Structure:
    """The structure a written description names, or ValueError when it names none.

    A description BabelStone gives for a character names that character's structure. A written
    description may keep `？` for a part no character names; an approximate or subtracting one names
    no composition.
    """
    if not described(sequence):
        raise ValueError("Not a well-formed ideographic description sequence.")
    if set(sequence) & (han_component_variants.SKIPPED - {"？"}):
        raise ValueError("An approximate or subtracting description names no structure.")
    written = from_tree(han_component_variants.parse(sequence))
    spelled = _spellings().get(written.label)
    return for_character(spelled) or written if spelled else written


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
