"""Draw a character Unicode lacks from its Ideographic Description Sequence, in GenZui Sans.

GenZui Sans takes its ideographs unchanged from Noto Sans JP at weight 400, which in turn draws them
as Noto Sans CJK JP does. The composer reads Noto Sans CJK JP, the variable font that draws 30,289
ideographs to Noto Sans JP's 13,742 with the same outlines, so each outline is known at weight 400
and at `HEAVY` with the same points. Every layout and every part comes from a character a type
designer drew; a sequence (⿰亻哥) is drawn in three steps:

1. Teacher. For each node, the drawn character with a node of the same operator whose operands are
   most like the target's (written the same, else holding about as much ink in about the same
   proportions) is cut into its operands (`split` along the operator's axis where no outline
   crosses; `enclosures` round an enclosed operand). Its layout is a designer's balance for such
   operands. An operand written there as the target writes it is used as the teacher drew it.
2. Parts. Any other operand is cut from a host: a drawn character with that operand, or its
   positional form (王 on the left as 𤣩, 水 as 氵), in the same position, at any depth of the
   host's sequence, beside siblings holding about as much ink. It takes the teacher's share along
   the operator's axis and the extent its own host gave it across. A cut is used only when each
   piece looks like its operand. With no host, a nested sequence is drawn the same way, and a
   character from its own glyph.
3. Weight. Noto thins its strokes as a character fills up (`STEM_CURVE`). Each part is moved
   towards the `HEAVY` master, along x and along y apart, until its strokes have the width the
   whole composed character's density calls for; a lone stroke keeps that width without scaling.

The result is a set of closed outlines in the font's 1000-unit em, y up, drawn out by `svg_path`.
⿻ (operands drawn across each other) is not laid out.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field
from functools import cached_property
from itertools import combinations
from pathlib import Path

import numpy as np
from fontTools.pens.recordingPen import RecordingPen
from fontTools.ttLib import TTFont

from .han_components import BINARY, TERNARY, UNARY, read_rows

#: Noto Sans CJK JP 2.004, variable, TrueType outlines (SIL OFL 1.1).
FONT_URL = "https://github.com/notofonts/noto-cjk/raw/Sans2.004/Sans/Variable/TTF/NotoSansCJKjp-VF.ttf"
FONT_SHA256 = "240c9b83bf7b386edbae39995ae7e068ed4583f484d92e4a74c34158b5f27b1a"
FONT_FILE = "NotoSansCJKjp-VF-2.004.ttf"
EM = 1000
#: The em's top in the font's units: an SVG path is drawn from here down.
ASCENT = 880
HEAVY = 900
#: The operators a part is cut from its host along: x for side by side, y for stacked.
AXIS = {"⿰": 0, "⿲": 0, "⿱": 1, "⿳": 1}
#: The sides an enclosing operand closes, y up: ⿸ 厂 closes the left and the top.
ENCLOSE = {"⿴": "LRBT", "⿵": "LRT", "⿶": "LRB", "⿷": "LBT", "⿸": "LT", "⿹": "RT", "⿺": "LB"}
#: Where an enclosed operand sits when no host shows it, as shares of the region (x0, y0, x1, y1).
INSIDE = {"⿴": (0.2, 0.15, 0.8, 0.8), "⿵": (0.2, 0.0, 0.8, 0.8), "⿶": (0.2, 0.2, 0.8, 1.0),
          "⿷": (0.2, 0.15, 1.0, 0.85), "⿸": (0.3, 0.0, 1.0, 0.78), "⿹": (0.0, 0.0, 0.75, 0.78),
          "⿺": (0.3, 0.25, 1.0, 1.0)}
#: Least likeness of a cut piece to its operand's own glyph.
LIKENESS = 0.45
#: Most a cut piece's outline length for its size may differ from its operand's glyph's.
DENSER = 1.7
#: Characters whose median box is the box a full ideograph fills.
FULL = "國圖體觀讀鬱識護議變顯襲驚響露臨"

Node = str | tuple
Box = tuple[float, float, float, float]


def font_file(cache: Path | None = None) -> Path:
    """The composer's font under `<cache>/fonts`, fetched and checked against its SHA-256 when missing."""
    import httpx

    from .images import cache_root

    path = (cache or cache_root()) / "fonts" / FONT_FILE
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        data = httpx.get(FONT_URL, follow_redirects=True, timeout=600).content
        if hashlib.sha256(data).hexdigest() != FONT_SHA256:
            raise ValueError(f"{FONT_URL} does not match its pinned SHA-256.")
        path.write_bytes(data)
    return path


# ------------------------------------------------------------------ sequences
def _selector(char: str) -> bool:
    point = ord(char)
    return 0xFE00 <= point <= 0xFE0F or 0xE0100 <= point <= 0xE01EF


def parse(sequence: str) -> Node:
    """`⿰亻⿱亠女` as ('⿰', '亻', ('⿱', '亠', '女')); a character, with any variation selector, stands for itself."""
    chars = list(sequence)
    at = 0

    def node() -> Node:
        nonlocal at
        if at >= len(chars):
            raise ValueError(f"{sequence} is missing a component.")
        char = chars[at]
        at += 1
        arity = 2 if char in BINARY else 3 if char in TERNARY else 1 if char in UNARY else 0
        if not arity:
            if at < len(chars) and _selector(chars[at]):
                char += chars[at]
                at += 1
            return char
        return (char, *(node() for _ in range(arity)))

    tree = node()
    if at != len(chars):
        raise ValueError(f"{sequence} has more components than its operators take.")
    return tree


