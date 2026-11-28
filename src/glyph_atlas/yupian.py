"""新修絫音引證群籍玉篇's headwords by the half-leaf of the 金刻本, as 青雀古籍's table gives them.

The table (`data/sources/xinxiu-yupian.yaml`) has one row per reading: a headword printed once with several
readings fills as many rows, which share the headword's place on its page (推导页内字序). A row without that
place and without a headword is a section's opening note, printed as text before its first headword. The
stroke count a section's headwords are grouped by (笔画（推导）) is printed in a cartouche where it changes.
Every row's status is a first proofreading by a language model (AI初校), as in 龍龕手鏡's table, and the rules
that hold a doubtful headword back are that table's (`longkan.unsettled`), counted in printed headwords.
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
#: An editor's note standing in a headword field: ［口部開頭缺］, [=此处版面残缺=].
_NOTE = re.compile(r"[\[［]")
#: Punctuation between headwords printed together: 歲勾，歲欠.
_BETWEEN = re.compile(r"[，、。；\s]")


def _field(text: str) -> str:
    """The headwords a field names as printed: empty for an editor's note in its place."""
    return "" if _NOTE.search(text) else _BETWEEN.sub("", text)


def read(table: Path, edition: str = "金刻本") -> dict[int, list[Entry]]:
    """The printed headwords of the table by the half-leaf `edition` gives them, in the table's order.

    An entry's `row` is the table row of its first reading, its gloss every reading's, and its `tone` the stroke
    group. A section's opening note is an entry with an empty field and `tone` "note"."""
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
                entries[k] = Entry(first.row, first.field, first.gloss + values["注文"], first.volume, first.section, first.tone,
                                   page, first.note + values["校勘记"])
                continue
            held[page, place] = len(entries)
            entries.append(Entry(row, _field(values["字头"].strip()), values["注文"], values["卷序"], values["部首"],
                                 values["笔画（推导）"].strip(), page, values["校勘记"]))
    return pages


def slots(entries: list[Entry]) -> list[Slot]:
    """What a page prints in order: its headwords, a place for each section's title and opening note, and a stroke count where it changes."""
    out: list[Slot] = []
    before: Entry | None = None
    titled = None
    for entry in entries:
        if entry.tone == "note":
            out.append(Slot(None, TITLE + longkan.length(entry.gloss), -1))
            titled = entry.section
            continue
        if before is not None and entry.section != before.section and entry.section != titled:
            out.append(Slot(None, TITLE, -1))
        elif before is not None and entry.section == before.section and entry.tone != before.tone and entry.tone not in ("", UNMARKED):
            out.append(Slot(None, HEADER, -1))
        before = entry
        characters = longkan.fields(entry)
        for k, char in enumerate(characters):
            out.append(Slot(char, longkan.length(entry.gloss) if k == len(characters) - 1 else 0, entry.row, k))
    return out


def places(pages: dict[int, list[Entry]]) -> dict[int, int]:
    """Each headword's row by its place among the book's printed headwords."""
    rows = sorted(entry.row for group in pages.values() for entry in group if entry.tone != "note")
    return {row: k for k, row in enumerate(rows)}


def unsettled(pages: dict[int, list[Entry]], neighbours: bool = True) -> set[tuple[int, int]]:
    """`longkan.unsettled`, with neighbours counted in printed headwords."""
    return longkan.unsettled({page: [e for e in group if e.tone != "note"] for page, group in pages.items()}, neighbours, places(pages))


def kin(pages: dict[int, list[Entry]]) -> dict[tuple[int, int], set[tuple[int, int]]]:
    """`longkan.kin`, with neighbours counted in printed headwords."""
    return longkan.kin({page: [e for e in group if e.tone != "note"] for page, group in pages.items()}, places(pages))
