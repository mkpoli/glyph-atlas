"""Headwords of the 観智院本類聚名義抄 on the NDL facsimile, placed from the HDIC KRM database.

KRM (`data/sources/hdic-krm.yaml`) transcribes every entry of the manuscript and gives its place in
the 天理図書館善本叢書 edition: page, line, segment and order. A page of the manuscript is eight
columns read right to left, and each column four tiers (segments) read top to bottom; an entry
starts at the top of a tier with its headword, one large character after another, followed by
small glosses. A frame of the NDL facsimile (貴重図書複製会, 1937) shows one opening, two pages.

`read_entries` reads the entries with their headword glyphs. A headword slot is one written
character: the form in `original_entry`, or the collated form in `hanzi_entry` where the original
is 〇, with the collated form as the glyph's standard when the two differ. `ー（X）`, `｜` and `〻`
are repetition marks (`Glyph.text` is `MARK`); `［X］` is a character the editors supply and no glyph
stands for it. IDS sequences, CHISE and GlyphWiki references and a form with a Greek suffix (`僕β`)
describe a character Unicode lacks, and `■` is one nobody could read.

`page_grids` and `place` find the headword boxes among a frame's detected character boxes:

1. Headword-sized boxes (`BIG`, relative to the page's median box) are kept; a thin tall box is a
   repetition mark (`is_mark`).
2. The right page is anchored on the frame's rightmost column of headwords and the left page on
   its leftmost, since the gutter between them may be as narrow as a column or several wide. `fit`
   finds each page's column pitch, and then four evenly spaced tiers over its headwords' tops, their
   pitch held to `TIER_PITCH` times the column pitch. A grid stands only when `COLUMNS_HELD` of its
   columns and every tier line hold headwords.
3. In each cell, `align_cell` pairs the cell's headword glyphs, in KRM order, with its candidate
   boxes top to bottom by least cost, the classifier's five best classes deciding whether a glyph
   and a box agree (`glossary.agrees`). Glyphs and boxes may go unpaired: a mark the detector
   missed, a large gloss character.

A pair is kept when the classifier reads the glyph there. A glyph the classifier cannot judge (a
description, or a character it has no class for) is kept when no pair of its cell was refused and
the pairing admits no doubt: every written glyph of the cell sits on its own headword box with no
box left over, and another glyph of the cell was read where it was placed or the cell's first box
stands at the tier line. The cell's first headword character on the cell's first box, at the tier
line, is kept on that alone. A pair the classifier refuses is left out, and
a page on which more than `REFUSED_SHARE` of the judged pairs are refused is left out whole,
since that is how a misfitted grid looks. The caller tries a frame's 天理 pages on its two grids,
one page to a grid.
The label always comes from KRM, never the classifier.
"""

from __future__ import annotations

import csv
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from itertools import permutations
from pathlib import Path

import numpy as np

from .glossary import Glyph, agrees
from .schema import Box, Dating, Document, Licence, Register, Rights

#: `Glyph.text` of a repetition mark.
MARK = "ー"
#: How KRM writes a repetition mark.
MARKS = frozenset({"ー", "｜", "〻"})
#: How KRM writes a character nobody could read.
UNREADABLE = "■"
#: Columns on a page and tiers in a column.
COLUMNS, TIERS = 8, 4
#: A headword box's longer side, as a range of multiples of the page's median box side.
BIG = (1.5, 3.5)
#: Most of a page's judged pairs the classifier may refuse before the page is left out.
REFUSED_SHARE = 0.25
#: How far from its tier line a cell's first box may stand and still anchor the cell, in tier pitches.
ANCHOR = 0.2
#: The tier pitch, as a range of multiples of the column pitch. Half or twice the true spacing fits
#: a page's headword tops almost as well as the spacing itself, and falls outside this range.
TIER_PITCH = (2.5, 4.5)
#: Least number of a page's eight columns that must hold a headword for its grid to stand.
COLUMNS_HELD = 6
#: Least number of headword tops on each tier line for the tiers to stand.
TIER_HELD = 2

_STANDARD = re.compile(r"[（(]([^）)]*)[）)]")
_LOCATION = re.compile(r"^T(?P<volume>[abc])(?P<page>\d{3})(?P<line>\d)(?P<segment>\d)(?P<order>\d)$")


@dataclass(frozen=True)
class Entry:
    """One KRM entry: where it stands in the 天理 edition, and its headword glyphs."""

    entry_id: str
    hanzi_id: str
    volume: str
    page: int
    line: int
    segment: int
    order: int
    location: str
    kazama: str
    radical: str
    hanzi_entry: str
    original_entry: str
    definition: str
    glyphs: tuple[Glyph, ...]


