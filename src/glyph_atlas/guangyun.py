"""The headwords of the 澤存堂本 廣韻 as nk2028's tables place them on the printed leaves.

`韻書/廣韻.csv` holds every entry with its gloss; `src/字序表.csv` gives each entry's `sbgy_id`: volume,
leaf, side, column (from the right) and position in the column. A headword may carry a collation
mark: `A〈B〉` is a printed A the editors correct to B, `｛X｝` a printed X they delete, `［X］` a
character they supply that the leaf does not print. The leaf is cut as printed, so A and X are the
headwords there and a supplied character is none; a headword Unicode lacks is written as an IDS.

A gloss is counted in characters as the table gives it. The gloss of a 小韻's first headword already
ends with its 反切, 切 and the count of the 小韻, so nothing is added for them.
"""

from __future__ import annotations

import csv
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from .leaf_text import Head

#: An `sbgy_id`: w, volume, leaf, side, column, position.
_PLACE = re.compile(r"^w(?P<volume>\d)(?P<leaf>\d\d)(?P<side>[ab])(?P<column>\d\d)(?P<position>\d\d)")
#: The collation marks of the tables, which are not printed.
_MARKS = re.compile(r"[〈〉［］｛｝（）〖〗｟｠〘〙]")
_CORRECTED = re.compile(r"^(?P<printed>[^〈〉]+)〈(?P<corrected>[^〈〉]+)〉$")
_DELETED = re.compile(r"^｛(?P<printed>[^｛｝]+)｝$")
_SUPPLIED = re.compile(r"^［[^［］]+］$")


@dataclass(frozen=True)
class Entry:
    """One printed headword: its place in its column, its id, the character on the leaf with its
    gloss length, the form the editors correct it to (if they do), and its row of 廣韻.csv."""

    position: int
    entry_id: str
    head: Head
    corrected: str | None
    row: dict[str, str]

    @property
    def encoded(self) -> bool:
        """Whether the printed headword is one encoded character, not a description."""
        return len(self.head.char) == 1


def printed(headword: str) -> tuple[str, str | None] | None:
    """The form a table headword has on the leaf, and the form it is corrected to; None for a supplied one."""
    if _SUPPLIED.match(headword):
        return None
    corrected = _CORRECTED.match(headword)
    if corrected:
        return corrected["printed"], corrected["corrected"]
    deleted = _DELETED.match(headword)
    return (deleted["printed"], None) if deleted else (headword, None)


def read_entries(table: Path) -> dict[tuple[str, int, str], dict[int, list[Entry]]]:
    """Every printed headword by half-leaf (volume, leaf, side) and column, in the column's order."""
    glosses: dict[tuple[str, str], tuple[int, dict[str, str]]] = {}
    with (table / "韻書" / "廣韻.csv").open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            glosses[(row["小韻號"].rstrip("abc"), row["小韻字號"])] = (len(_MARKS.sub("", row["釋義"])), row)
    found: dict[tuple[str, int, str], dict[int, list[Entry]]] = defaultdict(lambda: defaultdict(list))
    with (table / "src" / "字序表.csv").open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            place = _PLACE.match(row["sbgy_id"])
            form = printed(row["字"])
            if not place or form is None:
                continue
            length, entry = glosses.get((row["原書小韻號"], row["小韻字號"]), (0, {}))
            found[(place["volume"], int(place["leaf"]), place["side"])][int(place["column"])].append(
                Entry(int(place["position"]), row["sbgy_id"], Head(form[0], length), form[1], entry))
    for columns in found.values():
        for entries in columns.values():
            entries.sort(key=lambda entry: entry.position)
    return found
