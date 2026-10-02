"""Headwords of the Heian dictionaries on NDL facsimiles, placed from the HDIC databases.

HDIC (`data/sources/hdic-*.yaml`) transcribes every entry of a dictionary and gives its place on a
page of a printed edition: the page (or half-leaf), the column counted from the right, and where the
layout has them, the tier and the order inside it. `DICTIONARIES` names, for each one, how its entries
and frames are read and the `Layout` of its pages:

- KRM, 観智院本類聚名義抄: eight columns of four tiers, each tier opening with a headword
  (`read_krm`). A frame of the facsimile (貴重図書複製会, 1937) shows two pages of the 天理 edition.
- KTB, 高山寺本篆隷萬象名義: six columns per half-leaf, each holding the upper and the lower entry
  of the manuscript one after another (`read_ktb`), on the 崇文叢書 edition.
- TSJ, 天治本新撰字鏡: eight columns per half-leaf, entries running on down each column
  (`read_tsj`), on the 六合館 edition.

A headword slot is one written character: the written form where HDIC gives one, else the collated
form, with the collated form as the glyph's standard when the two differ. A repetition mark
(`ー（X）`, `｜`, `〻`) has `Glyph.text` `MARK`; `［X］`, a character the editors supply, has no glyph.
IDS sequences, CHISE and GlyphWiki references and a form with a Greek suffix (`僕β`) describe a
character Unicode lacks, and `■` is one nobody could read.

`page_grids` and `place` find the headword boxes among a frame's detected character boxes:

1. Headword-sized boxes (`BIG`, relative to the page's median box) are kept; a thin tall box is a
   repetition mark (`is_mark`).
2. The right page is anchored on the frame's rightmost column of headwords and the left page on
   its leftmost, since the gutter between them may be as narrow as a column or several wide. `fit`
   finds each page's column pitch, and for a tiered layout the tiers over its headwords' tops, their
   pitch held to `TIER_PITCH` times the column pitch. A grid stands only when enough of its columns
   (`Layout.held`) and every tier line hold headwords. A layout of one tier takes each column whole.
3. In each cell (a column, or one tier of it), `align_cell` pairs the cell's headword glyphs, in HDIC
   order, with its candidate boxes top to bottom by least cost, the classifier's five best classes
   deciding whether a glyph and a box agree (`glossary.agrees`). Glyphs and boxes may go unpaired:
   a mark the detector missed, a large gloss character.

A pair is kept when the classifier reads the glyph there. A glyph the classifier cannot judge (a
description, or a character it has no class for) is kept when no pair of its cell was refused and
its place admits no doubt: every written glyph of the cell sits on its own headword box with no box
left over, and another glyph of the cell was read where it was placed or the cell's first box stands
at the tier line; or the glyph is the cell's first headword character on the cell's first box, at
the tier line; or the glyphs before and after it in the cell were both read, on the boxes either
side of its own. A pair the classifier refuses is left out, and a page on which more than
`REFUSED_SHARE` of the judged pairs are refused is left out whole, since that is how a misfitted grid
looks. The caller tries a frame's pages on its two grids, one page to a grid (`assign_pages`).
The label always comes from HDIC, never the classifier.
"""

from __future__ import annotations

import csv
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from itertools import permutations
from pathlib import Path
from typing import Any

import numpy as np

from .glossary import Glyph, agrees
from .schema import Box, Dating, Document, Licence, Register, Rights

#: `Glyph.text` of a repetition mark.
MARK = "ー"
#: How HDIC writes a repetition mark.
MARKS = frozenset({"ー", "｜", "〻"})
#: How HDIC writes a character nobody could read.
UNREADABLE = "■"
#: A headword box's longer side, as a range of multiples of the page's median box side.
BIG = (1.5, 3.5)
#: Most of a page's judged pairs the classifier may refuse before the page is left out.
REFUSED_SHARE = 0.25
#: How far from its tier line a cell's first box may stand and still anchor the cell, in tier pitches.
ANCHOR = 0.2
#: The tier pitch, as a range of multiples of the column pitch. Half or twice the true spacing fits
#: a page's headword tops almost as well as the spacing itself, and falls outside this range.
TIER_PITCH = (2.5, 4.5)
#: Least number of headword tops on each tier line for the tiers to stand.
TIER_HELD = 2

