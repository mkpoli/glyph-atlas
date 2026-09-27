"""Adjacent character pairs on a line, the occurrences a two-character frequency counts."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from .schema import Unit, UnitKind

# Two neighbours in `seq` order whose centres lie further apart than this many of the larger box's
# longer side have ink between them that no unit covers, most often a character the detector missed.
REACH = 1.6


def _centre(unit: Unit) -> tuple[float, float]:
    return unit.box.x + unit.box.w / 2, unit.box.y + unit.box.h / 2


def _near(a: Unit, b: Unit) -> bool:
    (ax, ay), (bx, by) = _centre(a), _centre(b)
    reach = REACH * max(a.box.w, a.box.h, b.box.w, b.box.h)
    return (ax - bx) ** 2 + (ay - by) ** 2 <= reach ** 2


def adjacent_pairs(units: Iterable[Unit]) -> list[tuple[str, str]]:
    """Each active character unit and the one at the next position on its line, as `(first, second)` ids.

    The second unit's `seq` is the first's plus one: a position with no unit, or with two, leaves its
    neighbours unpaired. A gap, an unreadable unit, a mark or an unsegmented run is never half of a
    pair, and neither is a unit without a box. Two units too far apart to be neighbours on the page are
    not paired either.
    """
    lines: dict[str, dict[int, list[Unit]]] = defaultdict(lambda: defaultdict(list))
    for unit in units:
        if unit.active and unit.line_id and unit.seq is not None:
            lines[unit.line_id][unit.seq].append(unit)
    pairs = []
    for positions in lines.values():
        for seq, (a, *others) in sorted(positions.items()):
            following = positions.get(seq + 1, [])
            if others or len(following) != 1:
                continue
            b = following[0]
            if all(u.kind == UnitKind.CHAR and u.granularity == "char" and u.box for u in (a, b)) and _near(a, b):
                pairs.append((a.id, b.id))
    return pairs


def pair_inserts(pairs: list[tuple[str, str]], batch: int = 200) -> list[str]:
    """D1 statements that record `pairs` whose two units the site holds as its own crops.

    A pair's text and book are read from the rows the site holds, whose labels may have been reviewed
    since publication. A pair already recorded keeps its row; the triggers of migration 0028 keep its
    text in step with its units.
    """
    quote = lambda value: "'" + value.replace("'", "''") + "'"
    return [
        "INSERT OR IGNORE INTO unit_pairs(first,second,text,document) "
        "SELECT p.column1,p.column2,a.character||b.character,a.document FROM (VALUES "
        + ",".join(f"({quote(first)},{quote(second)})" for first, second in pairs[start:start + batch])
        + ") AS p JOIN units a ON a.id=p.column1 AND a.origin='local' JOIN units b ON b.id=p.column2 AND b.origin='local';"
        for start in range(0, len(pairs), batch)
    ]