def key(node: Node) -> str:
    """A node written out again as a sequence."""
    return node if isinstance(node, str) else node[0] + "".join(key(n) for n in node[1:])


#: A sequence's source regions: (GJ), (G[B]) or (UTC2003).
REGIONS = re.compile(r"^(.*?)\(([A-Z0-9\[\]]+)\)$")


def japanese_sequences() -> dict[str, str]:
    """Each ideograph's sequence as BabelStone gives it for Japan, else its first; sequences naming
    an unencoded component ({n}) or an unnameable one (？) are left out."""
    out = {}
    for row in read_rows("han-ids.tsv"):
        char, chosen = row[1], None
        for item in row[2].split():
            match = REGIONS.match(item)
            text, regions = (match.group(1), match.group(2)) if match else (item, "")
            if "J" in regions:
                chosen = text
                break
            chosen = chosen or text
        if chosen and chosen != char and "{" not in chosen and "？" not in chosen:
            out[char] = chosen
    return out


# ------------------------------------------------------------------ outlines
@dataclass
class Contour:
    """One closed contour: its pen commands (command, points, whether it closes on an implied
    point), and its points at weight 400 and at `HEAVY`."""

    ops: list[tuple[str, int, bool]]
    light: np.ndarray
    heavy: np.ndarray

    @cached_property
    def box(self) -> Box:
        return (*self.light.min(axis=0), *self.light.max(axis=0))


def _contours(pen: RecordingPen) -> list[tuple[list, np.ndarray]]:
    out, ops, points = [], [], []
    for op, args in pen.value:
        if op in ("closePath", "endPath"):
            out.append((ops, np.array(points, dtype=float)))
            ops, points = [], []
            continue
        real = [p for p in args if p is not None]
        ops.append((op, len(real), bool(args) and args[-1] is None))
        points.extend(real)
    return out


@dataclass
class Part:
    """The contours of a glyph, or of an operand cut from one."""

    contours: list[Contour]

    @cached_property
    def box(self) -> Box:
        boxes = np.array([c.box for c in self.contours])
        return boxes[:, 0].min(), boxes[:, 1].min(), boxes[:, 2].max(), boxes[:, 3].max()


class Font:
    """A variable font with a `wght` axis, read at weight 400 and at `HEAVY`."""

    def __init__(self, path: Path):
        self.font = TTFont(path)
        self.cmap = self.font.getBestCmap()
        # Ideographic variation sequences: (base, selector) → glyph name, None for the base's own glyph.
        self.variants = {(base, selector): name for table in self.font["cmap"].tables if table.format == 14
                         for selector, pairs in table.uvsDict.items() for base, name in pairs}
        self.light = self.font.getGlyphSet(location={"wght": 400})
        self.heavy = self.font.getGlyphSet(location={"wght": HEAVY})
        self._parts: dict[str, Part | None] = {}
        # Stem widths at 400 and at HEAVY: 丨's width across x, 一's height across y.
        self.stem = (self._stem("丨", 0), self._stem("一", 1))

    def _stem(self, char: str, axis: int) -> tuple[float, float]:
        part = self.glyph(char)
        heavy = np.concatenate([c.heavy for c in part.contours])
        return part.box[axis + 2] - part.box[axis], float(heavy[:, axis].max() - heavy[:, axis].min())

    def _name(self, char: str) -> str | None:
        """The glyph a character draws with, with a variation selector only as the font maps that sequence."""
        if len(char) == 1:
            return self.cmap.get(ord(char))
        if len(char) == 2 and _selector(char[1]) and (ord(char[0]), ord(char[1])) in self.variants:
            return self.variants[(ord(char[0]), ord(char[1]))] or self.cmap.get(ord(char[0]))
        return None

    def has(self, char: str) -> bool:
        return self._name(char) is not None

    def glyph(self, char: str) -> Part | None:
        if char not in self._parts:
            part = None
            name = self._name(char)
            if name is not None:
                light, heavy = RecordingPen(), RecordingPen()
                self.light[name].draw(light)
                self.heavy[name].draw(heavy)
                contours = [Contour(ops, p, q) for (ops, p), (_, q) in zip(_contours(light), _contours(heavy))]
                part = Part(contours) if contours else None
            self._parts[char] = part
        return self._parts[char]


# ------------------------------------------------------------------ cutting hosts
def _units(contours: list[Contour]) -> list[list[Contour]]:
    """Contours grouped with those inside them: a counter goes with the smallest contour whose box holds it."""
    order = {id(c): k for k, c in enumerate(contours)}
    area = lambda c: (c.box[2] - c.box[0]) * (c.box[3] - c.box[1])

    def inside(a: Contour, b: Contour) -> bool:
        # Of two contours with the same box, the earlier holds the later, so no two hold each other.
        held = a.box[0] >= b.box[0] and a.box[1] >= b.box[1] and a.box[2] <= b.box[2] and a.box[3] <= b.box[3]
        return held and (area(a) < area(b) or (area(a) == area(b) and order[id(b)] < order[id(a)]))

    owner = {}
    for c in contours:
        holders = [d for d in contours if inside(c, d)]
        if holders:
            owner[id(c)] = min(holders, key=lambda d: (d.box[2] - d.box[0]) * (d.box[3] - d.box[1]))

    def root(c: Contour) -> Contour:
        while id(c) in owner:
            c = owner[id(c)]
        return c

    groups: dict[int, list[Contour]] = {}
    for c in contours:
        groups.setdefault(id(root(c)), []).append(c)
    return list(groups.values())