_STANDARD = re.compile(r"[（(]([^）)]*)[）)]")


@dataclass(frozen=True)
class Layout:
    """The grid of a dictionary's pages: columns, tiers per column, and how many columns must hold a headword."""

    columns: int
    tiers: int
    held: int


@dataclass(frozen=True)
class Entry:
    """One HDIC entry: the page it stands on, its column, tier and order there, and its headword glyphs.

    `page` is the dictionary's own page key (a 天理 page number, a half-leaf id); `location` sorts the
    entries of a cell in reading order; `meta` holds the dictionary's own fields for the line record.
    """

    entry_id: str
    volume: str
    page: Any
    line: int
    segment: int
    order: int
    location: str
    glyphs: tuple[Glyph, ...]
    meta: dict[str, str] = field(default_factory=dict, hash=False, compare=False)


def read_tsv(path: Path) -> list[dict[str, str]]:
    """The rows of an HDIC TSV, skipping its `#` header."""
    with Path(path).open(encoding="utf-8", newline="") as handle:
        body = [line for line in handle if not line.startswith("#")]
    return list(csv.DictReader(body, delimiter="\t"))


def slot_glyph(written: str, collated: str) -> Glyph | None:
    """The glyph a headword slot names, or None for a character the editors supply."""
    standard = _STANDARD.search(written)
    text = _STANDARD.sub("", written).strip()
    if text.startswith("［") or not text:
        return None
    if text in MARKS:
        return Glyph(MARK, standard.group(1) if standard else None)
    if text == UNREADABLE:
        return Glyph("〓")
    clean = _STANDARD.sub("", collated).strip()
    return Glyph(text, clean if clean and clean != text else None)


def headword(collated: str, written: str, *, same: frozenset[str] = frozenset({"", "〇"})) -> tuple[Glyph, ...]:
    """The written glyphs of a headword whose slots are separated by ／, in order.

    `written` in `same` (or a slot of it in `same`) means the written form is the collated one.
    """
    collated_slots = collated.split("／")
    written_slots = written.split("／") if written not in same else collated_slots
    if len(written_slots) != len(collated_slots):
        written_slots = collated_slots
    out = []
    for slot, base in zip(written_slots, collated_slots, strict=True):
        glyph = slot_glyph(base if slot in same else slot, base)
        if glyph is not None:
            out.append(glyph)
    return tuple(out)


def encoded(glyph: Glyph) -> str | None:
    """The code point of a glyph that is one encoded character, as `U+XXXX`."""
    if glyph.text == MARK or not glyph.encoded or len(glyph.text) != 1:
        return None
    return f"U+{ord(glyph.text):04X}"


Frames = dict[tuple[str, Any], tuple[str, int]]


def frame_table(rows: Sequence[tuple[str, Any, str]]) -> tuple[Frames, set[tuple[str, Any]]]:
    """(volume, page) → (NDL pid, frame) from (volume, page, NDL URL) rows, and the pages put on two frames.

    A page listed twice on the same frame is one mapping; a page listed on two frames has none, since
    nothing in the table says which is right. A row without an NDL URL maps nothing.
    """
    seen: dict[tuple[str, Any], set[tuple[str, int]]] = {}
    for volume, page, url in rows:
        found = re.search(r"pid/(\d+)/(\d+)", url or "")
        if found:
            seen.setdefault((volume, page), set()).add((found.group(1), int(found.group(2))))
    conflicts = {page for page, frames in seen.items() if len(frames) > 1}
    return {page: next(iter(frames)) for page, frames in seen.items() if page not in conflicts}, conflicts


# ------------------------------------------------------------------ KRM
_KRM_LOCATION = re.compile(r"^T(?P<volume>[abc])(?P<page>\d{3})(?P<line>\d)(?P<segment>\d)(?P<order>\d)$")


