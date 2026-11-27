"""How a form is named: its representations (docs/design/form-model.md).

A form is a category of written appearance with an id of its own; a representation is one way of
naming it, and never its identity. A scheme says how the value is read:

- `unicode`: one character, a base with the combining marks it carries (𮟃, が).
- `ivs`: one base and one variation selector, an Ideographic Variation Sequence (葛󠄀).
  A CJK compatibility ideograph is no form of its own (docs/decisions/0013-finer-forms.md): typed, it
  is the sequence of the figure the MJ文字情報一覧表 implements as it (突 U+FA55 is 突 U+7A81 U+E0103),
  or with none its unified ideograph (說 U+F96F is 說 U+8AAA).
- `ids`: an Ideographic Description Sequence for a shape Unicode does not encode, kept exactly as
  written (⿺辶𦊷 is 辶 wrapped round 𦊷). Its operators are BabelStone's as `han_components` reads
  them, each with its own number of operands; its components are ideographs, radicals, strokes,
  katakana letters standing for a component of that shape (コ in ⿱コ疋, which no ideograph writes),
  private-use characters, ？ for a part no character names, or a part no code point names written as
  its identifier (`&X-00087;`, the top of 合). A description holding such a part has one spelling
  (`custom_parts.spelt`): ⿱{88}龴, ⿱&CDP-…;龴 and ⿱⿵𠆢一龴 are all ⿱&X-00087;龴. Any other
  description is kept as written.
- `mj`: an MJ 文字図形名 (`MJ012345`), with the MJ文字情報一覧表 version it was read from when known.
- `glyphwiki`: a GlyphWiki glyph name (`u2e7c3-j`), with its version when known.
- `pua`: one private-use character, with the namespace of the mapping it belongs to and that
  mapping's version, both required: the code point alone names nothing. The site's own mapping is the
  table of drawn forms (`drawn_forms`), whose code points a picker may give.

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
from functools import cache
from pathlib import Path

from . import custom_parts, drawn_forms
from .han_components import BINARY, TERNARY, UNARY

MJ_TSV = Path(__file__).resolve().parents[2] / "data" / "vocab" / "mj-kanji.tsv"

SCHEMES = ("unicode", "ivs", "ids", "mj", "glyphwiki", "pua")
#: The longest value, in code points: a description nested several levels deep stays well inside it.
#: A part no code point names counts as one.
LONGEST = 64
#: The longest text a request may name a form with: every component a part's identifier, nine code
#: points each.
TEXT_LONGEST = 10 * LONGEST

#: Code points a description may name as a component. Planes 2 and 3 hold ideographs only.
COMPONENTS = (
    (0x2E80, 0x2FDF),  # CJK radicals and Kangxi radicals
    (0x30A1, 0x30FA),  # katakana letters, for a component of that shape (コ, マ)
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
#: A part no code point names, in the one spelling a description holds it in.
PART = re.compile(r"&(?:X-[0-9]{5}|(?:VR|GW)-[A-Za-z0-9-]+);")
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


def _length(value: str) -> int:
    """How long `value` is, in code points, with each part no code point names counted as one."""
    return len(PART.sub("？", value))


def is_sequence(value: str) -> bool:
    """Whether `value` is written as a description, which starts with an operator."""
    return bool(value) and (_arity(value[0]) > 0 or value in custom_parts.YAO_SPELLINGS)


def _described(value: str, at: int = 0) -> int:
    """Where the one description starting at `value[at]` ends; ValueError when it is malformed."""
    if at >= len(value):
        raise ValueError("This description is missing a component.")
    arity = _arity(value[at])
    if (part := PART.match(value, at)) is not None:
        if part.group() not in custom_parts.YAO_SPELLINGS and custom_parts.identifier(part.group()) != part.group()[1:-1]:
            raise ValueError(f"{part.group()} is no part in use.")
        if part.end() < len(value) and _selector(value[part.end()]):
            raise ValueError("A part no code point names takes no variation selector.")
        return part.end()
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
    if not is_sequence(value) or _length(value) > LONGEST:
        return False
    try:
        return _described(value) == len(value)
    except ValueError:
        return False


def written(text: str) -> list[str]:
    """The characters and descriptions `text` names one after another: a description ends where its
    operators are satisfied, so 說⿰月兊説 names three. What follows a malformed description, or one
    longer than a form may be, is left out."""
    found, at = [], 0
    while at < len(text):
        if _arity(text[at]) or PART.match(text, at):
            try:
                end = _described(text, at)
            except (ValueError, RecursionError):
                break
            if _length(text[at:end]) > LONGEST:
                break
        else:
            end = at + 1
            while end < len(text) and (unicodedata.category(text[end]).startswith("M") or _selector(text[end])):
                end += 1
        found.append(text[at:end])
        at = end
    return found


def _single(value: str) -> bool:
    """One character: one base, with any combining marks and variation selector after it."""
    if not value or unicodedata.category(value[0]).startswith("M"):
        return False
    return all(unicodedata.category(c).startswith("M") or _selector(c) for c in value[1:])


def check_text(value: str) -> str:
    """`value` when it is one character or a description; ValueError naming what is wrong otherwise."""
    if not value or _length(value) > LONGEST or value != value.strip():
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


def compatibility(char: str) -> bool:
    """Whether `char` is a CJK compatibility ideograph: a code point that stands for a unified
    ideograph (說 U+F96F and U+F9A1 for 說 U+8AAA), which NFC writes in its place. The twelve unified
    ideographs of the compatibility block (﨑 U+FA11) stand for nothing."""
    return (len(char) == 1 and unicodedata.name(char, "").startswith("CJK COMPATIBILITY IDEOGRAPH")
            and unicodedata.normalize("NFC", char) != char)


@cache
def figures() -> dict[str, str]:
    """Each compatibility ideograph the MJ文字情報一覧表 gives as a figure's implementation, with that
    figure's Moji_Joho sequence: 突 U+FA55 with 突 U+7A81 U+E0103 (MJ030283)."""
    rows = [line.split("\t") for line in MJ_TSV.read_text(encoding="utf-8").splitlines() if not line.startswith("#")]
    at = {name: index for index, name in enumerate(rows[0])}
    found: dict[str, str] = {}
    for row in rows[1:]:
        code, sequence = row[at["implemented_code_point"]].removeprefix("U+"), row[at["ivs"]]
        if not code or not sequence or not compatibility(char := chr(int(code, 16))):
            continue
        base, selector = (chr(int(part, 16)) for part in sequence.split("_"))
        if char in found or unicodedata.normalize("NFC", char) != base:
            raise ValueError(f"the MJ table's figure for U+{code} is not one sequence of its unified ideograph")
        found[char] = base + selector
    return found


