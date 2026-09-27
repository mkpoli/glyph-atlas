"""Headword boxes on a page of a glossary printed in entries down ruled columns, from its transcription.

The 倭語類解 sets each column as a run of entries read top to bottom, and the columns right to left.
An entry is its headword, one large Han character after another, each with small hangul readings
beside or under it; then a circle (○) and the Japanese reading in hangul. The Wikisource
transcription gives each entry's headword as a `;` line, in reading order, but not where it stands.

`page_entries` finds the entries among a page's detected character boxes:

1. The boxes are grouped into columns by the gaps between their centres, right to left.
2. A box is a circle when the character classifier reads it as ○ or 〇 and it stands at least
   `CIRCLE_HEIGHT` of the page's wide box (the 95th percentile of box widths) tall; a smaller box
   so read is a mark inside a reading. A box is a headword character when its area is at least
   `HEADWORD_AREA` of the wide box's square, or when it is at least `HEADWORD_WIDTH` of the wide box
   across (a flat character such as 一 or 三). Every other box is a reading.
3. Down each column, the headword characters before a circle are that entry's headword. A column
   with no circle holds no entry (the 版心, a heading).

`match` pairs the entries with the transcription's headwords. A page is used only when it has as
many entries as headwords, so that the order holds; within it, an entry is kept only when it has as
many boxes as its headword has printed characters. Nothing is guessed: each kept box is labelled
with the transcription's character at its place. The classifier only checks it (`agrees`): an entry
whose character the classifier knows but does not read there is left out, and a page on which more
than `REFUSED_SHARE` of the matched entries are refused is left out whole, since that is how a
pairing shifted by one looks.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from .schema import Box
from .wikitext import _IDC, _cluster, _skip_ids

#: The classifier classes read as the circle that ends an entry's headword and readings.
CIRCLES = frozenset({"U+25CB", "U+3007"})
#: Least height of a circle, as a share of the page's wide box.
CIRCLE_HEIGHT = 0.45
#: Least area of a headword character, as a share of the square of the page's wide box.
HEADWORD_AREA = 0.4
#: Least width of a flat headword character, as a share of the wide box.
HEADWORD_WIDTH = 0.8
#: Most of a page's matched entries the classifier may refuse before the page is taken as misaligned.
REFUSED_SHARE = 0.25
#: Least gap between two columns' box centres, as a share of the wide box.
COLUMN_GAP = 0.6

_HEADWORD_LINE = re.compile(r"^;(.*)$", re.MULTILINE)
#: Markup of a `;` line that heads a section instead of naming an entry.
_HEADING = re.compile(r"<h\d|<section\b")
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_TAG = re.compile(r"<[^>]*>")
#: `{{이체자|X}}`: a variant form of X that Unicode does not encode.
_VARIANT = re.compile(r"\{\{\s*이체자\s*\|\s*([^{}|]+?)\s*\}\}")
#: How a transcriber writes a character they could not read.
UNREADABLE = frozenset({"？", "?", "〓"})


@dataclass(frozen=True)
class Glyph:
    """One printed character of a headword: as transcribed, and the standard form given after it."""

    text: str
    standard: str | None = None

    @property
    def readable(self) -> bool:
        """Whether the transcription names the printed character (it may still be a description)."""
        return self.text not in UNREADABLE

    @property
    def encoded(self) -> bool:
        """Whether the printed character is one encoded character, not a description."""
        return self.readable and self.text[0] not in _IDC


@dataclass(frozen=True)
class Entry:
    column: int
    boxes: tuple[Box, ...]


def glyphs(headword: str) -> list[Glyph]:
    """The printed characters of a headword line, e.g. `𬌟(牽)牛星` or `一⿰方⿱厶夫(族)`.

    A character is one encoded character or one ideographic description sequence, and a `(…)`
    right after it gives its standard form. `{{이체자|X}}` is an unencoded variant of X, and `？` a
    character the transcriber could not read: each stands in its place but names no printed
    character. Comments and tags are not text.
    """
    headword = _TAG.sub("", _COMMENT.sub("", headword))
    headword = _VARIANT.sub(lambda m: f"〓({m.group(1)})", headword)
    out: list[Glyph] = []
    index = 0
    while index < len(headword):
        char = headword[index]
        if char.isspace() or char == ")":
            index += 1
            continue
        if char == "(":
            end = headword.find(")", index)
            if end < 0:  # a stray bracket
                index += 1
                continue
            if out:
                out[-1] = Glyph(out[-1].text, headword[index + 1:end])
            index = end + 1
            continue
        if char in UNREADABLE:
            out.append(Glyph(char))
            index += 1
            continue
        if char in _IDC:
            end = _skip_ids(headword, index)
            out.append(Glyph(headword[index:end]))
        else:
            text, end = _cluster(headword, index)
            out.append(Glyph(text))
        index = end
    return out


def headwords(wikitext: str) -> list[list[Glyph]]:
    """Each entry's headword on a transcribed page, in reading order; a section heading is none."""
    return [glyphs(line.strip()) for line in _HEADWORD_LINE.findall(wikitext) if not _HEADING.search(line)]