def read_krm(clone: Path) -> tuple[list[Entry], Frames, set[tuple[str, Any]]]:
    """KRM's entries placed by `tenri_location` (page, line, segment, order), and its frame table."""
    entries = []
    for row in read_tsv(clone / "krm_main.tsv"):
        found = _KRM_LOCATION.match(row["tenri_location"])
        if not found:
            continue
        entries.append(Entry(
            entry_id=row["entry_id"], volume=row["volume_name"], page=int(found["page"]), line=int(found["line"]),
            segment=int(found["segment"]), order=int(found["order"]), location=row["tenri_location"],
            glyphs=headword(row["hanzi_entry"], row["original_entry"]),
            meta={"hanzi_id": row["hanzi_id"], "tenri_location": row["tenri_location"],
                  "kazama_location": row["kazama_location"], "radical": row["radical_name"],
                  "hanzi_entry": row["hanzi_entry"], "original_entry": row["original_entry"],
                  "definition": row["definition"]}))
    frames, conflicts = frame_table([(row["Book"], int(row["Tenri"]), row["NDL_url"])
                                     for row in read_tsv(clone / "krm_ndl.tsv") if row["Tenri"].isdigit()])
    return entries, frames, conflicts


# ------------------------------------------------------------------ KTB
#: A KTB headword's place: book, leaf, side, line and number. An id with a scholar's name after it
#: is that scholar's reading of an entry KTB also gives under the plain id.
_KTB_ID = re.compile(r"^(?P<book>\d)_(?P<leaf>\d{3})_(?P<side>[AB])(?P<line>\d)(?P<number>\d)$")
#: Entry types whose headword is written large at the head of its entry. Embedded headwords stand
#: inside another entry's gloss, omitted ones are not on the page, and Songben-Yupian ones are
#: variants the Song print adds.
KTB_WRITTEN = frozenset({"Regular", "Regular_seal"})


def read_ktb(clone: Path) -> tuple[list[Entry], Frames, set[tuple[str, Any]]]:
    """KTB's large headwords placed by `TBID` (half-leaf, line, number), and its frame table."""
    entries = []
    for row in read_tsv(clone / "KTB.tsv"):
        found = _KTB_ID.match(row["TBID"])
        if not found or row["Entry_type"] not in KTB_WRITTEN:
            continue
        half = f"{found['book']}_{found['leaf']}_{found['side']}"
        entries.append(Entry(
            entry_id=row["TBID"], volume=found["book"], page=half, line=int(found["line"]), segment=1,
            order=int(found["number"]), location=row["TBID"], glyphs=headword(row["Entry"], ""),
            meta={"entry": row["Entry"], "entry_type": row["Entry_type"], "radical": row["TB_radical"],
                  "volume_radical": row["TB_vol_radical"], "definition": row["TB_def"],
                  "remarks": row["TB_remarks"], "syid": row["SYID"]}))
    frames, conflicts = frame_table([(leaf.split("_")[0], leaf, row["NDL_url"])
                                     for row in read_tsv(clone / "KTB_ndl.txt") if (leaf := row["Book_leaf"])])
    return entries, frames, conflicts


# ------------------------------------------------------------------ TSJ
#: A TSJ headword's place: volume, leaf, side (a recto, b verso), line and number.
_TSJ_ID = re.compile(r"^s(?P<volume>\d{2})(?P<leaf>\d{2})(?P<side>[ab])(?P<line>\d)(?P<number>\d{2})$")
#: How TSJ writes "the written form is the collated one".
TSJ_SAME = frozenset({"", "◯", "○", "〇"})


