"""龍龕手鏡's headwords by the page of the 宋刻本, as 青雀古籍's table gives them.

The table (`data/sources/longkan-shoujing.yaml`) has one row per entry: the headword, or several headwords
that share a gloss, its gloss, its tone and section, and the half-leaf of each edition it stands on. A
headword is blank in the table where no code point was found. The text was roughly proofread and the
origin of its first transcription is untraced, as its repository says; every row's status in the file is a
first proofreading by a language model (AI初校). Labels are the table's, and `unsettled` names the rows whose
headword the table itself makes doubtful.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

from . import han_components
from .tape import Slot

#: What a gloss is counted without: the modern punctuation of the table, not printed.
_UNPRINTED = re.compile(r"[，。、；：？！《》「」『』〔〕（）()\[\]【】〈〉\s*/<>①②③④⑤…·]")
_IDS = re.compile(r"[⿰-⿻<]")
#: Words printed small between the headwords of one field: 鈗俗鈂 is two headwords.
_NOTES = set("俗正今古通或誤籀")
#: The written form of a radical the table names by another character.
WRITTEN = {"草": "艹", "四": "罒", "𠕀": "罒", "系": "糹", "䖝": "虫", "黒": "黑", "阜": "阝", "曰": "日", "䑕": "鼠", "𤓰": "瓜",
           "髙": "高", "文": "攵", "反": "歹", "歺": "歹", "尤": "尢", "元": "尢", "西": "襾", "䜴": "鼓", "學": "𦥯", "倝": "𠦝", "卄": "廾",
           "氣": "气", "凢": "凡"}
#: A section is tested for its radical when this share of its headwords hold it.
TESTED = 2 / 3
#: A note's items are numbered ①②…; one that names the headword and says either of these fills it in or doubts it.
_ITEMS = re.compile(r"[①-⑳]")
_DOUBT = ("原空", "原錄", "疑", "待核")
#: How many rows before and after a headword a second one written with the same character makes it doubtful.
NEAR = 2
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
    note: str = ""


def read(table: Path, edition: str) -> dict[int, list[Entry]]:
    """The entries of the table by the half-leaf number `edition` gives them, in the table's order."""
    pages: dict[int, list[Entry]] = {}
    with table.open(encoding="utf-8-sig", newline="") as handle:
        for row, values in enumerate(csv.DictReader(handle), 1):
            if values[edition].isdigit():
                entry = Entry(row, values["字头"].strip(), values["注文"], values["卷序"], values["部首"], values["四声"], int(values[edition]), values["校勘记"])
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


def _holds(char: str, section: str) -> bool:
    """Whether a character is its section's radical or names it at the top level of its description."""
    names = {section, WRITTEN.get(section, section)}
    return char in names or bool(names & han_components.direct(char))


def _doubted(note: str) -> bool:
    """Whether a note says the headword was filled in or is in doubt; one about the gloss alone does not."""
    return any("字頭" in item and any(word in item for word in _DOUBT) for item in _ITEMS.split(note))


def unsettled(pages: dict[int, list[Entry]]) -> set[tuple[int, int]]:
    """The headwords, as row and place in it, whose character is no sure reading of what the print shows.

    - The table writes 俗, 正 and 通 forms that stand side by side with one character, although the print shows a
      different shape for each.
    - A note on a headword says it was blank and filled in, or doubts it.
    - The character lacks the radical of its section (聴 under 口, where the print has 口 beside it). A section is
      tested when most of its headwords hold the radical; 雜部 and the like hold none.
    """
    entries = {entry.row: entry for group in pages.values() for entry in group}
    heads = {row: fields(entry) for row, entry in entries.items()}
    described: dict[str, list[bool]] = {}
    for row, entry in entries.items():
        described.setdefault(entry.section, []).extend(_holds(c, entry.section) for c in heads[row] if c and len(c) == 1)
    tested = {section for section, held in described.items() if sum(held) >= TESTED * len(held)}
    out = set()
    for row, entry in entries.items():
        own = [c for c in heads[row] if c]
        near = {c for k in range(row - NEAR, row + NEAR + 1) if k != row for c in heads.get(k, ()) if c}
        doubted = len(set(own)) < len(own) or bool(near & set(own)) or _doubted(entry.note)
        for sub, char in enumerate(heads[row]):
            foreign = char is not None and len(char) == 1 and entry.section in tested and not _holds(char, entry.section)
            if doubted or foreign:
                out.add((row, sub))
    return out
