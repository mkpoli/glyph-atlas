"""A form's 構形: the canonical component tree its IDS describes.

A structure is the components a character is built from and how they are arranged, with no stroke
design. Two forms share a structure when their IDS read to the same tree: 峰 (⿰山夆) and 峯
(⿱山夆) do not, while 艹 and 十十 do, because BabelStone writes the 艹 component itself as ⿰十十
(`data/vocab/han-ids.tsv`, U+8279; the canonical component is 艸). The design difference stays in
the form layer; the composition is the structure.

The identity is the canonical tree and the policy version, never the IDS text. `refs.descriptions`
reads a named part as the character it spells (⿰亻匕 is 化, ⿰十十 is 艹) and flattens a ⿰/⿱ chain.
`canonical` then reads every leaf through `data/vocab/han-component-forms.tsv`: Unicode's radical
equivalences anywhere (⺡ is 氵), and CJKVI's radical variants only in the position their row names
(氵 at the left is 水, 艹 at the top is 艸). A CJKVI row with no position (甲 and 田, 才 and 手) is an
indexing equivalence, not a composition, and stays distinct. A policy change makes new ids, so an id
issued today keeps its meaning. The ledger's `has_structure` claim, which lets a reviewer override
the derivation, is defined in `docs/decisions/0012-structure-layer.md`.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import cache

from . import han_component_variants, han_components, refs
from .representation import Representation, described

#: The IDS source the derivation reads, and the canonicalization policy's version.
SCHEME = "babelstone-ids"
VERSION = "2025-06-27"
POLICY = "structure-v1"

Tree = han_component_variants.Tree
#: The positions a component form stands in, from `han-component-forms.tsv`'s detail. A CJKVI form
#: without one indexes a radical (甲 and 田) and does not name the same composition.
POSITIONS = frozenset({"left", "right", "top", "bottom", "middle", "partial"})
#: The operators whose parts have a position, and each part's position.
SLOTS = {"⿰": ("left", "right"), "⿱": ("top", "bottom"),
         "⿲": ("left", "middle", "right"), "⿳": ("top", "middle", "bottom")}
#: How many times `canonical` re-reads a tree; folding a leaf can make a node spell a character the
#: next pass names, and the reading settles within three.
PASSES = 4


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
    """The folds a structure applies to a leaf, by (component, position).

    Unicode's `EquivalentUnifiedIdeograph` rows hold anywhere. CJKVI's radical variants hold only in
    the position their detail names; a CJKVI row without one (甲 and 田, 才 and 手) indexes a radical,
    and folding it would merge different compositions.
    """
    folds: dict[tuple[str, str | None], str] = {}
    for form, char, source, detail in han_components.read_rows("han-component-forms.tsv"):
        if source == "unicode-ucd":
            folds[(form, None)] = char
        elif source == "cjkvi-variants" and (position := detail.rsplit(" ", 1)[-1]) in POSITIONS:
            folds[(form, position)] = char
    return folds


def _standard(component: str, position: str | None) -> str:
    """The character a component stands for at `position` (⺡ is 氵, and 氵 at the left is 水)."""
    seen: set[str] = set()
    while component not in seen:
        seen.add(component)
        written = _folds().get((component, position))
        if written is None:
            written = _folds().get((component, None))
        if written is None:
            break
        component = written
    return component


def _leaves(tree: Tree, position: str | None = None) -> Tree:
    """`tree` with every leaf read as its standard component at its position, operators kept."""
    if isinstance(tree, str):
        return _standard(tree, position)
    operator, parts = tree
    slots = SLOTS.get(operator)
    return operator, tuple(_leaves(part, slots[index] if slots and index < len(slots) else None)
                           for index, part in enumerate(parts))


def canonical(tree: Tree) -> Tree:
    """The structure a parsed IDS writes: named parts read, leaves standardized, chains flattened.

    `refs.descriptions().whole` names a part as the character it spells, and folding a leaf can make a
    node spell another character the next pass names; the reading repeats until its text is stable.
    """
    current = tree
    for _ in range(PASSES):
        following = _leaves(refs.descriptions().whole(current))
        if han_component_variants.text(following) == han_component_variants.text(current):
            return following
        current = following
    return current


def _identity(label: str) -> str:
    """The id of a structure: the canonical text, the scheme and the policy, hashed.

    The source version is provenance on the row, not identity: a source bump that leaves a tree
    unchanged keeps its id.
    """
    material = f"structure\n{label}\n{SCHEME}\n{POLICY}"
    return "st:" + hashlib.sha256(material.encode()).hexdigest()[:32]


def from_tree(tree: Tree) -> Structure:
    """The structure a parsed IDS tree writes."""
    canonical_tree = canonical(tree)
    text = han_component_variants.text(canonical_tree)
    return Structure(id=_identity(text), label=text, tree=canonical_tree)


def for_character(char: str) -> list[Structure]:
    """Every distinct structure the character's BabelStone descriptions write, in source order.

    A character with no usable description has none. Sequences with an unencoded, approximate,
    subtracting or unrepresentable part are left out: they do not name a composition this layer can
    compare. A character with regional descriptions gives one structure for each distinct canonical
    description; a named component and its spelled-out form stay distinct unless the namer reads them
    alike.
    """
    found: dict[str, Structure] = {}
    for sequence in refs.ids_sequences(char):
        if han_component_variants.SKIPPED & set(sequence):
            continue
        item = from_tree(han_component_variants.parse(sequence))
        found.setdefault(item.id, item)
    return list(found.values())


def for_ids(sequence: str) -> Structure:
    """The structure a written IDS names, or ValueError when it names none.

    A written IDS may keep `？` for a part no character names; an approximate or subtracting
    description names no composition.
    """
    if not described(sequence):
        raise ValueError("Not a well-formed ideographic description sequence.")
    if set(sequence) & (han_component_variants.SKIPPED - {"？"}):
        raise ValueError("An approximate or subtracting description names no structure.")
    return from_tree(han_component_variants.parse(sequence))


def for_representation(named: Representation) -> list[Structure]:
    """The structures a representation names: an IDS one, a character's descriptions, an IVS's base.

    A representation that names no structure (an approximate or subtracting IDS, an MJ id, a
    GlyphWiki name or a private-use code point) returns none; so does an empty value.
    """
    if named.scheme == "ids":
        try:
            return [for_ids(named.value)]
        except ValueError:
            return []
    if named.scheme == "unicode":
        return for_character(named.value)
    if named.scheme == "ivs":
        return for_character(named.value[0]) if named.value else []
    return []