def read_tsj(clone: Path) -> tuple[list[Entry], Frames, set[tuple[str, Any]]]:
    """TSJ's headwords placed by `SJID` (half-leaf, line, number), and its frame table."""
    entries = []
    for row in read_tsv(clone / "TSJ_entries.tsv"):
        found = _TSJ_ID.match(row["SJID"])
        if not found:
            continue
        entries.append(Entry(
            entry_id=row["SJID"], volume=found["volume"], page=row["SJID"][:6], line=int(found["line"]), segment=1,
            order=int(found["number"]), location=row["SJID"],
            glyphs=headword(row["Entry"], row["Entry_original"], same=TSJ_SAME),
            meta={"entry": row["Entry"], "entry_original": row["Entry_original"], "radical": row["SJ_radical"],
                  "source": row["SJ_source"], "remarks": row["SJ_entry_remarks"], "rinsen": row["SJ_Rinsen"]}))
    frames, conflicts = frame_table([(row["SJ_vol_leaf"][1:3], row["SJ_vol_leaf"], row["NDL_URL_813.2-Sy968s_1916"])
                                     for row in read_tsv(clone / "TSJ_ndl.tsv")])
    return entries, frames, conflicts


@dataclass(frozen=True)
class Dictionary:
    """How one HDIC dictionary is read and laid out, and which pages of a frame are expected on the right."""

    name: str
    read: Callable[[Path], tuple[list[Entry], Frames, set[tuple[str, Any]]]]
    layout: Layout
    right: Callable[[Any], bool]


DICTIONARIES = {
    # 天理 pages: the even page of an opening is on the right.
    "krm": Dictionary("krm", read_krm, Layout(columns=8, tiers=4, held=6), lambda page: page % 2 == 0),
    # Half-leaves: an opening shows a verso (B, b) on the right and the next recto on the left.
    "ktb": Dictionary("ktb", read_ktb, Layout(columns=6, tiers=1, held=4), lambda page: page.endswith("B")),
    "tsj": Dictionary("tsj", read_tsj, Layout(columns=8, tiers=1, held=6), lambda page: page.endswith("b")),
}


# ------------------------------------------------------------------ geometry
def side(boxes: Sequence[Box]) -> float:
    """The page's typical box side: the median of the boxes' longer sides."""
    return float(np.median([max(b.w, b.h) for b in boxes])) if boxes else 0.0


def is_big(box: Box, unit: float) -> bool:
    return BIG[0] * unit <= max(box.w, box.h) <= BIG[1] * unit and 0.25 <= box.w / box.h <= 4


def is_mark(box: Box, unit: float) -> bool:
    """A thin tall box, as the repetition mark ｜ is drawn."""
    return box.h >= 1.2 * unit and box.w <= 0.45 * box.h and box.w < unit


def fit(values: Sequence[float], count: int, low: float, high: float, share: float,
        anchor: float | None = None, direction: int = 1) -> tuple[float, float, float]:
    """The evenly spaced lines `start + direction * k * step`, k < count, that most values sit near.

    Every whole step in [low, high) is tried as the spacing and, unless `anchor` fixes it, every
    value as the first line. A value scores `1 - distance / (share * step)` near a line of the grid
    and nothing elsewhere. Returns the score, the start and the step.
    """
    data = np.asarray(sorted(values), dtype=float)
    if len(data) == 0 or high <= low:
        return 0.0, 0.0, 0.0
    starts = data if anchor is None else np.asarray([anchor])
    best = (-np.inf, 0.0, 0.0)
    for step in np.arange(int(low), int(high), 1.0):
        tolerance = share * step
        for start in starts:
            k = np.rint(direction * (data - start) / step)
            near = np.clip(1 - np.abs(start + direction * k * step - data) / tolerance, 0, None)
            score = float(np.where((k >= 0) & (k < count), near, 0).sum())
            if score > best[0]:
                best = (score, float(start), float(step))
    return best


@dataclass(frozen=True)
class Grid:
    """A page's columns (x centres, line 1 first) and tiers (y lines, segment 1 first, each `tier_pitch` tall)."""

    columns: tuple[float, ...]
    pitch: float
    tiers: tuple[float, ...]
    tier_pitch: float

    def holds(self, box: Box) -> bool:
        """Whether a box's centre falls inside the page's columns."""
        x = box.x + box.w / 2
        return min(self.columns) - self.pitch / 2 <= x < max(self.columns) + self.pitch / 2