def _span(unit: list[Contour], axis: int) -> tuple[float, float]:
    return min(c.box[axis] for c in unit), max(c.box[axis + 2] for c in unit)


#: A cut may pass through an outline that lies at least this share on one side of it, which goes with
#: that side: a stroke reaching under its neighbour, as 正's last stroke rises under 攵 in 政.
SIDED = 0.8


def split(part: Part, axis: int, pieces: int) -> list[Part] | None:
    """`part` cut into `pieces` along `axis` (x 0, y 1), in reading order: left to right, top to bottom.

    The cuts are where the fewest outlines cross them, and those least; an outline a cut passes
    through goes with the side that holds at least `SIDED` of it. A cut through the middle of an
    outline gives None, as does an empty piece.
    """
    units = _units(part.contours)
    if len(units) < pieces:
        return None
    spans = [_span(u, axis) for u in units]
    centres = sorted({(a + b) / 2 for a, b in spans})
    cuts = [(centres[k] + centres[k + 1]) / 2 for k in range(len(centres) - 1)]

    def cost(cut: float) -> tuple[int, float]:
        through = [(cut - a) / max(b - a, 1) for a, b in spans if a < cut < b]
        return sum(1 for share in through if 1 - SIDED < share < SIDED), sum(min(s, 1 - s) for s in through)

    def total(chosen: tuple[float, ...]) -> tuple[int, float]:
        costs = [cost(c) for c in chosen]
        return sum(c[0] for c in costs), sum(c[1] for c in costs)

    best = min(((total(chosen), chosen) for chosen in combinations(cuts, pieces - 1)), default=None)
    if best is None or best[0][0]:
        return None
    bounds = [-math.inf, *best[1], math.inf]
    groups: list[list[Contour]] = [[] for _ in range(pieces)]
    for unit, (a, b) in zip(units, spans):
        centre = (a + b) / 2
        groups[next(i for i in range(pieces) if bounds[i] <= centre < bounds[i + 1])].extend(unit)
    if any(not g for g in groups):
        return None
    parts = [Part(g) for g in groups]
    return parts[::-1] if axis == 1 else parts


def enclosures(part: Part, op: str) -> list[tuple[Part, Part]]:
    """The ways `part` may be cut into an enclosing and an enclosed operand under `op`: its outlines
    ranked by how far inside the closed sides they lie, the innermost k taken as the enclosed one."""
    units = _units(part.contours)
    X0, Y0, X1, Y1 = part.box

    def depth(unit: list[Contour]) -> float:
        (x0, x1), (y0, y1) = _span(unit, 0), _span(unit, 1)
        far = {"L": x0 - X0, "R": X1 - x1, "B": y0 - Y0, "T": Y1 - y1}
        return min(far[side] for side in ENCLOSE[op])

    ranked = sorted(units, key=depth, reverse=True)
    return [(Part([c for u in ranked[k:] for c in u]), Part([c for u in ranked[:k] for c in u]))
            for k in range(1, len(ranked))]


def mapped(box: Box, frame: Box, region: Box) -> Box:
    """`box`, drawn within `frame`, carried into `region` in proportion."""
    sx = (region[2] - region[0]) / max(frame[2] - frame[0], 1)
    sy = (region[3] - region[1]) / max(frame[3] - frame[1], 1)
    return (region[0] + (box[0] - frame[0]) * sx, region[1] + (box[1] - frame[1]) * sy,
            region[0] + (box[2] - frame[0]) * sx, region[1] + (box[3] - frame[1]) * sy)


# ------------------------------------------------------------------ composing
#: Stroke width at weight 400 against a glyph's density, fitted on 1,500 ideographs of the font by
#: `scripts/compose_ids.py calibrate`: (intercept, slope per 1,000 units of outline length), for the
#: stems that run along y (measured across x) and those that run along x. Noto thins its strokes as
#: a character fills up, the horizontals more than the verticals.
STEM_CURVE = ((85.34, -1.324), (86.93, -1.914))
#: The positions an operand takes, by operator: left, centre, right; top, middle, bottom.
SLOTS = {"⿰": "LR", "⿲": "LCR", "⿱": "TB", "⿳": "TMB"}
#: The table's position names for a radical's positional form.
FORM_POSITION = {"left": "L", "right": "R", "top": "T", "bottom": "B"}
#: Most an operand may differ from the teacher's, in ink or in proportions, for the teacher's layout to hold.
UNLIKE = 2.0
#: Most an enclosed part's or a whole glyph's proportions may change on the way into its box.
STRETCH = 1.6


def stem(density: float, axis: int) -> float:
    """The stroke width the font draws in a glyph of `density`, along `axis`'s stems."""
    intercept, slope = STEM_CURVE[axis]
    return intercept + slope * density / 1000


def slot(op: str, index: int) -> str:
    """An operand's position: L C R T M B for the aligned operators, the operator and index for the rest."""
    return SLOTS[op][index] if op in SLOTS else f"{op}{index}"


