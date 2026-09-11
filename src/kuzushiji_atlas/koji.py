"""Parse a みんなで翻刻 transcription line in koji markup.

`parse(text)` returns the line as a tree with offsets into the raw line; `plain(text)` returns the
same line with the notation removed.

Plain text
----------
Honkoku-Lines stores every line twice: `text` as the transcriber entered it and `plain_text` with the
notation removed. `plain()` applies that rule; it was read off the `text`/`plain_text` pairs of
Honkoku-Lines v2.0 `lines.jsonl.gz` and reproduces `plain_text` for all 1,169,304 rows. Over the
whole line, in this order:

1. remove `【…】` (注記) together with its content;
2. remove `（…）` (振り仮名 reading) together with its content;
3. remove `｛…｝` together with its content;
4. replace `《…》` with what follows its first `：`, with `｜` and `﹅` dropped from it;
5. remove `＿` or `_` and the one 返り点 character after it;
6. remove `￣` and the run of kana after it;
7. remove the remaining marker characters `■ □ 〓 ＿ ￣ ＜ ＞ # ／ 《 》 ： ｜ ﹅`;
8. remove runs of ASCII letters and digits;
9. remove runs of full-width and half-width spaces, then strip both ends.

Steps 4 and 6 are not recursive and match left to right: in `《割書：《題：一》｜《題：二》》` step 4
keeps the inner tag name, so step 7 leaves `題一二`; `￣` before a character that is not kana is left
to step 7. Step 7 removes `□` and `■`, the placeholders for damaged glyphs. The rule keeps full-width
`＃`, `〔`, `〕`, `（`, `）`, `○` and ASCII `|`: the platform does not use them as notation
everywhere, so Honkoku-Lines leaves them in place.

Structure
---------
`Parsed.nodes` is a tree of `Text(start, end)` and `Element(kind, start, end, children, attrs)`;
`start`/`end` are half-open and index code points of the raw line. The tree is lossless: the `Text`
nodes and the spans of the elements partition the line.

`Parsed.chars` flattens the tree to one `Char(text, start, end, path, role)` per code point of the
transcribed text, in reading order. `text[start:end]` is always `text`. Notation characters — the
delimiters `《》【】（）〔〕｛｝＜＞`, the separators `：` and `｜` of a construct, the prefixes `＿`
and `￣`, and the markers `＃`, `％`, `<TATE>`, `<BLOCK>` — carry no `Char`, because they are not
glyphs of the document. `path` holds the `id` of every enclosing `Element`, outermost first, so
`path[-1]` is the innermost one; `Element.id` numbers elements in document order from 0.

`role` is one of `main`, `ruby`, `ruby-left`, `warigaki`, `note`, `okurigana`, `kaeriten`, `gap`,
`unreadable`, `cancelled`, `inserted`:

- `main` — text of the document, including the content of 題, 箱, 場所, 人物, 日時, 圏点 and of a
  `《name：…》` whose name is not known;
- `ruby` / `ruby-left` — 振り仮名 readings, right and left of the base;
- `warigaki` — 割書 columns; the enclosing column element has `attrs["column"]`, counted from 1 in
  reading order;
- `note` — content of 注記 `【…】`;
- `okurigana` / `kaeriten` — the kana after `￣` and the point after `＿`;
- `gap` / `unreadable` — one per `□`/`〓` and one per `■`;
- `cancelled` / `inserted` — the first and second field of 見せ消ち.

Elements that hold no glyph of their own carry the data in `attrs`: `reference` (`＃１０`) has
`number`; `gap` has `mark` and `count`; `block` (`％字下げ一`, `％表紙`) has `block`; `tate`
(`<TATE>`) has `text` = `ー`, the character the token stands for. The plain rule of Honkoku-Lines
keeps the ASCII angle brackets of `<TATE>` and drops the rest of the token; the corpus holds no such
token. Nested constructs are parsed inside their parent, so 振り仮名 inside 割書 is an element of the
割書 column.

A `《name：body》` whose name is not in the vocabulary becomes `Element(kind="unknown")` with
`attrs["unknown"] = name` and its body as `main` text. A known name with the wrong number of fields
keeps the same shape with `attrs["malformed"] = True`. An unbalanced or unterminated bracket is
literal text, and `Parsed.malformed` is true for the line.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from typing import Any, Literal

from pydantic import BaseModel, Field

__all__ = [
    "BRACKET_KINDS",
    "KAERITEN",
    "Char",
    "Element",
    "Node",
    "Parsed",
    "Role",
    "Text",
    "parse",
    "plain",
    "walk",
]

Role = Literal[
    "main",
    "ruby",
    "ruby-left",
    "warigaki",
    "note",
    "okurigana",
    "kaeriten",
    "gap",
    "unreadable",
    "cancelled",
    "inserted",
]


class Text(BaseModel):
    """A span of the raw line: an element's delimiters, or text outside any construct."""

    start: int
    end: int