def edge_columns(boxes: Sequence[Box], unit: float) -> tuple[float, float] | None:
    """The x centres of the rightmost and the leftmost column of headwords on a frame.

    Headword centres within `unit` of each other form a column; a column needs three headwords, so
    that a stray box at the plate's edge or on the ruler does not count.
    """
    xs = sorted(b.x + b.w / 2 for b in boxes if is_big(b, unit))
    groups: list[list[float]] = []
    for x in xs:
        if groups and x - groups[-1][-1] < unit:
            groups[-1].append(x)
        else:
            groups.append([x])
    columns = [float(np.median(g)) for g in groups if len(g) >= 3]
    return (columns[-1], columns[0]) if columns else None


def page_grids(boxes: Sequence[Box], unit: float, layout: Layout) -> dict[str, Grid]:
    """The grids of the two pages of a frame: `right` counted from the rightmost column, `left` ending at the leftmost.

    The gutter between the pages may be as wide as a column or several, so each page is anchored on
    its outer column and only its pitch is fitted. With one tier, the cell runs from the highest
    headword top on the page to the lowest bottom.
    """
    edges = edge_columns(boxes, unit)
    if edges is None:
        return {}
    big = [b for b in boxes if is_big(b, unit)]
    centres = [b.x + b.w / 2 for b in big]
    grids = {}
    for name, anchor, direction in (("right", edges[0], -1), ("left", edges[1], 1)):
        _, start, pitch = fit(centres, layout.columns, 2.2 * unit, 4.5 * unit, 0.25, anchor=anchor, direction=direction)
        if not pitch:
            continue
        columns = [start + direction * k * pitch for k in range(layout.columns)]
        if name == "left":
            columns = columns[::-1]  # line 1 is the page's rightmost column
        inside = [b for b in big if Grid(tuple(columns), pitch, (), 0.0).holds(b)]
        held = sum(1 for x in columns if any(abs(c - x) < pitch / 4 for c in centres))
        if held < layout.held or not inside:
            continue
        tops = [b.y for b in inside]
        if layout.tiers == 1:
            # `cell_boxes` opens a cell a quarter of its height above the tier line.
            top, bottom = min(tops) - unit, max(b.y + b.h for b in inside) + unit
            grids[name] = Grid(tuple(columns), pitch, (top + 0.25 * (bottom - top),), bottom - top)
            continue
        _, top, tier_pitch = fit(tops, layout.tiers, TIER_PITCH[0] * pitch, TIER_PITCH[1] * pitch, 0.1)
        if not tier_pitch:
            continue
        tiers = [top + k * tier_pitch for k in range(layout.tiers)]
        if any(sum(1 for y in tops if abs(y - t) < 0.1 * tier_pitch) < TIER_HELD for t in tiers):
            continue
        grids[name] = Grid(tuple(columns), pitch, tuple(tiers), tier_pitch)
    return grids


def cell_boxes(boxes: Sequence[Box], grid: Grid, line: int, segment: int, unit: float) -> list[Box]:
    """The candidate headword boxes of a cell, top to bottom: headword-sized boxes and marks."""
    x = grid.columns[line - 1]
    top = grid.tiers[segment - 1] - 0.25 * grid.tier_pitch
    return sorted((b for b in boxes if abs(b.x + b.w / 2 - x) < 0.45 * grid.pitch
                   and top <= b.y < top + grid.tier_pitch and (is_big(b, unit) or is_mark(b, unit))),
                  key=lambda b: b.y)


# ------------------------------------------------------------------ pairing
@dataclass
class Pair:
    entry: Entry
    slot: int
    glyph: Glyph
    box: Box
    top5: list[str]
    verdict: bool | None
    kept: bool = False


@dataclass
class Placement:
    """What `place` made of a page: the kept pairs and what happened to the rest."""

    pairs: list[Pair] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)

    def count(self, key: str, n: int = 1) -> None:
        self.counts[key] = self.counts.get(key, 0) + n

    @property
    def agreed(self) -> int:
        return sum(1 for pair in self.pairs if pair.kept and pair.verdict)