def stands_for(char: str) -> str:
    """What a compatibility ideograph is written as: its registered figure's sequence, or with none
    its unified ideograph. Inside a longer text, the base of a sequence or a part of a description,
    it is its unified ideograph."""
    if compatibility(char):
        return figures().get(char) or unicodedata.normalize("NFC", char)
    return "".join(unicodedata.normalize("NFC", c) if compatibility(c) else c for c in char)


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

    A private-use character is a drawn form where the site's table gives it (`drawn_forms`); any other
    is refused, since a picker cannot say which mapping it belongs to. A compatibility ideograph is
    what it stands for (`stands_for`), and a description is written in its one spelling
    (`custom_parts.spelt`).
    """
    if is_sequence(value):
        if unknown := custom_parts.unknown(value):
            raise ValueError(f"{' '.join(unknown)}: no such part is known.")
        value = custom_parts.spelt(stands_for(value))
    text = stands_for(check_text(value))
    if is_sequence(text):
        return check(Representation("ids", text))
    if drawn_forms.drawn(text):
        table = drawn_forms.table()
        return check(Representation("pua", text, table.namespace, table.version))
    if _selector(text[-1]):
        return check(Representation("ivs", text))
    return check(Representation("unicode", text))


def anchored_form(representation: Representation) -> str:
    """The id of the broad form this representation is the encoded name of."""
    return "fm:" + hashlib.sha256(("anchor\n" + representation.id).encode()).hexdigest()[:32]
