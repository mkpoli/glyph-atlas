"""Runs of consecutive characters on a line, the occurrences two- and three-character frequencies count."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Container, Iterable, Iterator
from typing import Any, NamedTuple

from .schema import Unit, UnitKind

#: The lengths counted: pairs and trigrams. `unit_ngrams` holds a column for each member.
SIZES = (2, 3)
COLUMNS = ("first", "second", "third")


class Run(NamedTuple):
    """The ids of consecutive crops in reading order, and whether their line is written down the page.
    A line written across is read left to right: the review store numbers its units by `x`."""
    units: tuple[str, ...]
    vertical: bool


class Place(NamedTuple):
    x: float
    y: float
    w: float
    h: float


class Glyph(NamedTuple):
    """What `adjacent_ngrams` reads of a unit, from a row of a corpus's units table: a corpus holds a
    million of them, too many to validate as `Unit`s."""
    id: str
    line_id: str | None
    seq: int | None
    kind: str
    granularity: str
    box: Place | None
    active: bool

    @classmethod
    def of(cls, row: dict[str, Any]) -> Glyph:
        box = row.get("box")
        return cls(row["id"], row.get("line_id"), row.get("seq"), row.get("kind") or "char",
                   row.get("granularity") or "char", Place(box["x"], box["y"], box["w"], box["h"]) if box else None,
                   row.get("active") is not False)


# Two neighbours in `seq` order whose centres lie further apart than this many of the larger box's
# longer side have ink between them that no unit covers, most often a character the detector missed.
REACH = 1.6


def _centre(unit: Unit | Glyph) -> tuple[float, float]:
    return unit.box.x + unit.box.w / 2, unit.box.y + unit.box.h / 2


def _near(a: Unit | Glyph, b: Unit | Glyph) -> bool:
    (ax, ay), (bx, by) = _centre(a), _centre(b)
    reach = REACH * max(a.box.w, a.box.h, b.box.w, b.box.h)
    return (ax - bx) ** 2 + (ay - by) ** 2 <= reach ** 2


def _down_the_column(a: Unit | Glyph, b: Unit | Glyph) -> bool:
    """Whether `b` continues `a`'s column: below it, and less than a character's width aside."""
    (ax, ay), (bx, by) = _centre(a), _centre(b)
    return by > ay and abs(bx - ax) < max(a.box.w, b.box.w)


def adjacent_ngrams(units: Iterable[Unit | Glyph], horizontal: Container[str] = frozenset(),
                    columns: Container[str] = frozenset()) -> list[Run]:
    """Each active character unit and the units at the next positions on its line, as runs of every
    length in `SIZES`. A line is vertical unless its id is in `horizontal`, as `Line.vertical` is.

    Each unit's `seq` is the one before it plus one: a position with no unit, or with two, breaks the
    run there. A gap, an unreadable unit, a mark or an unsegmented run is never part of one, and neither
    is a unit without a box. Two neighbours too far apart on the page break the run as well.

    A line in `columns` holds several columns read one after another, as a CODH block does: it steps
    from the foot of one column to the head of the next, and with small characters that step can fall
    within `REACH`. On such a line a neighbour that does not stand below in the same column breaks the
    run too.
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
        wraps = line in columns
        for seq, unit in sorted(held.items()):
            run = [unit]
            while (len(run) < max(SIZES) and (following := held.get(seq + len(run))) and _near(run[-1], following)
                   and (not wraps or _down_the_column(run[-1], following))):
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


def in_reading_order(glyphs: Iterable[Glyph], horizontal: Container[str] = frozenset()) -> list[Glyph]:
    """`glyphs` with each line's boxed glyphs numbered in the order the line is read on the page.

    A detect-align dataset written before 2026-09-24 numbered a vertical line's units in an order
    close to random down the column (`glyph_atlas.box_relabel`); its repair keeps every id on its
    box, so the boxes, not the numbers, say which glyphs follow each other. A vertical line is read
    as `align.reading_order` reads one, a horizontal one left to right. Glyphs sharing a box share
    its place, which no run passes through, and a glyph without a box has no place.
    """
    from . import align
    from .schema import Box

    lines: dict[str, list[Glyph]] = defaultdict(list)
    for glyph in glyphs:
        if glyph.line_id and glyph.box:
            lines[glyph.line_id].append(glyph)
    placed = []
    for line, found in lines.items():
        boxes = sorted({glyph.box for glyph in found})
        detections = [align.Detection(box=Box(x=b.x, y=b.y, w=b.w, h=b.h), score=1.0) for b in boxes]
        ordered = (sorted(detections, key=lambda d: d.centre) if line in horizontal
                   else align.reading_order(detections))
        rank = {Place(d.box.x, d.box.y, d.box.w, d.box.h): i for i, d in enumerate(ordered)}
        placed += [glyph._replace(seq=rank[glyph.box]) for glyph in found]
    return placed


#: A corpus glyph's character as the site holds it: the one a round or review gave it (its `units` row)
#: or, while it has none, its published row's, which a form decision moves (`corpus_units`).
def _written(i: int) -> str:
    return f"iif(u{i}.id IS NULL,c{i}.character,u{i}.character)"


def corpus_ngram_statements(ranges: Iterable[tuple[str, str]], ngrams: list[Run], batch: int = 200) -> list[str]:
    """D1 statements that make `ngrams` the runs of corpus glyphs starting in the id `ranges`.

    The runs starting in each range, an inclusive pair of corpus glyph ids, are removed first, so a
    publication may be applied again after the lines were cut anew. A run is then recorded only while
    every glyph of it is published (`corpus_units`), so one with a glyph of a withdrawn document or one
    the corpus publication left out is never recorded; its text is its glyphs' characters as the site
    holds them and its document its first glyph's.
    """
    quote = lambda value: "'" + value.replace("'", "''") + "'"
    statements = [f"DELETE FROM unit_ngrams WHERE first>={quote(low)} AND first<={quote(high)};" for low, high in ranges]
    for size in SIZES:
        runs = [run for run in ngrams if len(run.units) == size]
        head = (f"INSERT OR IGNORE INTO unit_ngrams(size,{','.join(COLUMNS[:size])},vertical,text,document) "
                f"SELECT {size},{','.join(f'p.column{i + 1}' for i in range(size))},p.column{size + 1},"
                f"{'||'.join(_written(i) for i in range(size))},c0.document FROM (VALUES ")
        joins = "".join(f" JOIN corpus_units c{i} ON c{i}.id=p.column{i + 1} LEFT JOIN units u{i} ON u{i}.id=c{i}.id"
                        for i in range(size))
        statements += [
            head + ",".join(f"({','.join(map(quote, run.units))},{int(run.vertical)})" for run in runs[start:start + batch])
            + ") AS p" + joins + ";"
            for start in range(0, len(runs), batch)
        ]
    return statements


def id_ranges(ids: Iterable[str], size: int) -> Iterator[tuple[str, str]]:
    """Sorted `ids` as inclusive ranges of at most `size` ids each."""
    ordered = sorted(ids)
    for start in range(0, len(ordered), size):
        yield ordered[start], ordered[min(start + size, len(ordered)) - 1]