def align_cell(glyphs: Sequence[Glyph], boxes: Sequence[Box], verdicts: Callable[[int, int], bool | None],
               marks: Sequence[bool]) -> list[tuple[int, int]]:
    """The least-cost pairing of a cell's glyphs with its boxes, both in reading order.

    `verdicts(i, j)` is whether the classifier reads glyph i in box j; `marks[j]` whether box j is
    mark-shaped. Returns (glyph index, box index) pairs.
    """
    def match(i: int, j: int) -> float:
        if (glyphs[i].text == MARK) != marks[j]:
            return 2.5
        if glyphs[i].text == MARK:
            return 0.0
        return {True: 0.0, False: 2.0, None: 0.8}[verdicts(i, j)]

    n, m = len(glyphs), len(boxes)
    skip_glyph = [0.3 if g.text == MARK else 1.5 for g in glyphs]
    cost = np.zeros((n + 1, m + 1))
    cost[1:, 0] = np.cumsum(skip_glyph)
    cost[0, 1:] = np.arange(1, m + 1) * 1.0
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cost[i, j] = min(cost[i - 1, j - 1] + match(i - 1, j - 1),
                             cost[i - 1, j] + skip_glyph[i - 1], cost[i, j - 1] + 1.0)
    pairs, i, j = [], n, m
    while i and j:
        if np.isclose(cost[i, j], cost[i - 1, j - 1] + match(i - 1, j - 1)):
            pairs.append((i - 1, j - 1))
            i, j = i - 1, j - 1
        elif np.isclose(cost[i, j], cost[i - 1, j] + skip_glyph[i - 1]):
            i -= 1
        else:
            j -= 1
    return pairs[::-1]


def place(entries: Sequence[Entry], boxes: Sequence[Box], grid: Grid, unit: float,
          rank: Callable[[Box], list[str]], known: set[str]) -> Placement:
    """Place one page's headwords among a frame's detected boxes, on the grid of the page it stands on.

    `rank(box)` is the classifier's five best classes for a box, `known` all its classes.
    """
    result = Placement()
    cells: dict[tuple[int, int], list[Entry]] = {}
    for entry in entries:
        cells.setdefault((entry.line, entry.segment), []).append(entry)
    for (line, segment), members in sorted(cells.items()):
        if line > len(grid.columns) or segment > len(grid.tiers):
            result.count("off-grid")
            continue
        members.sort(key=lambda e: (e.order, e.location))
        slots = [(e, k, g) for e in members for k, g in enumerate(e.glyphs)]
        candidates = cell_boxes(boxes, grid, line, segment, unit)
        if not slots:
            continue
        if not candidates:
            result.count("cell-empty")
            continue
        ranked = [rank(b) for b in candidates]
        judged: dict[tuple[int, int], bool | None] = {}

        def verdict(i: int, j: int, ranked=ranked, slots=slots, judged=judged) -> bool | None:
            if (i, j) not in judged:
                judged[(i, j)] = agrees(slots[i][2], ranked[j], known)
            return judged[(i, j)]

        marks = [is_mark(b, unit) for b in candidates]
        indexed = [(i, j) for i, j in align_cell([s[2] for s in slots], candidates, verdict, marks)
                   if slots[i][2].text != MARK and not marks[j]]
        pairs = [Pair(slots[i][0], slots[i][1], slots[i][2], candidates[j], ranked[j], verdict(i, j)) for i, j in indexed]
        refused = any(p.verdict is False for p in pairs)
        written = [k for k, s in enumerate(slots) if s[2].text != MARK]
        # Every written glyph on its own headword box, in order: no other pairing exists to doubt.
        complete = len(pairs) == len(written) == sum(1 for mark in marks if not mark)
        read = any(p.verdict for p in pairs)
        on_line = abs(candidates[0].y - grid.tiers[segment - 1]) <= ANCHOR * grid.tier_pitch
        for n, (pair, (i, j)) in enumerate(zip(pairs, indexed, strict=True)):
            if pair.verdict:
                pair.kept = True
            elif pair.verdict is None and not refused:
                at_line = bool(written) and i == written[0] and j == 0 and on_line
                # Read on both sides, on the boxes next to its own.
                held = (0 < n < len(pairs) - 1 and pairs[n - 1].verdict and pairs[n + 1].verdict
                        and indexed[n - 1][1] == j - 1 and indexed[n + 1][1] == j + 1)
                pair.kept = bool((complete and (read or on_line)) or at_line or held)
            result.count("kept" if pair.kept else "refused" if pair.verdict is False else "unanchored")
        result.count("unpaired", len(written) - len(pairs))
        result.pairs += pairs
    judged_pairs = [p for p in result.pairs if p.verdict is not None]
    if judged_pairs and sum(1 for p in judged_pairs if p.verdict is False) > REFUSED_SHARE * len(judged_pairs):
        result.count("page-refused")
        result.count("dropped-with-page", result.counts.pop("kept", 0))
        for pair in result.pairs:
            pair.kept = False
    return result


