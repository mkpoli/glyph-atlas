"""龍龕手鏡's headwords by the page of the 宋刻本, as 青雀古籍's table gives them.

The table (`data/sources/longkan-shoujing.yaml`) has one row per entry: the headword, or several headwords
that share a gloss, its gloss, its tone and section, and the half-leaf of each edition it stands on. A
headword is blank in the table where no code point was found. The text was roughly proofread and the
origin of its first transcription is untraced, as its repository says: labels are the table's.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

from .tape import Slot

#: What a gloss is counted without: the modern punctuation of the table, not printed.
_UNPRINTED = re.compile(r"[，。、；：？！《》「」『』〔〕（）()\[\]【】〈〉\s*/<>①②③④⑤…·]")
_IDS = re.compile(r"[⿰-⿻<]")
#: Words printed small between the headwords of one field: 鈗俗鈂 is two headwords.
_NOTES = set("俗正今古通或誤籀")
#: A section title 某部第幾 is printed at about this many gloss characters' length; a tone header in its own cartouche.
TITLE, HEADER = 4, 0


@dataclass(frozen=True)
class Entry:
    """One table row: its number (the first data row is 1), headword field as written, gloss, volume, section."""

    row: int
    field: str
    gloss: str
    volume: str
    section: str
    tone: str
    page: int


def read(table: Path, edition: str) -> dict[int, list[Entry]]:
    """The entries of the table by the half-leaf number `edition` gives them, in the table's order."""
    pages: dict[int, list[Entry]] = {}
    with table.open(encoding="utf-8-sig", newline="") as handle:
        for row, values in enumerate(csv.DictReader(handle), 1):
            if values[edition].isdigit():
                entry = Entry(row, values["字头"].strip(), values["注文"], values["卷序"], values["部首"], values["四声"], int(values[edition]))
                pages.setdefault(entry.page, []).append(entry)
    return pages


def length(gloss: str) -> int:
    """How many characters a gloss prints."""
    return len(_UNPRINTED.sub("", gloss))


def fields(entry: Entry) -> list[str | None]:
    """The headwords a row's field stands for: one per printed headword, None where the table leaves it blank.

    Several characters in one field are headwords printed one after another with a gloss after the last.
    """
    if not entry.field:
        return [None]
    if _IDS.search(entry.field):
        return [entry.field]
    characters = [c for c in entry.field if c != "*"]
    return [c for c in characters if not (len(characters) > 1 and c in _NOTES)] or characters


def slots(entries: list[Entry]) -> list[Slot]:
    """What a page prints in order: its headwords, and a place for each section title and tone header between them."""
    out: list[Slot] = []
    before: Entry | None = None
    for entry in entries:
        if before is not None and entry.section != before.section:
            out.append(Slot(None, TITLE, -1))
        elif before is not None and entry.tone != before.tone:
            out.append(Slot(None, HEADER, -1))
        before = entry
        characters = fields(entry)
        for k, char in enumerate(characters):
            out.append(Slot(char, length(entry.gloss) if k == len(characters) - 1 else 0, entry.row, k))
    return out