class Element(BaseModel):
    """A parsed construct, spanning its own delimiters. `id` numbers elements in document order."""

    kind: str
    start: int
    end: int
    children: list[Text | Element] = Field(default_factory=list)
    attrs: dict[str, Any] = Field(default_factory=dict)
    id: int = -1


Node = Text | Element
Element.model_rebuild()


class Char(BaseModel):
    """One code point of the transcribed text. `path` is the `id` of every enclosing element."""

    text: str
    start: int
    end: int
    path: list[int] = Field(default_factory=list)
    role: Role


class Parsed(BaseModel):
    """A parsed line: `plain`, the node tree, the flattened characters and the malformed flag."""

    plain: str
    nodes: list[Node] = Field(default_factory=list)
    chars: list[Char] = Field(default_factory=list)
    malformed: bool = False


# --------------------------------------------------------------------------------------------
# The plain-text rule of Honkoku-Lines.

_PLAIN_NOTE = re.compile(r"【[^】]*】")
_PLAIN_READING = re.compile(r"（[^）]*）")
_PLAIN_BRACE = re.compile(r"｛[^｝]*｝")
_PLAIN_BRACKET = re.compile(r"《([^》]*)》")
_PLAIN_KAERITEN = re.compile(r"[＿_]([レ一二三四五六七八九十上中下天地人甲乙丙丁])")
_PLAIN_OKURIGANA = re.compile(r"￣([ぁ-ゖァ-ヺー]+)")
_PLAIN_MARKER = re.compile(r"[■□〓＿￣＜＞#／《》：｜﹅]")
_PLAIN_LATIN = re.compile(r"[A-Za-z0-9]+")
_PLAIN_SPACE = re.compile(r"[　 ]+")


def _bracket_body(match: re.Match[str]) -> str:
    body = match.group(1)
    if "：" in body:
        body = body.split("：", 1)[1]
    return body.replace("｜", "").replace("﹅", "")


def plain(text: str) -> str:
    """Return `text` with the koji notation removed, as Honkoku-Lines' `plain_text` does."""
    text = _PLAIN_NOTE.sub("", text)
    text = _PLAIN_READING.sub("", text)
    text = _PLAIN_BRACE.sub("", text)
    text = _PLAIN_BRACKET.sub(_bracket_body, text)
    text = _PLAIN_KAERITEN.sub("", text)
    text = _PLAIN_OKURIGANA.sub("", text)
    text = _PLAIN_MARKER.sub("", text)
    text = _PLAIN_LATIN.sub("", text)
    text = _PLAIN_SPACE.sub("", text)
    return text.strip()


# --------------------------------------------------------------------------------------------
# Notation vocabulary.

# 返り点 characters: the set the Honkoku-Lines rule strips after ＿.
KAERITEN = "レ一二三四五六七八九十上中下天地人甲乙丙丁"

_KANJI = "\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\U00020000-\U0003134f"
_HIRAGANA = "\u3041-\u3096"
_KATAKANA = "\u30a1-\u30fa\u30fc"
_HENTAIGANA = "\u1b000-\u1b12f"

# 《name：…》 names, with the element kind and the number of ｜-separated fields it takes.
BRACKET_KINDS: dict[str, tuple[str, int, int]] = {
    "振り仮名": ("ruby", 2, 3),
    "迎え仮名": ("ruby", 2, 3),
    "割書": ("warigaki", 1, 4),
    "見せ消ち": ("misekechi", 1, 2),
    "訂正": ("misekechi", 1, 2),
    "圏点": ("kenten", 1, 2),
    "傍点": ("kenten", 1, 2),
    "右線": ("right-line", 1, 1),
    "題": ("title", 1, 1),
    "外題": ("title", 1, 1),
    "内題": ("title", 1, 1),
    "箱": ("box", 1, 1),
    "文字囲": ("box", 1, 1),
    "場所": ("place", 1, 1),
    "人物": ("person", 1, 1),
    "日時": ("date", 1, 1),
    "注記": ("note", 1, 1),
    "傍注": ("note", 1, 1),
    "脚注": ("note", 1, 1),
    "送り仮名": ("okurigana", 1, 1),
    "返り点": ("kaeriten", 1, 1),
}

