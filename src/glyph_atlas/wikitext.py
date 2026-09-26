"""Read a proofread Wikisource page (zh.wikisource) as the glyphs printed on it, column by column.

`glyphs(text)` returns one list per column of the page, in reading order, and each column as the
`Glyph`s written in it. A glyph is one grapheme: a character with the combining marks and
variation selectors that follow it. Its `role` says what it is on the page:

- `main` — text of the book;
- `warigaki` — a column of a split annotation (割注); `column` counts them from 1, right to left;
- `note` — small annotation characters printed on the page;
- `unreadable` — a character the transcriber could not enter (`{{?}}`, an `{{SKchar}}` without its
  Unicode form, an ideographic description sequence); its `text` is empty;
- `substituted` — a character entered as the standard form of an unencoded variant
  (`{{Unencoded Original}}`); the ink is the variant, `text` is the standard form.

The page text is read as a tree: a template's parameters are read with the role the template gives
them, and a template inside another keeps the outer role unless it sets its own, so a note inside a
split annotation is a note and the characters after it are the annotation again. Columns follow the
page text: each line break, `<br>` and `{{換行頂格}}` (`DG`) starts a new column, and a template that
spans a line break keeps its role in the next column.

Editorial punctuation, which the transcribers add and the prints do not carry, is dropped, as are
`{{Interpretive apparatus}}` (`ia`), `{{nop}}`, `{{gap}}`, anchors, embedded files, comments and
HTML tags (their content is kept). `[[target|label]]` keeps its label, and `-{…}-` its text or, for
a conversion rule, its traditional (`zh-hant`, `zh-tw`, `zh-hk`) form.

The meaning of each template is the one its documentation on zh.wikisource states (read
2026-09-26):

- `雙行註文`, `分注`: parameter 1 is the right column, 2 the left;
- `SKchar`, `SKchar2`: a rare character given by number, with its Unicode form in parameter 2;
- `僻字`/`!`: the character, then its description; `校`: the character as printed, then the
  correction; `Year Link`/`YL`: the text, then a gloss; `換行頂格`/`DG`: the text, then a margin;
  `Unencoded Original`: the standard form, then a reference; `參`: the text, then an editor's
  remark; `PL`: the text, then a link target — each keeps parameter 1 only;
- `註`/`*` and `Annotation` (annotations of the book, in smaller type): `note`; `?`: unreadable;
- `Small`/`-`, `多行合一`, `Seal`, `3`, `letter-spacing`, `right` and any template not listed: the
  text of every positional parameter, in order (`1=`, `2=` and `1a=`-style names count as
  positions). A parameter that is only a length (`1em`, `.475em`, `32px`) sets the layout and is
  not text.

`Annotation` is documented for the annotations of a book, and some pages use it for an editor's note;
those notes come out as `note` glyphs as well.
"""

from __future__ import annotations

import html
import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

Role = Literal["main", "warigaki", "note", "unreadable", "substituted"]

#: Templates whose content is not text of the page.
DROPPED = frozenset({"ia", "interpretive apparatus", "nop", "gap", "sk anchor", "visible anchor"})
#: Templates whose parameter 1 is the printed text and whose later parameters are not.
FIRST_ONLY = frozenset({"yl", "year link", "校", "僻字", "!", "dg", "換行頂格", "參", "pl"})
WARIGAKI = frozenset({"雙行註文", "dl", "分注"})
NOTE = frozenset({"*", "註", "annotation"})
UNREADABLE = frozenset({"?"})
RARE = frozenset({"skchar", "skchar2"})
SUBSTITUTED = frozenset({"original character", "unencoded original", "uno"})
#: Templates that start a new column before their text.
BREAK = frozenset({"dg", "換行頂格"})
#: Conversion-rule variants that give the traditional form, in order of preference.
TRADITIONAL = ("zh-hant", "zh-tw", "zh-hk", "zh-mo")

_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_BR = re.compile(r"<br\s*/?>", re.IGNORECASE)
_TAG = re.compile(r"</?[a-zA-Z][^>]*>")
_FILE = re.compile(r"^(?:File|Image|檔案|文件|图像|圖像):", re.IGNORECASE)
_POSITION = re.compile(r"^(\d+)([a-z]?)\s*=")
_NAMED = re.compile(r"^[\w\s\-]+=")
_LENGTH = re.compile(r"^\s*(?:-?[\d.]+\s*(?:em|ex|px|pt|cm|mm|%|rem)|calc\(.*\))\s*$")
_BREAK = "\n"
#: Ideographic description characters and the number of components each takes.
_IDC = {**{chr(code): 2 for code in range(0x2FF0, 0x2FFC)}, "⿲": 3, "⿳": 3, "㇯": 2}


@dataclass(frozen=True)
class Glyph:
    text: str
    role: Role = "main"
    column: int | None = None


def glyphs(text: str) -> list[list[Glyph]]:
    """The glyphs of a page, one list per column, by the rules of the module docstring."""
    text = _COMMENT.sub("", text)
    text = _BR.sub(_BREAK, text)
    reader = _Reader()
    reader.read(text, "main", None)
    return [column for column in reader.columns if column]


