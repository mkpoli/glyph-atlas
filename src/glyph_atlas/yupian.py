"""新修絫音引證群籍玉篇's headwords by the half-leaf of the 金刻本, as 青雀古籍's table gives them.

The table (`data/sources/xinxiu-yupian.yaml`) has one row per reading: a headword printed once with several
readings fills as many rows, which share the headword's place on its page (推导页内字序). A row without that
place and without a headword is a note printed as text: a section's opening note before its first headword, or
a group's count (凡N字) within it. The stroke count a section's headwords are grouped by (笔画（推导）) is printed
in a cartouche where it changes. Every row's status is a first proofreading by a language model (AI初校), as in
龍龕手鏡's table, and the rules that hold a doubtful headword back are that table's (`longkan.unsettled`),
counted in printed headwords.
"""
from __future__ import annotations

import csv
import re
from pathlib import Path

from . import longkan
from .longkan import Entry
from .tape import Slot

#: A section title 某部第幾 is printed at about this many gloss characters' length; a stroke count in its own cartouche.
TITLE, HEADER = 4, 0
#: How the table writes a stroke group it found no mark for.
UNMARKED = "〔無標記〕"
#: Marks around a headword the table's conventions do not explain: an editor's note (［口部開頭缺］,
#: [=此处版面残缺=]), 〖𠘲〗, 㜐（㛸）. The headword is placed and not labelled.
_MARKED = re.compile(r"[\[［〖〗（）()]")
#: What separates headwords printed one after another when one of them is written as its parts: 元失，元矢.
_BETWEEN = re.compile(r"[，、]")
#: A stroke count typed in a headword's place (十一).
_NUMERALS = set("一二三四五六七八九十")
_IDS = re.compile(r"[⿰-⿻]")


def heads(field: str) -> tuple[str | None, ...] | None:
    """The printed headwords a field names, None for one it does not name; None where `longkan.fields` reads it as is."""
    if _MARKED.search(field) or (len(field) > 1 and set(field) <= _NUMERALS):
        return (None,)
    if not _BETWEEN.search(field):
        return None
    return tuple(group if _IDS.search(group) or len(group) == 1 else None for group in _BETWEEN.split(field) if group)


def read(table: Path, edition: str = "金刻本") -> dict[int, list[Entry]]:
    """The printed headwords of the table by the half-leaf `edition` gives them, in the table's order.

    An entry's `row` is the table row of its first reading, its gloss every reading's, its note every reading's
    on a line of its own, and its `tone` the stroke group. A note printed as text is an entry with an empty field
    and `tone` "note"."""
    pages: dict[int, list[Entry]] = {}
    held: dict[tuple[int, str], int] = {}
    with table.open(encoding="utf-8-sig", newline="") as handle:
        for row, values in enumerate(csv.DictReader(handle), 1):
            if not values[edition].isdigit():
                continue
            page = int(values[edition])
            place = values["推导页内字序"].strip()
            entries = pages.setdefault(page, [])
            if not place:
                if not values["字头"].strip() and values["注文"].strip():
                    entries.append(Entry(row, "", values["注文"], values["卷序"], values["部首"], "note", page, values["校勘记"]))
                continue
            if (page, place) in held:
                k = held[page, place]
                first = entries[k]
                note = "\n".join(n for n in (first.note, values["校勘记"]) if n)
                entries[k] = Entry(first.row, first.field, first.gloss + values["注文"], first.volume, first.section, first.tone,
                                   page, note, first.heads)
                continue
            held[page, place] = len(entries)
            field = values["字头"].strip()
            entries.append(Entry(row, field, values["注文"], values["卷序"], values["部首"], values["笔画（推导）"].strip(), page,
                                 values["校勘记"], heads(field)))
    return pages


def paged(pages: dict[int, list[Entry]]) -> dict[int, list[Slot]]:
    """What each page prints in order: its headwords, a place for each section's title and note, and a stroke count
    where it changes. A section or stroke count that changes at the top of a page is seen against the page before."""
    out: dict[int, list[Slot]] = {}
    section, titled, strokes = None, None, None
    for page in sorted(pages):
        slots = out[page] = []
        for entry in pages[page]:
            if entry.tone == "note":
                opens = entry.section != section
                slots.append(Slot(None, (TITLE if opens else 0) + longkan.length(entry.gloss), -1))
                if opens:
                    section, titled, strokes = entry.section, entry.section, None
                continue
            if entry.section != section:
                if section is not None and entry.section != titled:
                    slots.append(Slot(None, TITLE, -1))
                section, strokes = entry.section, None
            if entry.tone not in ("", UNMARKED) and entry.tone != strokes:
                slots.append(Slot(None, HEADER, -1))
                strokes = entry.tone
            characters = longkan.fields(entry)
            for k, char in enumerate(characters):
                slots.append(Slot(char, longkan.length(entry.gloss) if k == len(characters) - 1 else 0, entry.row, k))
    return out


def places(pages: dict[int, list[Entry]]) -> dict[int, int]:
    """Each headword's row by its place among the book's printed headwords."""
    rows = sorted(entry.row for group in pages.values() for entry in group if entry.tone != "note")
    return {row: k for k, row in enumerate(rows)}


def _headwords(pages: dict[int, list[Entry]]) -> dict[int, list[Entry]]:
    return {page: [e for e in group if e.tone != "note"] for page, group in pages.items()}


def unsettled(pages: dict[int, list[Entry]], neighbours: bool = True) -> set[tuple[int, int]]:
    """`longkan.unsettled`, with neighbours counted in printed headwords."""
    return longkan.unsettled(_headwords(pages), neighbours, places(pages))


def kin(pages: dict[int, list[Entry]]) -> dict[tuple[int, int], set[tuple[int, int]]]:
    """`longkan.kin`, with neighbours counted in printed headwords."""
    return longkan.kin(_headwords(pages), places(pages))