# A ruby base is one run of a single script class, or a bracketed note, or a damage placeholder.
_BASE = "|".join(
    (
        r"【[^【】]*】",
        r"[■□〓]+",
        f"[{_KANJI}]+",
        f"[{_HIRAGANA}]+",
        f"[{_KATAKANA}]+",
        f"[{_HENTAIGANA}]+",
        r"[A-Za-z]+",
        r"[０-９Ａ-Ｚａ-ｚ]+",
    )
)
_RUBY = re.compile(rf"(?P<prefix>[／｜]?)(?P<base>{_BASE})(?P<reading>（[^（）]*）)")
_READING_FIELD = re.compile(r"[^（）《》【】〔〕｛｝＜＞｜|＃#＿￣％※]+")
_OKURIGANA_RUN = re.compile(f"[{_HIRAGANA}{_KATAKANA}]+")
_WRAPPER = re.compile(r"〔[^〔〕\r\n]*〕|｛[^｛｝\r\n]*｝|＜[^＜＞\r\n]*＞")
_LEGACY_KAERITEN = re.compile(rf"[＿_][{KAERITEN}]")
_REFERENCE = re.compile(r"[＃#]([0-9０-９]+)")
_PSEUDO_TAGS: dict[str, tuple[str, str]] = {"<TATE>": ("tate", "ー"), "<BLOCK>": ("block", "")}
_BLOCK_LINE = re.compile(r"[　 \t]*％(?P<label>表紙|裏表紙|字下げ[一二三四五六])?[　 \t]*")
_GAP_MARKS = "□■〓"
_ZENKAKU_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")
_OPENERS = "《【〔｛＜（"
_CLOSERS = "》】〕｝＞）"
_UNBALANCED = "《》【】〔〕｛｝＜＞（）"


def _field_role(kind: str, index: int) -> Role | None:
    """The role of a construct's field, or None when the field keeps the enclosing role."""
    if kind == "ruby":
        return (None, "ruby", "ruby-left")[index]
    if kind == "misekechi":
        return "cancelled" if index == 0 else "inserted"
    if kind == "warigaki":
        return "warigaki"
    if kind == "note":
        return "note"
    if kind == "okurigana":
        return "okurigana"
    if kind == "kaeriten":
        return "kaeriten"
    return None


def walk(nodes: Iterable[Node]) -> Iterator[Element]:
    """Yield every element of a node tree in document order."""
    for node in nodes:
        if isinstance(node, Element):
            yield node
            yield from walk(node.children)