def positional_forms() -> dict[str, list[tuple[str, str | None]]]:
    """character → its radical variants and where each stands, None where the table names no place:
    水 → [(氵, L), (氺, B)], 玉 → [(王, None), (𤣩, L)]."""
    out: dict[str, list[tuple[str, str | None]]] = {}
    for form, char, source, detail in read_rows("han-component-forms.tsv"):
        if source == "cjkvi-variants":
            out.setdefault(char, []).append((form, FORM_POSITION.get(detail.rsplit(" ", 1)[-1])))
    return out


def fitted(source: Box, target: Box) -> Box:
    """`target` narrowed about its centre so that a part from `source` changes its proportions by
    at most `STRETCH`."""
    sx = (target[2] - target[0]) / max(source[2] - source[0], 1)
    sy = (target[3] - target[1]) / max(source[3] - source[1], 1)
    x0, y0, x1, y1 = target
    if sx > sy * STRETCH:
        half = sy * STRETCH * (source[2] - source[0]) / 2
        middle = (x0 + x1) / 2
        x0, x1 = middle - half, middle + half
    elif sy > sx * STRETCH:
        half = sx * STRETCH * (source[3] - source[1]) / 2
        middle = (y0 + y1) / 2
        y0, y1 = middle - half, middle + half
    return x0, y0, x1, y1


@dataclass
class Placed:
    """A part drawn into `target` from `source`, thickened by `weight`: the share of the way to
    `HEAVY` its points move, along x and along y. `native` is the density of the glyph the part
    was drawn in, which set its strokes' width."""

    part: Part
    source: Box
    target: Box
    weight: tuple[float, float] = (0.0, 0.0)
    native: float = 0.0
    #: Where the part comes from: "teacher 謌", "host 何" or "glyph 王".
    origin: str = ""

    @property
    def scale(self) -> tuple[float, float]:
        return ((self.target[2] - self.target[0]) / max(self.source[2] - self.source[0], 1),
                (self.target[3] - self.target[1]) / max(self.source[3] - self.source[1], 1))

    def contours(self) -> list[np.ndarray]:
        scale = np.array(self.scale)
        centre = np.array([(self.source[0] + self.source[2]) / 2, (self.source[1] + self.source[3]) / 2])
        goal = np.array([(self.target[0] + self.target[2]) / 2, (self.target[1] + self.target[3]) / 2])
        weight = np.array(self.weight)
        return [(c.light + (c.heavy - c.light) * weight - centre) * scale + goal for c in self.part.contours]


@dataclass
class Host:
    """A node of a drawn character cut into its operands: the character, the pieces, the box of the
    node's ink, and the mean gap between the pieces."""

    char: str
    pieces: list[Part]
    frame: Box
    gap: float


