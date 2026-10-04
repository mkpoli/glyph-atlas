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


Box = tuple[float, float, float, float]


def _near_boxes(a: Box, b: Box) -> bool:
    """Whether two boxes, (x, y, w, h), stand close enough to be neighbours on a line (`REACH`)."""
    ax, ay, bx, by = a[0] + a[2] / 2, a[1] + a[3] / 2, b[0] + b[2] / 2, b[1] + b[3] / 2
    reach = REACH * max(a[2], a[3], b[2], b[3])
    return (ax - bx) ** 2 + (ay - by) ** 2 <= reach ** 2


def _near(a: Unit, b: Unit) -> bool:
    return _near_boxes((a.box.x, a.box.y, a.box.w, a.box.h), (b.box.x, b.box.y, b.box.w, b.box.h))


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


class Glyph(NamedTuple):
    """A located glyph of a corpus: its line, its position on it, its box and the text it transcribes.
    `wraps` marks an order that runs on from one column to the next, as a CODH block's does: the next
    glyph then follows only where it stands below and in the same column."""
    id: str
    line: str
    seq: int
    box: Box
    text: str
    wraps: bool = False


class Pair(NamedTuple):
    """Two corpus glyphs that follow each other on a line, the text they make, and whether the second
    stands below the first rather than beside it."""
    first: str
    second: str
    text: str
    vertical: bool


def glyph_pairs(glyphs: Iterable[Glyph]) -> list[Pair]:
    """Each glyph and the one at the next position on its line, under the rules `adjacent_ngrams`
    keeps: a position with no glyph, or with two, breaks the line there, and so do two neighbours too
    far apart on the page. The caller passes only glyphs that can be part of a run."""
    lines: dict[str, dict[int, list[Glyph]]] = defaultdict(lambda: defaultdict(list))
    for glyph in glyphs:
        lines[glyph.line][glyph.seq].append(glyph)
    pairs = []
    for positions in lines.values():
        held = {seq: found[0] for seq, found in positions.items() if len(found) == 1}
        for seq, glyph in sorted(held.items()):
            following = held.get(seq + 1)
            if not following or not _near_boxes(glyph.box, following.box):
                continue
            (ax, ay, aw, ah), (bx, by, bw, bh) = glyph.box, following.box
            dx, dy = bx + bw / 2 - ax - aw / 2, by + bh / 2 - ay - ah / 2
            if (glyph.wraps or following.wraps) and not (dy > 0 and abs(dx) < max(aw, bw)):
                continue
            pairs.append(Pair(glyph.id, following.id, glyph.text + following.text, abs(dy) >= abs(dx)))
    return pairs


def corpus_pair_statements(glyphs: Iterable[str], pairs: list[Pair], batch: int = 500) -> list[str]:
    """D1 statements that make `pairs` the pairs starting at `glyphs`, as the site holds them.

    Every pair starting at one of `glyphs` is removed first, so a glyph whose neighbour changed keeps no
    stale pair. A pair is then recorded only when both its glyphs are published (`corpus_units`)."""
    quote = lambda value: "'" + value.replace("'", "''") + "'"
    ids = sorted(set(glyphs))
    statements = [
        "DELETE FROM corpus_ngrams WHERE first IN (" + ",".join(map(quote, ids[start:start + batch])) + ");"
        for start in range(0, len(ids), batch)
    ]
    head = ("INSERT OR IGNORE INTO corpus_ngrams(first,second,text,vertical) "
            "SELECT p.column1,p.column2,p.column3,p.column4 FROM (VALUES ")
    tail = ") AS p JOIN corpus_units a ON a.id=p.column1 JOIN corpus_units b ON b.id=p.column2;"
    statements += [
        head + ",".join(f"({quote(p.first)},{quote(p.second)},{quote(p.text)},{int(p.vertical)})" for p in pairs[start:start + batch]) + tail
        for start in range(0, len(pairs), batch)
    ]
    return statements