def read_tsv(path: Path) -> list[dict[str, str]]:
    """The rows of a KRM TSV, skipping its `#` header."""
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


def headword(hanzi_entry: str, original_entry: str) -> tuple[Glyph, ...]:
    """The written glyphs of a headword, in order."""
    collated = hanzi_entry.split("／")
    written = original_entry.split("／") if original_entry not in ("", "〇") else collated
    if len(written) != len(collated):
        written = collated
    out = []
    for slot, base in zip(written, collated, strict=True):
        glyph = slot_glyph(base if slot == "〇" else slot, base)
        if glyph is not None:
            out.append(glyph)
    return tuple(out)


def read_entries(path: Path) -> list[Entry]:
    """Every entry of `krm_main.tsv` whose 天理 location parses."""
    out = []
    for row in read_tsv(path):
        found = _LOCATION.match(row["tenri_location"])
        if not found:
            continue
        out.append(Entry(
            entry_id=row["entry_id"], hanzi_id=row["hanzi_id"], volume=row["volume_name"],
            page=int(found["page"]), line=int(found["line"]), segment=int(found["segment"]),
            order=int(found["order"]), location=row["tenri_location"], kazama=row["kazama_location"],
            radical=row["radical_name"], hanzi_entry=row["hanzi_entry"], original_entry=row["original_entry"],
            definition=row["definition"], glyphs=headword(row["hanzi_entry"], row["original_entry"])))
    return out


def read_frames(path: Path) -> tuple[dict[tuple[str, int], tuple[str, int]], set[tuple[str, int]]]:
    """`krm_ndl.tsv` as (volume, 天理 page) → (NDL pid, frame), and the pages it puts on two frames.

    A page listed twice on the same frame is one mapping; a page listed on two frames has none, since
    nothing in the table says which is right.
    """
    seen: dict[tuple[str, int], set[tuple[str, int]]] = {}
    for row in read_tsv(path):
        found = re.search(r"pid/(\d+)/(\d+)", row["NDL_url"])
        if found and row["Tenri"].isdigit():
            seen.setdefault((row["Book"], int(row["Tenri"])), set()).add((found.group(1), int(found.group(2))))
    conflicts = {page for page, frames in seen.items() if len(frames) > 1}
    return {page: next(iter(frames)) for page, frames in seen.items() if page not in conflicts}, conflicts


def encoded(glyph: Glyph) -> str | None:
    """The code point of a glyph that is one encoded character, as `U+XXXX`."""
    if glyph.text == MARK or not glyph.encoded or len(glyph.text) != 1:
        return None
    return f"U+{ord(glyph.text):04X}"


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
    """A page's columns (x centres, line 1 first) and tiers (y tops, segment 1 first)."""

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