@dataclass
class Composer:
    font: Font
    sequences: dict[str, str]
    #: Characters nothing is taken from: the character being drawn, when an encoded one is redrawn to test.
    exclude: set[str] = field(default_factory=set)
    #: Whether layouts and parts come from drawn characters, and whether strokes are brought to the
    #: weight the result's density calls for. With both off, each operand's own glyph is squeezed
    #: into its share, the plainest way to compose, for comparison.
    hosted: bool = True
    weighted: bool = True
    _expanding: set[str] = field(default_factory=set, init=False, repr=False)
    _inks: dict[str, float] = field(default_factory=dict, init=False, repr=False)
    _sketches: dict[str, Part | None] = field(default_factory=dict, init=False, repr=False)

    @cached_property
    def by_sequence(self) -> dict[str, str]:
        """The drawn character each sequence describes."""
        return {seq: char for char, seq in self.sequences.items() if self.font.has(char)}

    @cached_property
    def forms(self) -> dict[str, list[tuple[str, str | None]]]:
        return positional_forms()

    @cached_property
    def _index(self) -> tuple[dict, dict]:
        """(slot, operand) → drawn characters holding that operand there, and operator → drawn
        characters with a node of it; each with its tree and the path of positions to the node."""
        hosts: dict[tuple[str, str], list] = {}
        templates: dict[str, list] = {}

        def walk(char: str, tree: tuple, node: Node, path: tuple) -> None:
            if not isinstance(node, tuple) or (node[0] not in AXIS and node[0] not in ENCLOSE):
                return
            templates.setdefault(node[0], []).append((char, tree, path))
            for i, child in enumerate(node[1:]):
                hosts.setdefault((slot(node[0], i), key(child)), []).append((char, tree, path, i))
                walk(char, tree, child, path + (i,))

        for char, seq in self.sequences.items():
            if self.font.has(char):
                try:
                    tree = parse(seq)
                except ValueError:
                    continue
                walk(char, tree, tree, ())
        return hosts, templates

    @cached_property
    def face(self) -> Box:
        """The box a full ideograph fills."""
        boxes = [g.box for g in map(self.font.glyph, FULL) if g]
        return tuple(float(v) for v in np.median(np.array(boxes), axis=0))

    def ink(self, node: Node, part: Part | None = None, seen: frozenset[str] = frozenset()) -> float:
        """How much a part holds: its outlines' length, which grows with its strokes. An operand the
        font has no glyph for is measured by its own sequence, else (or in a loop of sequences) as 永."""
        if part is not None:
            return sum(float(np.linalg.norm(np.diff(np.vstack([c.light, c.light[:1]]), axis=0), axis=1).sum())
                       for c in part.contours)
        if isinstance(node, tuple):
            return sum(self.ink(n, seen=seen) for n in node[1:])
        if node not in self._inks:
            glyph = self.font.glyph(node)
            if glyph is not None:
                self._inks[node] = self.ink(node, glyph)
            elif node in self.sequences and node not in seen:
                try:
                    return self.ink(parse(self.sequences[node]), seen=seen | {node})
                except ValueError:
                    return self.ink("永")
            else:
                return self.ink("永")
        return self._inks[node]

    def aspect(self, node: Node, seen: frozenset[str] = frozenset()) -> float:
        """An operand's natural width over height: its glyph's, or for a sequence the operands'
        put side by side or stacked; an enclosure takes its enclosing operand's."""
        if isinstance(node, str):
            glyph = self.font.glyph(node)
            if glyph is not None:
                return max(glyph.box[2] - glyph.box[0], 1) / max(glyph.box[3] - glyph.box[1], 1)
            if node in self.sequences and node not in seen:
                try:
                    return self.aspect(parse(self.sequences[node]), seen | {node})
                except ValueError:
                    pass
            return 1.0
        if key(node) in self.by_sequence:
            return self.aspect(self.by_sequence[key(node)], seen)
        parts = [self.aspect(n, seen) for n in node[1:]]
        if node[0] in ("⿰", "⿲"):
            return sum(parts)
        if node[0] in ("⿱", "⿳"):
            return 1 / sum(1 / a for a in parts)
        return parts[0]

    @cached_property
    def _plain(self) -> Composer:
        return Composer(self.font, self.sequences, hosted=False, weighted=False)

    def _sketch(self, node: tuple) -> Part | None:
        """A sequence drawn on its own the plain way, from its operands' own glyphs, as one part to
        compare pieces with; None when it cannot be drawn."""
        name = key(node)
        if name not in self._sketches:
            self._sketches[name] = None
            try:
                placed = self._plain._node(node, self.face)
            except LookupError:
                return None
            contours = [Contour(c.ops, points, points) for p in placed for c, points in zip(p.part.contours, p.contours())]
            self._sketches[name] = Part(contours) if contours else None
        return self._sketches[name]

    def likeness(self, part: Part, node: Node) -> float:
        """How much a piece looks like its operand's own glyph, each stretched to fill a square; 1
        for an operand with no glyph. An operand written as a sequence is compared with that sequence
        drawn on its own. Proportions far from the operand's (a lone stroke of 口) count
        as no likeness, since stretching would hide them."""
        if isinstance(node, tuple) and key(node) not in self.by_sequence:
            # A piece written as a sequence is compared with that sequence drawn on its own.
            own = self._sketch(node)
            if own is None:
                return 0.0
        else:
            own = self.font.glyph(self.by_sequence.get(key(node), node) if isinstance(node, tuple) else node)
            if own is None:
                return 1.0
        aspect = lambda box: max(box[2] - box[0], 1) / max(box[3] - box[1], 1)
        if abs(math.log(aspect(part.box) / aspect(own.box))) > 1.2:
            return 0.0
        # As much outline for its size as the operand's: one dot of 馬 is not 馬.
        density = lambda p: self.ink("", p) / max(p.box[2] - p.box[0] + p.box[3] - p.box[1], 1)
        if abs(math.log(density(part) / density(own))) > math.log(DENSER):
            return 0.0
        a, b = _shape(part), _shape(own)
        return float((a & b).sum()) / max(float((a | b).sum()), 1.0)

    def _cut(self, part: Part, node: tuple) -> tuple[list[Part], float] | None:
        """`part`, drawn as `node`, cut into its operands with the mean gap between them; None when
        no clean cut exists or a piece does not look like its operand."""
        if node[0] not in AXIS and node[0] not in ENCLOSE:
            return None
        if node[0] in ENCLOSE:
            best = None
            for outer, inner in enclosures(part, node[0]):
                a, b = self.likeness(outer, node[1]), self.likeness(inner, node[2])
                if a >= LIKENESS and b >= LIKENESS and (best is None or a + b > best[0]):
                    best = (a + b, [outer, inner])
            return None if best is None else (best[1], 0.0)
        axis = AXIS[node[0]]
        pieces = split(part, axis, len(node) - 1)
        if pieces is None or any(self.likeness(p, n) < LIKENESS for p, n in zip(pieces, node[1:])):
            return None
        spans = sorted((p.box[axis], p.box[axis + 2]) for p in pieces)
        return pieces, float(np.mean([spans[k + 1][0] - spans[k][1] for k in range(len(spans) - 1)]))

    def _host_node(self, char: str, tree: tuple, path: tuple) -> Host | None:
        """The node `path` leads to in `char`, cut into its operands, level by level."""
        part, node = self.font.glyph(char), tree
        for step in path:
            cut = self._cut(part, node)
            if cut is None:
                return None
            part, node = cut[0][step], node[1 + step]
        cut = self._cut(part, node)
        return None if cut is None else Host(char, cut[0], part.box, cut[1])

    def alternatives(self, node: Node, place: str) -> list[str]:
        """What an operand may be drawn as in `place`: itself, then its positional forms there
        (王 on the left as 𤣩, 水 on the left as 氵)."""
        out = [key(node)]
        if isinstance(node, tuple) and key(node) in self.by_sequence:
            # A sequence that describes a drawn character may be found as that character (⿰生生 as 甡).
            node = self.by_sequence[key(node)]
            out.append(node)
        if isinstance(node, str):
            # The operand's own positional forms, then those of the character it is a variant of (王 of 玉).
            for base in (node, *self.bases.get(node, ())):
                out.extend(form for form, where in self.forms.get(base, []) if where == place and form not in out)
        return out

    @cached_property
    def bases(self) -> dict[str, list[str]]:
        """variant → the characters the table gives it as a radical variant of: 王 → [玉]."""
        out: dict[str, list[str]] = {}
        for char, forms in self.forms.items():
            for form, _ in forms:
                out.setdefault(form, []).append(char)
        return out

    def _host(self, op: str, index: int, node: Node, siblings: list[Node], box: Box) -> tuple[Host, int] | None:
        """The drawn part closest to what `node` needs at `index` of `op` inside `box`: written as the
        operand or a positional form of it, beside siblings that hold about as much ink, at about
        the size and proportions of `box`."""
        if not self.hosted:
            return None
        hosts, _ = self._index
        want = sum(self.ink(s) for s in siblings)
        aspect = (box[2] - box[0]) / max(box[3] - box[1], 1)
        for written in self.alternatives(node, slot(op, index)):
            scored = []
            for char, tree, path, i in hosts.get((slot(op, index), written), []):
                if char in self.exclude:
                    continue
                here = tree
                for step in path:
                    here = here[1 + step]
                others = [n for k, n in enumerate(here[1:]) if k != i]
                exact = here[0] == op and [key(n) for n in others] == [key(s) for s in siblings]
                ratio = abs(math.log(max(sum(self.ink(s) for s in others), 1) / max(want, 1)))
                scored.append((not exact, ratio, len(path), char, tree, path, i))
            best = None
            for exact, ratio, depth, char, tree, path, i in sorted(scored, key=lambda r: r[:3])[:16]:
                found = self._host_node(char, tree, path)
                if found is None:
                    continue
                piece = found.pieces[i].box
                shape = abs(math.log(((piece[2] - piece[0]) / max(piece[3] - piece[1], 1)) / aspect))
                size = abs(math.log(max(piece[2] - piece[0], 1) * max(piece[3] - piece[1], 1)
                                    / max((box[2] - box[0]) * (box[3] - box[1]), 1)))
                rank = (exact, ratio + shape + 0.5 * size + 0.1 * depth)
                if best is None or rank < best[0]:
                    best = (rank, (found, i))
            if best:
                return best[1]
        return None

    def _template(self, op: str, children: list[Node], region: Box) -> tuple[Host, list[bool]] | None:
        """A drawn character with a node of `op` whose operands are most like `children`, cut into
        them: its layout is a type designer's balance for operands like these. Each flag says
        whether that operand is written there as the target writes it, so its piece is used as is."""
        if not self.hosted:
            return None
        _, templates = self._index
        places = [slot(op, i) for i in range(len(children))]
        wanted = [set(self.alternatives(c, p)) for c, p in zip(children, places)]
        inks = [max(self.ink(c), 1) for c in children]
        aspects = [self.aspect(c) for c in children]
        aspect = (region[2] - region[0]) / max(region[3] - region[1], 1)
        scored = []
        for char, tree, path in templates.get(op, []):
            if char in self.exclude:
                continue
            here = tree
            for step in path:
                here = here[1 + step]
            if len(here) != len(children) + 1:
                continue
            same = [key(t) in w for t, w in zip(here[1:], wanted)]
            gaps = [(abs(math.log(max(self.ink(t), 1) / i)), abs(math.log(self.aspect(t) / a)))
                    for t, i, a in zip(here[1:], inks, aspects)]
            if any(not s and max(g) > math.log(UNLIKE) for s, g in zip(same, gaps)):
                continue
            unlike = sum(0 if s else 1 + g[0] + g[1] for s, g in zip(same, gaps))
            scored.append((unlike, len(path), char, tree, path, same))
        best = None
        for unlike, depth, char, tree, path, same in sorted(scored, key=lambda r: r[:2])[:24]:
            found = self._host_node(char, tree, path)
            if found is None:
                continue
            frame = found.frame
            shape = abs(math.log(((frame[2] - frame[0]) / max(frame[3] - frame[1], 1)) / aspect))
            rank = unlike + shape + 0.1 * depth
            if best is None or rank < best[0]:
                best = (rank, (found, same))
        return best[1] if best else None

    def compose(self, sequence: str) -> list[Placed]:
        """The parts that draw `sequence`; LookupError when an operand can be drawn no way."""
        placed = self._node(parse(sequence), self.face)
        if self.weighted:
            self._balance(placed)
        return placed

    def _balance(self, placed: list[Placed]) -> None:
        """Each part's strokes brought to the width the whole character's density calls for: a part
        drawn in a sparser glyph is thinned, one squeezed along an axis is thickened along it. A
        part no thicker than a stroke along an axis keeps its weight there."""
        density = sum(self.ink("", p.part) * (p.scale[0] + p.scale[1]) / 2 for p in placed)
        ratio = [self.font.stem[axis][1] / self.font.stem[axis][0] - 1 for axis in (0, 1)]
        for p in placed:
            weight, target = [], list(p.target)
            for axis, s in enumerate(p.scale):
                extent = p.source[axis + 2] - p.source[axis]
                if extent <= 2 * self.font.stem[axis][0]:
                    # A lone stroke across this axis: drawn at the width the density calls for, not scaled.
                    width = extent * stem(density, axis) / stem(p.native, axis)
                    middle = (target[axis] + target[axis + 2]) / 2
                    target[axis], target[axis + 2] = middle - width / 2, middle + width / 2
                    weight.append(0.0)
                    continue
                own = stem(p.native, axis)
                weight.append(float(np.clip((stem(density, axis) / (s * own) - 1) / ratio[axis], -0.6, 1.0)))
            p.target = tuple(target)
            p.weight = (weight[0], weight[1])

    def _node(self, node: Node, region: Box) -> list[Placed]:
        whole = node if isinstance(node, str) else self.by_sequence.get(key(node))
        if whole is not None and whole not in self._expanding and (isinstance(node, str) or whole not in self.exclude):
            glyph = self.font.glyph(whole)
            tree = node if isinstance(node, tuple) else None
            if tree is None and whole in self.sequences and whole not in self.exclude:
                try:
                    tree = parse(self.sequences[whole])
                except ValueError as error:
                    if glyph is None:
                        raise LookupError(f"{whole} has no glyph, and its sequence is malformed: {error}") from error
            # A glyph that would be squeezed out of shape is drawn from its sequence instead, whose
            # parts come from characters that draw them in such a box (鮮 flat on top as ⿰魚羊).
            squeezed = glyph is not None and self.hosted and self._stretch(glyph.box, region) > STRETCH
            if glyph is not None and not (squeezed and tree is not None):
                return [self._place(glyph, region, self.ink(whole), f"glyph {whole}")]
            if tree is None:
                raise LookupError(f"No glyph or sequence draws {whole}.")
            # A character whose sequence leads back to itself is not expanded again.
            self._expanding.add(whole)
            try:
                return self._node(tree, region)
            except LookupError:
                if glyph is None:
                    raise
                return [self._place(glyph, region, self.ink(whole), f"glyph {whole}")]
            finally:
                self._expanding.discard(whole)
        if isinstance(node, str):
            glyph = self.font.glyph(node)
            if glyph is None:
                raise LookupError(f"No glyph or sequence draws {node}.")
            return [self._place(glyph, region, self.ink(node), f"glyph {node}")]
        op, children = node[0], list(node[1:])
        if op not in AXIS and op not in ENCLOSE:
            raise LookupError(f"{op} is not laid out.")
        found = self._template(op, children, region)
        if found is None and len(children) == 3:
            # Three operands no drawn character lays out as three are laid out as two, the last two together.
            binary = {"⿲": "⿰", "⿳": "⿱"}[op]
            return self._node((binary, children[0], (binary, children[1], children[2])), region)
        if found is None:
            return self._shares(op, children, region)
        host, same = found
        placed = []
        for i, (child, piece) in enumerate(zip(children, host.pieces)):
            box = mapped(piece.box, host.frame, region)
            if same[i]:
                placed.append(Placed(piece, piece.box, box, native=self.ink(host.char), origin=f"teacher {host.char}"))
                continue
            part = self._host(op, i, child, children[:i] + children[i + 1:], box)
            if part is None:
                placed.extend(self._node(child, box))
                continue
            drawn, k = part
            source = drawn.pieces[k]
            if op in AXIS:
                # Along the axis the teacher's share; across it, the extent the part's own host gave it.
                axis = AXIS[op]
                frame, target = drawn.frame, list(box)
                lo, span = region[1 - axis], region[3 - axis] - region[1 - axis]
                width = frame[3 - axis] - frame[1 - axis]
                target[1 - axis] = lo + (source.box[1 - axis] - frame[1 - axis]) / width * span
                target[3 - axis] = lo + (source.box[3 - axis] - frame[1 - axis]) / width * span
                target = tuple(target)
            else:
                target = fitted(source.box, box)
            placed.append(Placed(source, source.box, target, native=self.ink(drawn.char), origin=f"host {drawn.char}"))
        return placed

    @staticmethod
    def _stretch(source: Box, target: Box) -> float:
        """How much a part from `source` changes its proportions in `target`."""
        sx = (target[2] - target[0]) / max(source[2] - source[0], 1)
        sy = (target[3] - target[1]) / max(source[3] - source[1], 1)
        return max(sx / sy, sy / sx)

    def _place(self, part: Part, target: Box, native: float, origin: str) -> Placed:
        """A whole glyph drawn into `target`, its proportions changed by at most `STRETCH`."""
        return Placed(part, part.box, fitted(part.box, target), native=native, origin=origin)

    def _shares(self, op: str, children: list[Node], region: Box) -> list[Placed]:
        """With no drawn character to follow, operands share the region by their ink along an
        aligned operator, and an enclosed operand takes the room `INSIDE` gives it."""
        if op in ENCLOSE:
            x0, y0, x1, y1 = INSIDE[op]
            w, h = region[2] - region[0], region[3] - region[1]
            room = (region[0] + x0 * w, region[1] + y0 * h, region[0] + x1 * w, region[1] + y1 * h)
            return self._node(children[0], region) + self._node(children[1], room)
        axis = AXIS[op]
        ink = [self.ink(c) ** 0.5 for c in children]
        gap = 0.04
        fit = (1 - gap * (len(children) - 1)) / sum(ink)
        lo, span = region[axis], region[axis + 2] - region[axis]
        placed, at = [], 0.0
        for child, share in zip(children, ink):
            a, b = at, at + share * fit
            at = b + gap
            if axis == 1:
                a, b = 1 - b, 1 - a
            box = list(region)
            box[axis], box[axis + 2] = lo + a * span, lo + b * span
            placed.extend(self._node(child, tuple(box)))
        return placed


