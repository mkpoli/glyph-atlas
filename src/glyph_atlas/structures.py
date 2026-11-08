"""A form's 構形: the canonical component tree its IDS describes.

A structure is the components a character is built from and how they are arranged, with no stroke
design. Two forms share a structure when their IDS read to the same tree: 峰 (⿰山夆) and 峯
(⿱山夆) do not, while 艹 and 十十 do, because BabelStone writes the 艹 component itself as ⿰十十
(the canonical component is 艸)
(`data/vocab/han-ids.tsv`, U+8279). The design difference stays in the form layer; the composition
is the structure.

The identity is the canonical tree and the policy version, never the IDS text. `refs.descriptions`
reads a named part as the character it spells (⿰亻匕 is 化, ⿰十十 is 艹) and flattens a ⿰/⿱ chain;
`canonical` then reads every leaf through `data/vocab/han-component-forms.tsv` (⺡ is 氵 is 水). A
policy change makes new ids, so an id issued today keeps its meaning. The ledger's `has_structure`
claim, which lets a reviewer override the derivation, is defined in
`docs/decisions/0012-structure-layer.md`.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from . import han_component_variants, han_components, refs
from .representation import Representation, described

#: The IDS source the derivation reads, and the canonicalization policy's version.
SCHEME = "babelstone-ids"
VERSION = "2025-06-27"
POLICY = "structure-v1"

Tree = han_component_variants.Tree


@dataclass(frozen=True)
class Structure:
    """One canonical component tree: its id, its label, and the tree itself."""

    id: str
    label: str
    tree: Tree
    scheme: str = SCHEME
    version: str = VERSION
    policy: str = POLICY


def _standard(component: str) -> str:
    """The character a component stands for, through the forms table (⺡ is 氵 is 水)."""
    seen: set[str] = set()
    while component not in seen:
        seen.add(component)
        written = han_components.forms().get(component)
        if written is None:
            break
        component = written
    return component


def _leaves(tree: Tree) -> Tree:
    """`tree` with every leaf read as its standard component, the operators and order kept."""
    if isinstance(tree, str):
        return _standard(tree)
    operator, parts = tree
    return operator, tuple(_leaves(part) for part in parts)


def canonical(tree: Tree) -> Tree:
    """The structure a parsed IDS writes: named parts read, leaves standardized, chains flattened."""
    return _leaves(refs.descriptions().whole(tree))


def label(tree: Tree) -> str:
    """The canonical tree as its IDS text, which is also how a structure is shown."""
    return han_component_variants.text(canonical(tree))


def _identity(label: str) -> str:
    """The id of a structure: the canonical text and the policy that read it, hashed."""
    material = f"structure\n{label}\n{SCHEME}\n{VERSION}\n{POLICY}"
    return "st:" + hashlib.sha256(material.encode()).hexdigest()[:32]


def from_tree(tree: Tree) -> Structure:
    """The structure a parsed IDS tree writes."""
    canonical_tree = canonical(tree)
    text = han_component_variants.text(canonical_tree)
    return Structure(id=_identity(text), label=text, tree=canonical_tree)


def for_character(char: str) -> list[Structure]:
    """Every distinct structure the character's BabelStone descriptions write, in source order.

    A character with no usable description has none. Sequences with an unencoded, approximate or
    subtracting part are left out: they name no composition. A character with regional descriptions
    gives one structure for each analysis, not one for each region.
    """
    found: dict[str, Structure] = {}
    for sequence in refs.ids_sequences(char):
        if han_component_variants.SKIPPED & set(sequence):
            continue
        item = from_tree(han_component_variants.parse(sequence))
        found.setdefault(item.id, item)
    return list(found.values())


def for_ids(sequence: str) -> Structure:
    """The structure a written IDS names, or ValueError when it names none."""
    if not described(sequence):
        raise ValueError("Not a well-formed ideographic description sequence.")
    if set(sequence) & (han_component_variants.SKIPPED - {"？"}):
        raise ValueError("An approximate or subtracting description names no structure.")
    return from_tree(han_component_variants.parse(sequence))


def for_representation(named: Representation) -> list[Structure]:
    """The structures a representation names: an IDS one, a character's descriptions, an IVS's base.

    An MJ id, a GlyphWiki name or a private-use code point names no structure here; a form anchored
    to one has none until a source or a reviewer states it.
    """
    if named.scheme == "ids":
        return [for_ids(named.value)]
    if named.scheme == "unicode":
        return for_character(named.value)
    if named.scheme == "ivs":
        return for_character(named.value[0])
    return []