def assign_pages(tried: dict[Any, dict[str, Placement]], right: Callable[[Any], bool]) -> dict[Any, str]:
    """Which grid each page of a frame stands on, one page to a grid.

    `tried[page][grid]` is the page placed on that grid. The assignment with the most headwords read
    where they are placed wins, and on a tie the one that puts on the right the pages `right` expects
    there. A frame given more pages than it has grids gets none.
    """
    pages = sorted(tried)
    grids = sorted({name for placements in tried.values() for name in placements})
    if not pages or len(pages) > len(grids):
        return {}

    def score(choice: tuple[str, ...]) -> tuple[int, int]:
        agreed = sum(tried[page][name].agreed for page, name in zip(pages, choice, strict=True))
        expected = sum((name == "right") == right(page) for page, name in zip(pages, choice, strict=True))
        return agreed, expected

    best = max(permutations(grids, len(pages)), key=score)
    return dict(zip(pages, best, strict=True))


# ------------------------------------------------------------------ records
def document_of(dictionary: Dictionary, pid: str, manifest: dict, source: dict) -> Document:
    """One NDL volume of a facsimile as a document, titled with the label its source record gives the
    volume, with the manuscript's dating and HDIC's text rights."""
    meta = {str(item.get("label")): item.get("value") for item in manifest.get("metadata", []) or []}
    images = source["images"]
    manifest_url = f"https://dl.ndl.go.jp/api/iiif/{pid}/manifest.json"
    image_rights = Rights(
        licence=Licence.PDM if meta.get("Access Restrictions") == "PDM" else Licence.UNKNOWN,
        holder=images["holder"], attribution=images["attribution"], evidence=manifest_url)
    text_rights = Rights(licence=Licence(source["licence"]), holder=source["publisher"],
                         attribution=source["attribution"], evidence=source["licence_evidence"])
    dating = source["dating"]
    label = next(v["label"] for v in images["volumes"] if str(v["pid"]) == pid)
    return Document(
        id=f"hdic-{dictionary.name}:{pid}",
        title=f"{source['title']}{label}",
        source_refs={f"hdic-{dictionary.name}": label, "ndl-pid": pid, "iiif-manifest": manifest_url},
        holder=images["holder"],
        shelfmark=images.get("shelfmark"),
        production="handwritten",
        origin="japan",
        genre=["dictionary"],
        text_register=Register(source.get("register", "mixed")),
        dating=[Dating(literal=dating["literal"], start=dating["start"], end=dating["end"],
                       kind=dating.get("kind", "unknown"), evidence=dating["evidence"])],
        image_rights=image_rights,
        text_rights=text_rights,
        meta={"volume": label, "facsimile": {"title": images["title"], "publisher": images["publisher"],
                                              "production": images.get("production"),
                                              "ndl_pid": pid, "ndl_catalogue": meta}},
    )