# ------------------------------------------------------------------ output
def _segments(ops: list[tuple[str, int, bool]], points: np.ndarray) -> list[tuple[str, list[np.ndarray]]]:
    """A contour as ('M' | 'L' | 'Q', points) segments, with TrueType's implied on-curve points made explicit."""
    at, out = 0, []
    for op, n, implied in ops:
        pts = [np.asarray(p) for p in points[at:at + n]]
        at += n
        if op == "moveTo":
            out.append(("M", [pts[0]]))
        elif op == "lineTo":
            out.append(("L", [pts[0]]))
        elif op == "qCurveTo":
            if implied:
                # Only off-curve points: the contour starts midway between the last and the first.
                start = (pts[-1] + pts[0]) / 2
                out.append(("M", [start]))
                pts = pts + [start]
            *offs, end = pts
            for k, off in enumerate(offs):
                out.append(("Q", [off, (off + offs[k + 1]) / 2 if k + 1 < len(offs) else end]))
        else:
            raise ValueError(f"{op} outlines are not read.")
    return out


def svg_path(placed: list[Placed]) -> str:
    """The composed outlines as one SVG path, in a 1000 × 1000 box with y down from the em's top.
    Fill it with the nonzero rule: strokes and parts overlap."""
    flip = lambda p: f"{p[0]:.1f} {ASCENT - p[1]:.1f}"
    out = []
    for item in placed:
        for contour, points in zip(item.part.contours, item.contours()):
            out.extend(kind + " ".join(map(flip, pts)) for kind, pts in _segments(contour.ops, points))
            out.append("Z")
    return "".join(out)


