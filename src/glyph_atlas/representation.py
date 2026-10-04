"""How a form is named: its representations (docs/design/form-model.md).

A form is a category of written appearance with an id of its own; a representation is one way of
naming it, and never its identity. A scheme says how the value is read:

- `unicode`: one character, a base with the combining marks it carries (𮟃, が).
- `ivs`: one base and one variation selector, an Ideographic Variation Sequence (葛󠄀).
- `ids`: an Ideographic Description Sequence for a shape Unicode does not encode, kept exactly as
  written (⿺辶𦊷 is 辶 wrapped round 𦊷). Its operators are BabelStone's as `han_components` reads
  them, each with its own number of operands; its components are ideographs, radicals, strokes,
  private-use characters, or ？ for a part no character names. A normalised IDS would only be a
  versioned search key, so none is made here.
- `mj`: an MJ 文字図形名 (`MJ012345`), with the MJ文字情報一覧表 version it was read from when known.
- `glyphwiki`: a GlyphWiki glyph name (`u2e7c3-j`), with its version when known.
- `pua`: one private-use character, with the namespace of the mapping it belongs to and that
  mapping's version, both required: the code point alone names nothing.

A representation's id hashes its scheme, namespace, version and value, so every service names it
alike. A code point anchors at most one broad form, the encoded form of that character; its id is
derived from the representation, so the first person to choose a value names the same form any later
choice of it does. The Worker's `representation.ts` applies the same rules.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass

from .han_components import BINARY, TERNARY, UNARY

SCHEMES = ("unicode", "ivs", "ids", "mj", "glyphwiki", "pua")
#: The longest value, in code points: a description nested several levels deep stays well inside it.
LONGEST = 64

#: Code points a description may name as a component. Planes 2 and 3 hold ideographs only.
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
PRIVATE_USE = ((0xE000, 0xF8FF), (0xF0000, 0xFFFFD), (0x100000, 0x10FFFD))
MJ = re.compile(r"MJ\d{6}")
GLYPHWIKI = re.compile(r"[a-z0-9][a-z0-9_@-]{0,63}")


def _selector(char: str) -> bool:
    point = ord(char)
    return 0xFE00 <= point <= 0xFE0F or 0xE0100 <= point <= 0xE01EF


def _component(char: str) -> bool:
    point = ord(char)
    return any(low <= point <= high for low, high in COMPONENTS)


def _private(char: str) -> bool:
    point = ord(char)
    return any(low <= point <= high for low, high in PRIVATE_USE)


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


def described(value: str) -> bool:
    """Whether `value` is one well-formed description, which names one character Unicode lacks."""
    if not is_sequence(value) or len(value) > LONGEST:
        return False
    try:
        return _described(value) == len(value)
    except ValueError:
        return False


def _single(value: str) -> bool:
    """One character: one base, with any combining marks and variation selector after it."""
    if not value or unicodedata.category(value[0]).startswith("M"):
        return False
    return all(unicodedata.category(c).startswith("M") or _selector(c) for c in value[1:])


def check_text(value: str) -> str:
    """`value` when it is one character or a description; ValueError naming what is wrong otherwise."""
    if not value or len(value) > LONGEST or value != value.strip():
        raise ValueError("Write one character or an ideographic description sequence.")
    if any(unicodedata.category(c) in ("Cc", "Cf", "Cs", "Zs", "Zl", "Zp") for c in value):
        raise ValueError("A form has no spaces or control characters.")
    if is_sequence(value):
        if _described(value) != len(value):
            raise ValueError("This description has more components than its operators take.")
        return value
    if not _single(value):
        raise ValueError("Write one character or an ideographic description sequence.")
    return value


@dataclass(frozen=True)
class Representation:
    """One name of a form: a scheme, its value as written, and the namespace and version it holds in."""

    scheme: str
    value: str
    namespace: str | None = None
    version: str | None = None

    @property
    def id(self) -> str:
        key = json.dumps([self.scheme, self.namespace, self.version, self.value], ensure_ascii=False, separators=(",", ":"))
        return "rp:" + hashlib.sha256(key.encode()).hexdigest()[:32]


def check(representation: Representation) -> Representation:
    """`representation` when its value fits its scheme; ValueError naming what is wrong otherwise."""
    scheme, value = representation.scheme, representation.value
    if scheme not in SCHEMES:
        raise ValueError(f"Unknown scheme {scheme!r}.")
    if scheme == "ids":
        if not is_sequence(value):
            raise ValueError("A description starts with an operator.")
        check_text(value)
    elif scheme in ("unicode", "ivs", "pua"):
        check_text(value)
        if is_sequence(value):
            raise ValueError("A description is written as an IDS.")
        selected = _selector(value[-1])
        if scheme == "ivs" and not (len(value) == 2 and selected):
            raise ValueError("A variation sequence is one base and one variation selector.")
        if scheme == "unicode" and selected:
            raise ValueError("A character with a variation selector is written as an IVS.")
        if scheme == "pua" and not (len(value) == 1 and _private(value)):
            raise ValueError("A private-use form is one private-use character.")
        if scheme != "pua" and _private(value[0]):
            raise ValueError("A private-use character names nothing without the namespace of its mapping.")
    elif scheme == "mj" and not MJ.fullmatch(value):
        raise ValueError("An MJ name is MJ and six digits.")
    elif scheme == "glyphwiki" and not GLYPHWIKI.fullmatch(value):
        raise ValueError("A GlyphWiki name is lower-case letters, digits, -, _ and @.")
    if scheme == "pua" and not (representation.namespace and representation.version):
        raise ValueError("A private-use form names the namespace and version of its mapping.")
    return representation


def typed(value: str) -> Representation:
    """What a reviewer typed or picked as a form: a description, a variation sequence or a character.

    A private-use character is refused here, since a picker cannot say which mapping it belongs to.
    """
    text = check_text(value)
    if is_sequence(text):
        return check(Representation("ids", text))
    if _selector(text[-1]):
        return check(Representation("ivs", text))
    return check(Representation("unicode", text))


def anchored_form(representation: Representation) -> str:
    """The id of the broad form this representation is the encoded name of."""
    return "fm:" + hashlib.sha256(("anchor\n" + representation.id).encode()).hexdigest()[:32]