class _Reader:
    def __init__(self) -> None:
        self.columns: list[list[Glyph]] = [[]]

    def emit(self, glyph: Glyph) -> None:
        self.columns[-1].append(glyph)

    def new_column(self) -> None:
        if self.columns[-1]:
            self.columns.append([])

    def read(self, text: str, role: Role, column: int | None) -> None:
        index = 0
        while index < len(text):
            if text.startswith("{{", index):
                end = _closing(text, index, "{{", "}}")
                self.template(text[index + 2:end], role, column)
                index = end + 2
            elif text.startswith("[[", index):
                end = _closing(text, index, "[[", "]]")
                self.link(text[index + 2:end], role, column)
                index = end + 2
            elif text.startswith("-{", index):
                end = _closing(text, index, "-{", "}-")
                self.read(_converted(text[index + 2:end]), role, column)
                index = end + 2
            elif text[index] == "<":
                tag = _TAG.match(text, index)
                index = tag.end() if tag else index + 1
            elif text[index] == _BREAK:
                self.new_column()
                index += 1
            elif text[index] == "&":
                entity = re.match(r"&#?\w+;", text[index:])
                if entity:
                    self.read(html.unescape(entity.group()), role, column)
                    index += len(entity.group())
                else:
                    index += 1
            elif text[index] in _IDC:
                index = _skip_ids(text, index)
                self.emit(Glyph("", "unreadable", column))
            else:
                cluster, index = _cluster(text, index)
                if _is_glyph(cluster[0]):
                    self.emit(Glyph(cluster, role, column))

    def link(self, body: str, role: Role, column: int | None) -> None:
        target, _, label = body.partition("|")
        if _FILE.match(target):
            return
        self.read(label if label else target, role, column)

    def template(self, body: str, role: Role, column: int | None) -> None:
        parts = _split(body)
        name = parts[0].strip().lower()
        args = _positional(parts[1:])
        if name in DROPPED:
            return
        if name in BREAK:
            self.new_column()
        if name in WARIGAKI:
            for number, arg in enumerate(args[:2], 1):
                self.read(arg, "warigaki", number)
        elif name in NOTE:
            self.read("".join(args), "note", column)
        elif name in UNREADABLE:
            self.emit(Glyph("", "unreadable", column))
        elif name in RARE:
            character = args[1].strip() if len(args) > 1 else ""
            if character:
                self.read(character, role, column)
            else:
                self.emit(Glyph("", "unreadable", column))
        elif name in SUBSTITUTED:
            self.read(args[0] if args else "", "substituted", column)
        elif name in FIRST_ONLY:
            self.read(args[0] if args else "", role, column)
        else:
            self.read("".join(args), role, column)


def _closing(text: str, start: int, opening: str, closing: str) -> int:
    """The index of the `closing` that matches the `opening` at `start`, or the end of the text."""
    depth, index = 0, start
    while index < len(text):
        if text.startswith(opening, index):
            depth += 1
            index += len(opening)
        elif text.startswith(closing, index):
            depth -= 1
            if depth == 0:
                return index
            index += len(closing)
        else:
            index += 1
    return len(text)


def _split(body: str) -> list[str]:
    """A template body split at the `|` that are not inside a nested template, link or rule."""
    parts, depth, start, index = [], 0, 0, 0
    while index < len(body):
        pair = body[index:index + 2]
        if pair in ("{{", "[[", "-{"):
            depth += 1
            index += 2
        elif pair in ("}}", "]]", "}-") and depth:
            depth -= 1
            index += 2
        elif body[index] == "|" and depth == 0:
            parts.append(body[start:index])
            start = index = index + 1
        else:
            index += 1
    parts.append(body[start:])
    return parts


def _positional(parts: list[str]) -> list[str]:
    """The positional parameters in order: unnamed ones, then `1=`/`1a=` names at their place."""
    ordered: list[tuple[tuple[int, str], str]] = []
    position = 0
    for part in parts:
        named = _POSITION.match(part)
        if named:
            ordered.append(((int(named.group(1)), named.group(2)), part[named.end():]))
        elif _NAMED.match(part):
            continue
        else:
            position += 1
            ordered.append(((position, ""), part))
    return [value for _, value in sorted(ordered, key=lambda item: item[0]) if not _LENGTH.match(value)]


def _converted(rule: str) -> str:
    """The text of `-{…}-`: the literal, or the traditional variant of a conversion rule."""
    if ":" not in rule:
        return rule
    variants = {}
    for piece in rule.split(";"):
        key, _, value = piece.partition(":")
        if value:
            variants[key.strip()] = value
    for key in TRADITIONAL:
        if key in variants:
            return variants[key]
    return next(iter(variants.values()), "")


def _skip_ids(text: str, index: int) -> int:
    """The index after the ideographic description sequence that starts at `index`."""
    arity = _IDC[text[index]]
    index += 1
    for _ in range(arity):
        if index >= len(text):
            break
        if text[index] in _IDC:
            index = _skip_ids(text, index)
        else:
            _, index = _cluster(text, index)
    return index


def _cluster(text: str, index: int) -> tuple[str, int]:
    """The grapheme at `index`: a character and the combining marks and selectors after it."""
    end = index + 1
    while end < len(text) and _attaches(text[end]):
        end += 1
    return text[index:end], end


def _attaches(char: str) -> bool:
    code = ord(char)
    return unicodedata.category(char).startswith("M") or 0xFE00 <= code <= 0xFE0F or 0xE0100 <= code <= 0xE01EF


def _is_glyph(char: str) -> bool:
    """Whether a character is written text: letters, numbers and symbols, not spaces or punctuation."""
    return unicodedata.category(char)[0] in "LN" or unicodedata.category(char) == "So"