class _Parser:
    """Recursive-descent parser for one line. Every region is parsed inside an explicit end offset."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.pos = 0
        self.end = len(text)
        self.malformed = False
        self.ambient: Role = "main"
        self.chars: list[Char] = []
        self._next_id = 0
        self._stack: list[int] = []
        self._matchers = (
            self._ruby,
            self._bracket,
            self._note,
            self._wrapper,
            self._kaeriten,
            self._okurigana,
            self._gap,
            self._reference,
            self._pseudo_tag,
            self._block_mark,
        )

    # -- helpers -------------------------------------------------------------------------

    def _element(self, kind: str, start: int, attrs: dict[str, Any] | None = None) -> Element:
        element = Element(kind=kind, start=start, end=start, attrs=attrs or {}, id=self._next_id)
        self._next_id += 1
        return element

    @contextmanager
    def _inside(self, element: Element) -> Iterator[list[Node]]:
        self._stack.append(element.id)
        try:
            yield element.children
        finally:
            self._stack.pop()

    def _emit(self, start: int, end: int, role: Role) -> None:
        path = list(self._stack)
        for index in range(start, end):
            self.chars.append(Char(text=self.text[index], start=index, end=index + 1, path=path, role=role))

    def _raw(self, children: list[Node], start: int, end: int, *, merge: bool = False) -> None:
        """Add a span with no characters of its own: a delimiter, or literal text."""
        if end <= start:
            return
        previous = children[-1] if children else None
        if merge and isinstance(previous, Text) and previous.end == start:
            previous.end = end
            return
        children.append(Text(start=start, end=end))

    def _content(self, children: list[Node], start: int, end: int, role: Role) -> None:
        self._emit(start, end, role)
        self._raw(children, start, end, merge=True)

    def _fill(self, children: list[Node], start: int, end: int, role: Role) -> None:
        """Parse `text[start:end]` as content of the given role and append the resulting nodes."""
        self.pos = start
        children.extend(self._scan(end, role))

    def _match(self, opener: str, closer: str, start: int) -> int | None:
        """Return the offset just past the closer that matches the opener at `start`."""
        depth = 0
        for index in range(start, self.end):
            char = self.text[index]
            if char == opener:
                depth += 1
            elif char == closer:
                depth -= 1
                if depth == 0:
                    return index + 1
        return None

    def _top_level(self, start: int, end: int, separators: str) -> Iterator[int]:
        """Yield the offsets of `separators` outside any nested bracket."""
        depth = 0
        for index in range(start, end):
            char = self.text[index]
            if char in _OPENERS:
                depth += 1
            elif char in _CLOSERS:
                depth = max(0, depth - 1)
            elif depth == 0 and char in separators:
                yield index

    # -- the scanner ---------------------------------------------------------------------

    def run(self) -> list[Node]:
        return self._scan(len(self.text), "main")

    def _scan(self, end: int, ambient: Role) -> list[Node]:
        saved_end, saved_ambient = self.end, self.ambient
        self.end, self.ambient = end, ambient
        children: list[Node] = []
        try:
            while self.pos < end:
                node = None
                for matcher in self._matchers:
                    node = matcher()
                    if node is not None:
                        break
                if node is not None:
                    children.append(node)
                    continue
                start = self.pos
                self.pos = start + 1
                if self.text[start] in _UNBALANCED:
                    self.malformed = True
                self._content(children, start, start + 1, ambient)
        finally:
            self.end, self.ambient = saved_end, saved_ambient
        return children

    # -- constructs ----------------------------------------------------------------------

    def _ruby(self) -> Element | None:
        match = _RUBY.match(self.text, self.pos, self.end)
        if match is None:
            return None
        fields = re.split(r"[｜|]", match.group("reading")[1:-1])
        if not 1 <= len(fields) <= 2 or not all(_READING_FIELD.fullmatch(f) for f in fields):
            return None
        base_start, base_end = match.start("base"), match.end("base")
        reading_open, reading_close = match.start("reading"), match.end("reading") - 1
        element = self._element("ruby", match.start(), {"form": "legacy"})
        with self._inside(element) as children:
            self._raw(children, match.start(), base_start)
            self._fill(children, base_start, base_end, self.ambient)
            self._raw(children, base_end, reading_open + 1)
            start = reading_open + 1
            for index, field in enumerate(fields):
                if index:
                    self._raw(children, start - 1, start)
                self._fill(children, start, start + len(field), ("ruby", "ruby-left")[index])
                start += len(field) + 1
            self._raw(children, reading_close, reading_close + 1)
        element.end = match.end()
        self.pos = match.end()
        return element

    def _bracket(self) -> Element | None:
        start = self.pos
        if self.text[start] != "《":
            return None
        close = self._match("《", "》", start)
        if close is None:
            return None
        inner_start, inner_end = start + 1, close - 1
        label = next(self._top_level(inner_start, inner_end, "：:"), None)
        name = None if label is None else self.text[inner_start:label]
        body_start = inner_start if label is None else label + 1
        separators = list(self._top_level(body_start, inner_end, "｜|"))
        bounds: list[tuple[int, int]] = []
        field_start = body_start
        for at in separators:
            bounds.append((field_start, at))
            field_start = at + 1
        bounds.append((field_start, inner_end))
        spec = BRACKET_KINDS.get(name) if name is not None else None
        attrs: dict[str, Any] = {"form": "bracket"}
        kind = "unknown"
        if spec is not None and spec[1] <= len(bounds) <= spec[2]:
            kind = spec[0]
        else:
            attrs = {"unknown": name}
            if spec is not None:
                attrs["malformed"] = True
                self.malformed = True
        element = self._element(kind, start, attrs)
        with self._inside(element) as children:
            self._raw(children, start, body_start)
            for index, (field_start, field_end) in enumerate(bounds):
                if index:
                    self._raw(children, separators[index - 1], field_start)
                if kind == "warigaki":
                    column = self._element("warigaki", field_start, {"column": index + 1})
                    with self._inside(column) as column_children:
                        self._fill(column_children, field_start, field_end, "warigaki")
                    column.end = field_end
                    children.append(column)
                else:
                    self._fill(children, field_start, field_end, _field_role(kind, index) or self.ambient)
            self._raw(children, inner_end, close)
        element.end = close
        self.pos = close
        return element

    def _note(self) -> Element | None:
        start = self.pos
        if self.text[start] != "【":
            return None
        close = self._match("【", "】", start)
        if close is None:
            return None
        divider = self.text[start + 1 : close - 1]
        element = self._element("divider" if divider in ("右丁", "左丁") else "note", start)
        if element.kind == "divider":
            element.attrs["divider"] = divider
            element.children = [Text(start=start, end=close)]
        else:
            with self._inside(element) as children:
                self._raw(children, start, start + 1)
                self._fill(children, start + 1, close - 1, "note")
                self._raw(children, close - 1, close)
        element.end = close
        self.pos = close
        return element

    def _wrapper(self) -> Element | None:
        match = _WRAPPER.match(self.text, self.pos, self.end)
        if match is None:
            return None
        start, close = match.start(), match.end()
        opener = self.text[start]
        if opener == "〔":
            kind: str = "place"
        elif opener == "＜":
            kind = "date"
        elif _LEGACY_KAERITEN.fullmatch(self.text[start + 1 : close - 1]):
            kind = "kaeriten"
        else:
            kind = "person"
        element = self._element(kind, start, {"form": "legacy"})
        with self._inside(element) as children:
            self._raw(children, start, start + 1)
            self._fill(children, start + 1, close - 1, _field_role(kind, 0) or self.ambient)
            self._raw(children, close - 1, close)
        element.end = close
        self.pos = close
        return element

    def _kaeriten(self) -> Element | None:
        start = self.pos
        if self.text[start] not in "＿_":
            return None
        if start + 1 >= self.end or self.text[start + 1] not in KAERITEN:
            return None
        element = self._element("kaeriten", start, {"mark": self.text[start + 1]})
        with self._inside(element) as children:
            self._raw(children, start, start + 1)
            self._content(children, start + 1, start + 2, "kaeriten")
        element.end = start + 2
        self.pos = start + 2
        return element

    def _okurigana(self) -> Element | None:
        start = self.pos
        if self.text[start] != "￣":
            return None
        match = _OKURIGANA_RUN.match(self.text, start + 1, self.end)
        if match is None:
            return None
        element = self._element("okurigana", start, {"mark": "￣"})
        with self._inside(element) as children:
            self._raw(children, start, start + 1)
            self._content(children, start + 1, match.end(), "okurigana")
        element.end = match.end()
        self.pos = match.end()
        return element

    def _gap(self) -> Element | None:
        start = self.pos
        if self.text[start] not in _GAP_MARKS:
            return None
        mark = self.text[start]
        end = start
        while end < self.end and self.text[end] == mark:
            end += 1
        role: Role = "unreadable" if mark == "■" else "gap"
        element = self._element("gap", start, {"mark": mark, "count": end - start})
        with self._inside(element) as children:
            for index in range(start, end):
                self._content(children, index, index + 1, role)
        element.end = end
        self.pos = end
        return element

    def _reference(self) -> Element | None:
        match = _REFERENCE.match(self.text, self.pos, self.end)
        if match is None:
            return None
        start, end = match.start(), match.end()
        element = self._element(
            "reference",
            start,
            {"number": int(match.group(1).translate(_ZENKAKU_DIGITS)), "mark": self.text[start]},
        )
        element.children = [Text(start=start, end=end)]
        element.end = end
        self.pos = end
        return element

    def _pseudo_tag(self) -> Element | None:
        for token, (kind, rendered) in _PSEUDO_TAGS.items():
            if self.text.startswith(token, self.pos, self.end):
                end = self.pos + len(token)
                element = self._element(kind, self.pos, {"token": token, "text": rendered})
                element.children = [Text(start=self.pos, end=end)]
                element.end = end
                self.pos = end
                return element
        return None

    def _block_mark(self) -> Element | None:
        if self.pos != 0:
            return None
        match = _BLOCK_LINE.fullmatch(self.text)
        if match is None:
            return None
        element = self._element("block", 0, {"block": match.group("label")})
        element.children = [Text(start=0, end=len(self.text))]
        element.end = len(self.text)
        self.pos = len(self.text)
        return element


def parse(text: str) -> Parsed:
    """Parse one transcription line into its node tree, characters and plain text."""
    parser = _Parser(text)
    nodes = parser.run()
    return Parsed(plain=plain(text), nodes=nodes, chars=parser.chars, malformed=parser.malformed)
