"""Parts no code point names: what `data/vocab/custom-parts.tsv` says of each.

A description may hold a part no character names. BabelStone IDS writes one as a number (`{88}`,
the top of 合), CHISE IDS as an entity (`&CDP-8BD0;`) and zi.tools as a stroke chain (`#(-𠃌㇉)`).
ids-input gives each such shape one identifier and lists every name it is known by; the atlas writes
the part with its identifier, `&X-00087;`, whatever it was typed as (`spelt`). An identifier taken out
of use answers for the one it was merged into. `drawn` writes a description out in encoded characters
where every such part of it has a drawing in them (`{88}` as ⿵𠆢一); 69 numbered parts are drawn only by
BabelStone Han PUA's private-use glyph.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

TABLE = Path(__file__).resolve().parents[2] / "data" / "vocab" / "custom-parts.tsv"
#: One part no code point names, in any of the three spellings or as an identifier.
TOKEN = re.compile(r"\{\d+\}|&[A-Za-z0-9+._-]+;|#\([^()\s]*\)")
#: A drawing in BabelStone Han PUA's private-use characters, which only that font draws.
PRIVATE = re.compile(r"[\uE000-\uF8FF]")
IDENTIFIER = re.compile(r"&(X-\d{5});")


@dataclass(frozen=True)
class Part:
    key: str
    names: tuple[str, ...]
    draw: str
    approximate: bool
    description: str


@cache
def _table() -> tuple[dict[str, Part], dict[str, str]]:
    parts: dict[str, Part] = {}
    retired: dict[str, str] = {}
    for line in TABLE.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        key, names, draw, approximate, description, gone = line.split("\t")
        if gone:
            retired[key] = "" if gone == "withdrawn" else gone
        else:
            parts[key] = Part(key, tuple(names.split(" ")) if names else (), draw, approximate == "1", description)
    return parts, retired


def parts() -> dict[str, Part]:
    """Every identifier in use, with what the table says of it."""
    return _table()[0]


@cache
def _named() -> dict[str, str]:
    named: dict[str, str] = {}
    for key, part in parts().items():
        named[f"&{key};"] = key
        for name in part.names:
            named.setdefault(name, key)
    return named


def identifier(name: str) -> str | None:
    """The identifier a part's name, or an identifier itself, stands for (`{88}` and `&X-00087;` are
    `X-00087`); one merged into another answers that one, and an unknown or withdrawn one None."""
    key = _named().get(name)
    if key is None and (found := IDENTIFIER.fullmatch(name)):
        key = _table()[1].get(found.group(1)) or None
    return key


def spelt(text: str) -> str:
    """`text` with every part no code point names written as its identifier (`⿱{88}龴` is
    `⿱&X-00087;龴`); one the table does not know stays as it was typed."""
    return TOKEN.sub(lambda found: f"&{key};" if (key := identifier(found.group())) else found.group(), text)


def unknown(text: str) -> list[str]:
    """The parts of `text` no code point names that the table does not know."""
    return [token for token in TOKEN.findall(text) if identifier(token) is None]


def drawn(text: str, depth: int = 0) -> str | None:
    """`text` with each such part written as what draws it, in encoded characters only, or None where
    a part has no drawing in them (BabelStone Han PUA's private-use glyph is none). A drawing may
    itself name such a part, so it is written out again."""
    if depth > 8:
        return None
    missing = False

    def written(found: re.Match[str]) -> str:
        nonlocal missing
        key = identifier(found.group())
        draw = parts()[key].draw if key else ""
        out = drawn(draw, depth + 1) if draw and not PRIVATE.search(draw) else None
        if out is None:
            missing = True
            return found.group()
        return out

    out = TOKEN.sub(written, text)
    return None if missing else out