def _rings(contours: list[Contour], points: list[np.ndarray], steps: int = 6) -> list[np.ndarray]:
    rings = []
    for contour, pts in zip(contours, points):
        ring, last = [], None
        for kind, seg in _segments(contour.ops, pts):
            if kind in "ML":
                last = seg[0]
                ring.append(last)
            else:
                off, on = seg
                for t in np.linspace(0, 1, steps + 1)[1:]:
                    ring.append((1 - t) ** 2 * last + 2 * (1 - t) * t * off + t ** 2 * on)
                last = on
        if len(ring) >= 3:
            rings.append(np.array(ring))
    return rings


def fill(rings: list[np.ndarray], size: int, box: Box) -> np.ndarray:
    """A size × size mask of `box` (x0, y0, x1, y1, y up), filled by the nonzero rule at pixel centres."""
    x0, y0, x1, y1 = box
    xs = x0 + (np.arange(size) + 0.5) * (x1 - x0) / size
    ys = y1 - (np.arange(size) + 0.5) * (y1 - y0) / size
    if not rings:
        return np.zeros((size, size), bool)
    a = np.concatenate(rings)
    b = np.concatenate([np.roll(r, -1, axis=0) for r in rings])
    winding = np.zeros((size, size), np.int32)
    for row, y in enumerate(ys):
        up = (a[:, 1] <= y) & (b[:, 1] > y)
        down = (a[:, 1] > y) & (b[:, 1] <= y)
        crossing = up | down
        if not crossing.any():
            continue
        p, q = a[crossing], b[crossing]
        x = p[:, 0] + (y - p[:, 1]) * (q[:, 0] - p[:, 0]) / (q[:, 1] - p[:, 1])
        sign = np.where(up[crossing], 1, -1)
        winding[row] = (sign[:, None] * (x[:, None] < xs[None, :])).sum(axis=0)
    return winding != 0


