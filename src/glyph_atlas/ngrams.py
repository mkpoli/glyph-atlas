"""Runs of consecutive characters on a line, the occurrences two- and three-character frequencies count."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Container, Iterable
from typing import NamedTuple

from .schema import Unit, UnitKind

#: The lengths counted: pairs and trigrams. `unit_ngrams` holds a column for each member.
SIZES = (2, 3)
COLUMNS = ("first", "second", "third")


class Run(NamedTuple):
    """The ids of consecutive crops in reading order, and whether their line is written down the page.
    A line written across is read left to right: the review store numbers its units by `x`."""
    units: tuple[str, ...]
    vertical: bool


# Two neighbours in `seq` order whose centres lie further apart than this many of the larger box's
# longer side have ink between them that no unit covers, most often a character the detector missed.
REACH = 1.6


def _centre(unit: Unit) -> tuple[float, float]:
    return unit.box.x + unit.box.w / 2, unit.box.y + unit.box.h / 2


def _near(a: Unit, b: Unit) -> bool:
    (ax, ay), (bx, by) = _centre(a), _centre(b)
    reach = REACH * max(a.box.w, a.box.h, b.box.w, b.box.h)
    return (ax - bx) ** 2 + (ay - by) ** 2 <= reach ** 2


def adjacent_ngrams(units: Iterable[Unit], horizontal: Container[str] = frozenset()) -> list[Run]:
    """Each active character unit and the units at the next positions on its line, as runs of every
    length in `SIZES`. A line is vertical unless its id is in `horizontal`, as `Line.vertical` is.

    Each unit's `seq` is the one before it plus one: a position with no unit, or with two, breaks the
    run there. A gap, an unreadable unit, a mark or an unsegmented run is never part of one, and neither
    is a unit without a box. Two neighbours too far apart on the page break the run as well.
    """
    lines: dict[str, dict[int, list[Unit]]] = defaultdict(lambda: defaultdict(list))
    for unit in units:
        if unit.active and unit.line_id and unit.seq is not None:
            lines[unit.line_id][unit.seq].append(unit)
    ngrams = []
    for line, positions in lines.items():
        # The positions held by one unit that can be part of a run; any other position breaks it.
        held = {seq: found[0] for seq, found in positions.items() if len(found) == 1
                and found[0].kind == UnitKind.CHAR and found[0].granularity == "char" and found[0].box}
        for seq, unit in sorted(held.items()):
            run = [unit]
            while len(run) < max(SIZES) and (following := held.get(seq + len(run))) and _near(run[-1], following):
                run.append(following)
            ngrams += [Run(tuple(u.id for u in run[:size]), line not in horizontal) for size in SIZES if len(run) >= size]
    return ngrams


def ngram_statements(units: Iterable[str], ngrams: list[Run], batch: int = 200) -> list[str]:
    """D1 statements that make `ngrams` the runs starting at `units`, as the site holds them.

    Every run starting at one of `units` is removed first, so a unit whose successors changed or went
    away since an earlier publication keeps no stale run. A run is then recorded only when all of its
    crops are on the site as its own; its text and book are read from the rows the site holds, whose
    labels may have been reviewed since. The triggers of migration 0043 keep them in step; a run's
    direction is its line's, which no review changes.
    """
    quote = lambda value: "'" + value.replace("'", "''") + "'"
    ids = sorted(set(units))
    statements = [
        "DELETE FROM unit_ngrams WHERE first IN (" + ",".join(map(quote, ids[start:start + batch])) + ");"
        for start in range(0, len(ids), batch)
    ]
    for size in SIZES:
        runs = [run for run in ngrams if len(run.units) == size]
        crops = [f"u{i}" for i in range(size)]
        head = (f"INSERT OR IGNORE INTO unit_ngrams(size,{','.join(COLUMNS[:size])},vertical,text,document) "
                f"SELECT {size},{','.join(f'p.column{i + 1}' for i in range(size))},p.column{size + 1},"
                f"{'||'.join(f'{u}.character' for u in crops)},u0.document FROM (VALUES ")
        joins = "".join(f" JOIN units {u} ON {u}.id=p.column{i + 1} AND {u}.origin='local'" for i, u in enumerate(crops))
        statements += [
            head + ",".join(f"({','.join(map(quote, run.units))},{int(run.vertical)})" for run in runs[start:start + batch])
            + ") AS p" + joins + ";"
            for start in range(0, len(runs), batch)
        ]
    return statements
