"""Parts no code point names: what `data/vocab/custom-parts.tsv` says of each.

A description may hold a part no character names. BabelStone IDS writes one as a number (`{88}`,
the top of 合), CHISE IDS as an entity (`&CDP-8BD0;`) and zi.tools as a stroke chain (`#(-𠃌㇉)`).
ids-input gives each such shape one identifier and lists every name it is known by. A form holding
such a part has one spelling (`spelt`): the part is written as its identifier, `&X-00087;`, whatever
it was typed as, and a part ids-input knows only as a description of encoded characters is written as
that description. A part of a description that draws exactly one named part is written as that part,
so ⿱⿵𠆢一龴 is ⿱&X-00087;龴. An identifier taken out of use answers for the one it was merged into.
`drawn` writes a description out in encoded characters where every such part of it has a drawing in
them (`{88}` as ⿵𠆢一), taking a part's drawings in ids-input's order; 69 numbered parts are drawn
only by BabelStone Han PUA's private-use glyph.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

from .han_components import BINARY, TERNARY, UNARY

TABLE = Path(__file__).resolve().parents[2] / "data" / "vocab" / "custom-parts.tsv"
#: One part no code point names, in ids-input's grammar (src/core/custom.js): a number of up to four
#: digits, an entity, a stroke chain.
TOKEN = re.compile(r"\{[0-9]{1,4}\}|&[A-Za-z][A-Za-z0-9+_-]{0,45};|#\([^\s()\[\]{}#&;]{1,44}\)")
#: A drawing in BabelStone Han PUA's private-use characters, which only that font draws.
PRIVATE = re.compile(r"[-]")
IDENTIFIER = re.compile(r"&(X-[0-9]{5});")
#: One unit of a description in its one spelling: a part written as its identifier, or one character
#: with the variation selector it may carry.
UNIT = re.compile(r"&X-[0-9]{5};|.[︀-️\U000E0100-\U000E01EF]?", re.DOTALL)


def _arity(char: str) -> int:
    return 2 if char in BINARY else 3 if char in TERNARY else 1 if char in UNARY else 0


@dataclass(frozen=True)
class Part:
    key: str
    names: tuple[str, ...]
    draws: tuple[str, ...]
    approximate: tuple[str, ...]
    editorial: bool
    description: str
    #: For a part ids-input knows only as a description of encoded characters, that description in
    #: its one spelling; empty for a part a source names.
    spelling: str = ""
    #: The descriptions, in their one spelling, that are written as this part inside a larger one.
    folds: tuple[str, ...] = ()


@dataclass(frozen=True)
class Table:
    """The parts in use and the identifiers taken out of use, with the one each went into ('' for
    one withdrawn)."""

    parts: dict[str, Part]
    retired: dict[str, str]
    named: dict[str, str] = field(init=False)
    folded: dict[str, str] = field(init=False)

    def __post_init__(self) -> None:
        named: dict[str, str] = {}
        for key, part in self.parts.items():
            named[f"&{key};"] = key
            for name in part.names:
                named.setdefault(name, key)
        object.__setattr__(self, "named", named)
        object.__setattr__(self, "folded", {text: key for key, part in self.parts.items() for text in part.folds})

    def identifier(self, name: str) -> str | None:
        key = self.named.get(name)
        if key is None and (found := IDENTIFIER.fullmatch(name)):
            key = self.retired.get(found.group(1)) or None
        return key

    def unknown(self, text: str) -> list[str]:
        return [token for token in TOKEN.findall(text) if self.identifier(token) is None]

    def spelt(self, text: str) -> str:
        def written(found: re.Match[str]) -> str:
            key = self.identifier(found.group())
            if key is None:
                return found.group()
            return self.parts[key].spelling or f"&{key};"

        return self.fold(TOKEN.sub(written, text))

    def drawn(self, text: str, depth: int = 0) -> str | None:
        if depth > 8:
            return None
        missing = False

        def written(found: re.Match[str]) -> str:
            nonlocal missing
            key = self.identifier(found.group())
            out = next((out for draw in (self.parts[key].draws if key else ())
                        if not PRIVATE.search(draw) and (out := self.drawn(draw, depth + 1)) is not None), None)
            if out is None:
                missing = True
                return found.group()
            return out

        out = TOKEN.sub(written, text)
        return None if missing else out

    def fold(self, text: str) -> str:
        """`text` with every part of it that draws one named part written as that part; the whole text
        is never replaced, and a text that is no one description is left as it is."""
        units = UNIT.findall(text)
        at = 0

        def read(top: bool) -> str:
            nonlocal at
            if at >= len(units):
                raise ValueError
            unit = units[at]
            at += 1
            arity = _arity(unit)
            if not arity:
                return unit
            node = unit + "".join(read(False) for _ in range(arity))
            key = None if top else self.folded.get(node)
            return f"&{key};" if key else node

        try:
            out = read(True)
        except ValueError:
            return text
        return out if at == len(units) else text


def _split(text: str) -> tuple[str, ...]:
    return tuple(text.split(" ")) if text else ()


@cache
def table() -> Table:
    parts: dict[str, Part] = {}
    retired: dict[str, str] = {}
    for line in TABLE.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        key, names, draws, approximate, editorial, description, spelling, folds, gone = line.split("\t")
        if gone:
            retired[key] = "" if gone == "withdrawn" else gone
        else:
            parts[key] = Part(key, _split(names), _split(draws), _split(approximate), editorial == "1", description,
                              spelling, _split(folds))
    return Table(parts, retired)


def parts() -> dict[str, Part]:
    """Every identifier in use, with what the table says of it."""
    return table().parts


def identifier(name: str) -> str | None:
    """The identifier a part's name, or an identifier itself, stands for (`{88}` and `&X-00087;` are
    `X-00087`); one merged into another answers that one, and an unknown or withdrawn one None."""
    return table().identifier(name)


def spelt(text: str) -> str:
    """`text` in its one spelling: every part no code point names written as its identifier
    (`⿱{88}龴` and `⿱⿵𠆢一龴` are `⿱&X-00087;龴`); one the table does not know stays as it was typed."""
    return table().spelt(text)


def unknown(text: str) -> list[str]:
    """The parts of `text` no code point names that the table does not know."""
    return table().unknown(text)


def drawn(text: str) -> str | None:
    """`text` with each such part written as the first of its drawings that comes out in encoded
    characters only, or None where none does (BabelStone Han PUA's private-use glyph is none). A
    drawing may itself name such a part, so it is written out again."""
    return table().drawn(text)