def raster(placed: list[Placed], size: int = 128) -> np.ndarray:
    """A size × size mask of the em, the parts filled and joined."""
    box = (0.0, ASCENT - EM, EM, ASCENT)
    mask = np.zeros((size, size), bool)
    for item in placed:
        mask |= fill(_rings(item.part.contours, item.contours()), size, box)
    return mask


def _shape(part: Part, size: int = 40) -> np.ndarray:
    """A part's ink stretched to fill a size × size square, thickened by a pixel so that a narrowed
    copy of a stroke still meets the stroke it copies."""
    mask = fill(_rings(part.contours, [c.light for c in part.contours], steps=2), size, part.box)
    grown = mask.copy()
    grown[1:] |= mask[:-1]
    grown[:-1] |= mask[1:]
    grown[:, 1:] |= grown[:, :-1].copy()
    grown[:, :-1] |= grown[:, 1:].copy()
    return grown


def stems(mask: np.ndarray, unit: float) -> tuple[float, float]:
    """The usual stroke width in a mask, in font units (`unit` per pixel): across x (the stems that
    run along y) and across y. Each is the most common length of the short ink runs along rows or
    columns, refined by its neighbours; long runs are strokes seen lengthwise."""
    out = []
    for lines in (mask, mask.T):
        edges = np.diff(np.pad(lines.astype(np.int8), ((0, 0), (1, 1))), axis=1)
        lengths = (np.nonzero(edges == -1)[1] - np.nonzero(edges == 1)[1])
        lengths = lengths[(lengths >= 2) & (lengths <= 40)]
        if not len(lengths):
            out.append(float("nan"))
            continue
        counts = np.bincount(lengths)
        mode = int(np.argmax(counts))
        near = np.arange(max(mode - 2, 0), min(mode + 3, len(counts)))
        out.append(float((near * counts[near]).sum() / counts[near].sum()) * unit)
    return out[0], out[1]
