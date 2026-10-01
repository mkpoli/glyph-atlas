"""What a unit's letterforms are written as, when a reviewer records that they differ from its label.

A crop of 還 may be written 𮟃. That is no error: the crop keeps its label, its grapheme and its
review, and its written form says which shape the page shows. A written form is one character (with
the combining marks or variation selector it carries), or an Ideographic Description Sequence for a
shape Unicode does not encode: ⿺辶𦊷 is 辶 wrapped round 𦊷. A sequence uses BabelStone's operators
as `han_components` reads them, each with its own number of operands, and its components are
ideographs, radicals, strokes, private-use characters, or ？ for a part no character names. The
Worker's `writtenForm` applies the same rule.
"""

from __future__ import annotations

import unicodedata

from .han_components import BINARY, TERNARY, UNARY

#: The longest written form, in code points: a sequence nested several levels deep stays well inside it.
LONGEST = 64

#: Code points a sequence may name as a component. Planes 2 and 3 hold ideographs only.
COMPONENTS = (
    (0x2E80, 0x2FDF),  # CJK radicals and Kangxi radicals
    (0x31C0, 0x31EE),  # CJK strokes
    (0x3400, 0x4DBF),
    (0x4E00, 0x9FFF),
    (0xE000, 0xF8FF),  # private use, where 구결자 and unencoded forms are kept
    (0xF900, 0xFAFF),
    (0xFF1F, 0xFF1F),  # ？, a part no character names
    (0x20000, 0x3FFFD),
    (0xF0000, 0x10FFFD),
)


def _selector(char: str) -> bool:
    point = ord(char)
    return 0xFE00 <= point <= 0xFE0F or 0xE0100 <= point <= 0xE01EF


def _component(char: str) -> bool:
    point = ord(char)
    return any(low <= point <= high for low, high in COMPONENTS)


def _arity(char: str) -> int:
    return 2 if char in BINARY else 3 if char in TERNARY else 1 if char in UNARY else 0


def is_sequence(value: str) -> bool:
    """Whether `value` is written as a description, which starts with an operator."""
    return bool(value) and _arity(value[0]) > 0


def _described(value: str, at: int = 0) -> int:
    """Where the one description starting at `value[at]` ends; ValueError when it is malformed."""
    if at >= len(value):
        raise ValueError("This description is missing a component.")
    arity = _arity(value[at])
    if not arity:
        if not _component(value[at]):
            raise ValueError(f"{value[at]} cannot be a component of a description.")
        at += 1
        return at + 1 if at < len(value) and _selector(value[at]) else at
    at += 1
    for _ in range(arity):
        at = _described(value, at)
    return at


def _single(value: str) -> bool:
    """One character: one base, with any combining marks and variation selector after it."""
    if not value or unicodedata.category(value[0]).startswith("M"):
        return False
    return all(unicodedata.category(c).startswith("M") or _selector(c) for c in value[1:])


def check(value: str) -> str:
    """`value` when it is a written form; ValueError naming what is wrong otherwise."""
    if not value or len(value) > LONGEST or value != value.strip():
        raise ValueError("Write one character or an ideographic description sequence.")
    if any(unicodedata.category(c) in ("Cc", "Cf", "Cs", "Zs", "Zl", "Zp") for c in value):
        raise ValueError("A written form has no spaces or control characters.")
    if is_sequence(value):
        if _described(value) != len(value):
            raise ValueError("This description has more components than its operators take.")
        return value
    if not _single(value):
        raise ValueError("Write one character or an ideographic description sequence.")
    return value