def page_grids(boxes: Sequence[Box], unit: float) -> dict[str, Grid]:
    """The grids of the two pages of a frame: `right` counted from the rightmost column, `left` ending at the leftmost.

    The gutter between the pages may be as wide as a column or several, so each page is anchored on
    its outer column and only its pitch is fitted.
    """
    edges = edge_columns(boxes, unit)
    if edges is None:
        return {}
    big = [b for b in boxes if is_big(b, unit)]
    centres = [b.x + b.w / 2 for b in big]
    grids = {}
    for name, anchor, direction in (("right", edges[0], -1), ("left", edges[1], 1)):
        _, start, pitch = fit(centres, COLUMNS, 2.2 * unit, 4.5 * unit, 0.25, anchor=anchor, direction=direction)
        if not pitch:
            continue
        columns = [start + direction * k * pitch for k in range(COLUMNS)]
        if name == "left":
            columns = columns[::-1]  # line 1 is the page's rightmost column
        inside = Grid(tuple(columns), pitch, (), 0.0)
        tops = [b.y for b in big if inside.holds(b)]
        _, top, tier_pitch = fit(tops, TIERS, TIER_PITCH[0] * pitch, TIER_PITCH[1] * pitch, 0.1)
        if not tier_pitch:
            continue
        tiers = [top + k * tier_pitch for k in range(TIERS)]
        held = sum(1 for x in columns if any(abs(c - x) < pitch / 4 for c in centres))
        if held < COLUMNS_HELD or any(sum(1 for y in tops if abs(y - t) < 0.1 * tier_pitch) < TIER_HELD for t in tiers):
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
    """Place one 天理 page's headwords among a frame's detected boxes, on the grid of the page it stands on.

    `rank(box)` is the classifier's five best classes for a box, `known` all its classes.
    """
    result = Placement()
    cells: dict[tuple[int, int], list[Entry]] = {}
    for entry in entries:
        cells.setdefault((entry.line, entry.segment), []).append(entry)
    for (line, segment), members in sorted(cells.items()):
        members.sort(key=lambda e: e.location)
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
        pairs = [Pair(slots[i][0], slots[i][1], slots[i][2], candidates[j], ranked[j], verdict(i, j))
                 for i, j in align_cell([s[2] for s in slots], candidates, verdict, marks)
                 if slots[i][2].text != MARK and not marks[j]]
        refused = any(p.verdict is False for p in pairs)
        written = [s for s in slots if s[2].text != MARK]
        # Every written glyph on its own headword box, in order: no other pairing exists to doubt.
        complete = len(pairs) == len(written) == sum(1 for mark in marks if not mark)
        read = any(p.verdict for p in pairs)
        on_line = bool(candidates) and abs(candidates[0].y - grid.tiers[segment - 1]) <= ANCHOR * grid.tier_pitch
        first = written[0] if written else None
        line_top = grid.tiers[segment - 1]
        for pair in pairs:
            if pair.verdict:
                pair.kept = True
            elif pair.verdict is None and not refused:
                at_line = (first is not None and pair.entry is first[0] and pair.slot == first[1]
                           and pair.box is candidates[0] and abs(pair.box.y - line_top) <= ANCHOR * grid.tier_pitch)
                pair.kept = (complete and (read or on_line)) or at_line
            result.count("kept" if pair.kept else "refused" if pair.verdict is False else "unanchored")
        result.count("unpaired", sum(1 for s in slots if s[2].text != MARK) - len(pairs))
        result.pairs += pairs
    judged_pairs = [p for p in result.pairs if p.verdict is not None]
    if judged_pairs and sum(1 for p in judged_pairs if p.verdict is False) > REFUSED_SHARE * len(judged_pairs):
        result.count("page-refused")
        result.count("dropped-with-page", result.counts.pop("kept", 0))
        for pair in result.pairs:
            pair.kept = False
    return result


# ------------------------------------------------------------------ records
def document_of(volume: str, pid: str, manifest: dict, source: dict) -> Document:
    """One NDL volume of the facsimile as a document, the manuscript's dating and KRM's text rights."""

    meta = {str(item.get("label")): item.get("value") for item in manifest.get("metadata", []) or []}
    images = source["images"]
    image_rights = Rights(
        licence=Licence.PDM if meta.get("Access Restrictions") == "PDM" else Licence.UNKNOWN,
        holder=images["holder"], attribution=images["attribution"],
        evidence=f"https://dl.ndl.go.jp/api/iiif/{pid}/manifest.json")
    text_rights = Rights(licence=Licence(source["licence"]), holder=source["publisher"],
                         attribution=source["attribution"], evidence=source["licence_evidence"])
    dating = source["dating"]
    return Document(
        id=f"hdic-krm:{pid}",
        title=f"類聚名義抄（観智院本）{volume}",
        source_refs={"hdic-krm": volume, "ndl-pid": pid, "iiif-manifest": f"https://dl.ndl.go.jp/api/iiif/{pid}/manifest.json"},
        holder=images["holder"],
        shelfmark=images["shelfmark"],
        production="handwritten",
        origin="japan",
        genre=["dictionary"],
        text_register=Register.MIXED,
        dating=[Dating(literal=dating["literal"], start=dating["start"], end=dating["end"], kind="unknown",
                       evidence=dating["evidence"])],
        image_rights=image_rights,
        text_rights=text_rights,
        meta={"volume": volume, "facsimile": {"title": images["title"], "publisher": images["publisher"],
                                              "ndl_pid": pid, "ndl_catalogue": meta}},
    )


def assign_pages(tried: dict[int, dict[str, Placement]]) -> dict[int, str]:
    """Which grid each 天理 page of a frame stands on, one page to a grid.

    `tried[page][grid]` is the page placed on that grid. The assignment with the most headwords read
    where they are placed wins, and on a tie the one that puts even pages on the right. A frame KRM
    gives more pages than it has grids gets none.
    """
    pages = sorted(tried)
    grids = sorted({name for placements in tried.values() for name in placements})
    if not pages or len(pages) > len(grids):
        return {}

    def score(choice: tuple[str, ...]) -> tuple[int, int]:
        agreed = sum(tried[page][name].agreed for page, name in zip(pages, choice, strict=True))
        parity = sum(name == ("right" if page % 2 == 0 else "left") for page, name in zip(pages, choice, strict=True))
        return agreed, parity

    best = max(permutations(grids, len(pages)), key=score)
    return dict(zip(pages, best, strict=True))
