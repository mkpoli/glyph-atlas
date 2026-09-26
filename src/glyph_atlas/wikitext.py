"""Read a proofread Wikisource page (zh.wikisource) as the glyphs printed on it, column by column.

`glyphs(text)` returns one list per column of the page, in reading order, and each column as the
`Glyph`s written in it. A glyph is one grapheme: a character with the combining marks and
variation selectors that follow it. Its `role` says what it is on the page:

- `main` — text of the book;
- `warigaki` — a column of a split annotation (割注); `column` counts them from 1, right to left;
- `note` — small annotation characters printed on the page;
- `unreadable` — a character the transcriber could not enter (`{{?}}`, an `{{SKchar}}` without its
  Unicode form); its `text` is empty;
- `substituted` — a character entered as the standard form of an unencoded variant
  (`{{Unencoded Original}}`); the ink is the variant, `text` is the standard form.

Columns follow the page text: each non-empty line is a column, and `{{換行頂格}}` (`DG`) and
`<br>` start a new one. Editorial punctuation, which the transcribers add and the prints do not
carry, is dropped, as are `{{Interpretive apparatus}}` (`ia`), `{{nop}}`, `{{gap}}`, embedded files,
comments and HTML tags (their content is kept). `{{letter-spacing}}` keeps its last parameter. `-{…}-` and `[[target|label]]` keep their text.

The meaning of each template is the one its documentation on zh.wikisource states (read
2026-09-26): `雙行註文` (parameter 1 is the right column, 2 the left), `SKchar` and `SKchar2`
(a rare character given by number, with its Unicode form when one is filled in), `僻字`/`!`
(the character, then its description), `校` (parameter 1 is the character as printed, 2 the
correction), `註`/`*`, `?`, `Year Link`/`YL` (parameter 1 is the text), `換行頂格`/`DG`,
`Unencoded Original` (parameter 1 is the standard form). A template not listed keeps the text of
its first parameter as `main`.
"""

from __future__ import annotations

import html
import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

Role = Literal["main", "warigaki", "note", "unreadable", "substituted"]

#: Templates whose content is not text of the page.
DROPPED = frozenset({"ia", "interpretive apparatus", "nop", "gap", "sk anchor", "visible anchor", "-"})
#: Templates whose first parameter is the printed text, read as main text.
FIRST = frozenset({"yl", "year link", "dg", "換行頂格", "校", "larger", "xx-larger",
                   "xxxx-larger", "big", "right", "center", "c", "box", "border", "w", "專", "參", "pl", "seal",
                   "僻字", "!"})
#: Templates whose last parameter is the text and whose first ones set its spacing.
LAST = frozenset({"letter-spacing", "lsp"})
WARIGAKI = frozenset({"雙行註文", "dl", "分注"})
NOTE = frozenset({"*", "註"})
UNREADABLE = frozenset({"?"})
RARE = frozenset({"skchar", "skchar2"})
SUBSTITUTED = frozenset({"original character", "unencoded original", "uno"})
#: Templates that start a new column before their text.
BREAK = frozenset({"dg", "換行頂格"})

_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_TAG = re.compile(r"</?[a-zA-Z][^>]*>")
_BR = re.compile(r"<br\s*/?>", re.IGNORECASE)
_FILE = re.compile(r"\[\[(?:File|Image|檔案|文件|图像|圖像):[^\]]*\]\]", re.IGNORECASE)
_LINK = re.compile(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]")
_CONVERT = re.compile(r"-\{(?:[^{}|]*\|)?([^{}]*)\}-")
_BREAK_MARK = "\n"


@dataclass(frozen=True)
class Glyph:
    text: str
    role: Role = "main"
    column: int | None = None


def glyphs(text: str) -> list[list[Glyph]]:
    """The glyphs of a page, one list per column, by the rules of the module docstring."""
    text = _COMMENT.sub("", text)
    text = _BR.sub(_BREAK_MARK, text)
    columns: list[list[Glyph]] = []
    for line in _expand(text).split(_BREAK_MARK):
        column = [glyph for glyph in _line_glyphs(line) if glyph is not None]
        if column:
            columns.append(column)
    return columns


# Templates are expanded into a private markup the line reader understands: a run of characters of
# one role is wrapped in U+E000 role U+E001 … U+E002, and an unreadable glyph is U+E003.
_OPEN, _SEP, _CLOSE, _GAP = "", "", "", ""


def _expand(text: str) -> str:
    """Replace every template, innermost first, and the links and conversion guards around text."""
    previous = None
    while previous != text:
        previous = text
        text = _FILE.sub("", text)
        text = _LINK.sub(r"\1", text)
        text = _CONVERT.sub(r"\1", text)
        text = re.sub(r"\{\{([^{}]*)\}\}", _template, text)
    text = _TAG.sub("", text)
    return html.unescape(text)


def _template(match: re.Match[str]) -> str:
    parts = [part.strip() for part in match.group(1).split("|")]
    name, args = parts[0].lower(), [a for a in parts[1:] if "=" not in a or a.startswith("&")]
    first = args[0] if args else ""
    if name in DROPPED:
        return ""
    prefix = _BREAK_MARK if name in BREAK else ""
    if name in WARIGAKI:
        return prefix + "".join(_wrap(f"warigaki{i}", arg) for i, arg in enumerate(args[:2], 1))
    if name in NOTE:
        return prefix + _wrap("note", first)
    if name in UNREADABLE:
        return prefix + _GAP
    if name in RARE:
        character = html.unescape(args[1]) if len(args) > 1 and args[1] else ""
        return prefix + (character if character else _GAP)
    if name in SUBSTITUTED:
        return prefix + _wrap("substituted", first)
    if name in LAST:
        return prefix + (args[-1] if args else "")
    return prefix + first


def _wrap(role: str, text: str) -> str:
    return f"{_OPEN}{role}{_SEP}{text}{_CLOSE}" if text else ""


def _line_glyphs(line: str) -> list[Glyph | None]:
    found: list[Glyph | None] = []
    role, column = "main", None
    index = 0
    while index < len(line):
        char = line[index]
        if char == _OPEN:
            end = line.index(_SEP, index)
            name = line[index + 1:end]
            role, column = ("warigaki", int(name[-1])) if name.startswith("warigaki") else (name, None)
            index = end + 1
            continue
        if char == _CLOSE:
            role, column = "main", None
            index += 1
            continue
        if char == _GAP:
            found.append(Glyph("", "unreadable"))
            index += 1
            continue
        cluster = char
        index += 1
        while index < len(line) and _attaches(line[index]):
            cluster += line[index]
            index += 1
        if _is_glyph(char):
            found.append(Glyph(cluster, role, column))
    return found


def _attaches(char: str) -> bool:
    """A combining mark or a variation selector, which belongs to the character before it."""
    code = ord(char)
    return (unicodedata.category(char).startswith("M") or 0xFE00 <= code <= 0xFE0F
            or 0xE0100 <= code <= 0xE01EF)


def _is_glyph(char: str) -> bool:
    """Whether a character is written text: letters and numbers, not spaces or punctuation."""
    return unicodedata.category(char)[0] in "LN" or unicodedata.category(char) == "So"