def wide_box(boxes: Sequence[Box]) -> float:
    """The page's wide box: the 95th percentile of box widths, which is a headword character's."""
    widths = sorted(box.w for box in boxes)
    return float(widths[min(len(widths) - 1, int(0.95 * len(widths)))])


def columns(boxes: Sequence[Box], wide: float) -> list[list[Box]]:
    """The boxes in columns, right to left, each top to bottom."""
    groups: list[list[Box]] = []
    last = None
    for box in sorted(boxes, key=lambda b: -(b.x + b.w / 2)):
        centre = box.x + box.w / 2
        if last is None or last - centre > COLUMN_GAP * wide:
            groups.append([])
        groups[-1].append(box)
        last = centre
    return [sorted(group, key=lambda b: b.y) for group in groups]


def kind(box: Box, label: str, wide: float) -> str:
    """`circle`, `headword` or `reading`, by rule 2 of the module docstring."""
    if label in CIRCLES and box.h >= CIRCLE_HEIGHT * wide:
        return "circle"
    if box.w * box.h >= HEADWORD_AREA * wide * wide or box.w >= HEADWORD_WIDTH * wide:
        return "headword"
    return "reading"


def page_entries(boxes: Sequence[Box], labels: Sequence[str]) -> list[Entry]:
    """The entries of a page, in reading order, from its boxes and the classifier's top label of each."""
    if not boxes:
        return []
    wide = wide_box(boxes)
    kinds = {id(box): kind(box, label, wide) for box, label in zip(boxes, labels, strict=True)}
    entries: list[Entry] = []
    for number, column in enumerate(columns(boxes, wide)):
        head: list[Box] = []
        for box in column:
            if kinds[id(box)] == "circle":
                entries.append(Entry(number, tuple(head)))
                head = []
            elif kinds[id(box)] == "headword":
                head.append(box)
    return entries


def agrees(glyph: Glyph, top: Sequence[str], known: set[str], policy: str = "align-v1") -> bool | None:
    """Whether the classifier's best classes `top` hold the glyph under the equivalence `policy`.

    None when the classifier cannot judge it: a described (unencoded) character, or one with no
    equivalent among the classifier's classes `known`.
    """
    from . import refs

    if not glyph.encoded:
        return None  # a description, or a character the transcription does not name
    forms = refs.equivalents(glyph.text, policy)
    if glyph.standard and len(glyph.standard) == 1:
        forms |= refs.equivalents(glyph.standard, policy)
    codes = {f"U+{ord(form):04X}" for form in forms if len(form) == 1}
    if not codes & known:
        return None
    return bool(codes & set(top))


def match(entries: Sequence[Entry], heads: Sequence[Sequence[Glyph]]) -> tuple[list[tuple[int, Entry, list[Glyph]]], str]:
    """The entries kept, each with its place in the page's order and its headword, and a report."""
    if not heads:
        return [], "no headword in the page text"
    if len(entries) != len(heads):
        return [], f"{len(entries)} entries found for {len(heads)} headwords"
    kept = [(place, entry, list(head)) for place, (entry, head) in enumerate(zip(entries, heads))
            if len(entry.boxes) == len(head)]
    return kept, f"{len(kept)} of {len(heads)} entries kept"
