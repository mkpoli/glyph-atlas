"""Characters of another script that a grapheme is also written with (`data/vocab/written-with.yaml`).

The 異体字 graph relates ideographs. A kana ligature that stands for a word a kanji writes (ヿ for 事)
is listed here with what states it, and a grapheme's card shows it among its 字体 as a shared form.
The Worker reads the same table as `writtenWithData.ts`, which `scripts/build_written_with.py` writes.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from pathlib import Path

import yaml

TABLE = Path(__file__).resolve().parents[2] / "data" / "vocab" / "written-with.yaml"


@dataclass(frozen=True)
class Writer:
    """One character and the grapheme it also writes: both as characters, the relation's name, and
    each statement of it as (source, detail)."""

    char: str
    grapheme: str
    relation: str
    evidence: tuple[tuple[str, str], ...]

    @property
    def code_point(self) -> str:
        return f"U+{ord(self.char):04X}"

    @property
    def relations(self) -> list[dict[str, str]]:
        return [{"relation": self.relation, "source": source, "detail": detail} for source, detail in self.evidence]


def _char(code_point: str) -> str:
    digits = code_point.removeprefix("U+")
    if digits == code_point:
        raise ValueError(f"{code_point!r} is not a code point")
    return chr(int(digits, 16))


@cache
def _table() -> tuple[dict[str, str], dict[str, Writer]]:
    data = yaml.safe_load(TABLE.read_text(encoding="utf-8"))
    sources = {key: " ".join(text.split()) for key, text in data["sources"].items()}
    writers: dict[str, Writer] = {}
    for code_point, row in data["writers"].items():
        evidence = tuple((item["source"], item["detail"]) for item in row.get("evidence") or ())
        if not evidence:
            raise ValueError(f"{code_point} writes {row['grapheme']} on no evidence")
        if unknown := [source for source, _ in evidence if source not in sources]:
            raise ValueError(f"{code_point} cites {unknown[0]}, which written-with.yaml does not describe")
        char = _char(code_point)
        writers[char] = Writer(char, _char(row["grapheme"]), row["relation"], evidence)
    return sources, writers


def sources() -> dict[str, str]:
    """Each source the table cites, by its id."""
    return dict(_table()[0])


def writers(grapheme: str) -> list[Writer]:
    """The characters of another script a grapheme, named by its head, is also written with, by code point."""
    return sorted((writer for writer in _table()[1].values() if writer.grapheme == grapheme), key=lambda writer: writer.char)


def writes(char: str) -> Writer | None:
    """The grapheme a character also writes by this table, or None."""
    return _table()[1].get(char)
