"""Draw a character Unicode lacks from its Ideographic Description Sequence, in GenZui Sans.

GenZui Sans takes its ideographs unchanged from Noto Sans JP at weight 400, which in turn draws them
as Noto Sans CJK JP does. The composer reads Noto Sans CJK JP, the variable font that draws 30,289
ideographs to Noto Sans JP's 13,742 with the same outlines, so each outline is known at weight 400
and at `HEAVY` with the same points. A sequence (⿰亻哥) is drawn in three steps:

1. Parts. Each operand is cut from a character the font draws with that operand in the same place:
   亻 in ⿰亻哥 from the left of a character written ⿰亻…, 哥 from the right of 謌. A type designer
   has already narrowed and lightened a part drawn there. The place may lie deep in the host's own
   sequence, and the host is cut level by level: along the operator's axis where no outline crosses
   (`split`), or round an enclosed operand by how far inside the enclosing sides each outline lies
   (`enclosures`). A cut is used only when each piece looks like its operand's own glyph. Among
   hosts, one beside the same siblings comes first, then one whose siblings hold about as much ink
   and whose node needs the least scaling. An operand with no host is drawn from its own glyph, or
   from its own sequence the same way.
2. Layout. Along a side-by-side or stacked operator, each part is as long as its host made it, or
   for one with no host as long for its ink as the others are for theirs; the lengths and the gaps
   the hosts left are then scaled together to fill the region. Across the axis a part keeps the
   extent its host gave it. An enclosed operand fills the room its enclosing operand's host left.
3. Weight. A part scaled by s along an axis has its strokes across that axis s times as thick. The
   deltas towards `HEAVY` are added along each axis by as much as brings its strokes back to their
   width at 400, thinned by s ** `Composer.thinning` as a type designer thins a crowded part. A part
   no thicker than a stroke along an axis (一 across y) keeps its thickness there.

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
            if at < len(chars) and 0xFE00 <= ord(chars[at]) <= 0xFE0F:
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


REGIONS = re.compile(r"^(.*?)\(([A-Z]+)\)$")


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
        self.light = self.font.getGlyphSet(location={"wght": 400})
        self.heavy = self.font.getGlyphSet(location={"wght": HEAVY})
        self._parts: dict[str, Part | None] = {}
        # Stem widths at 400 and at HEAVY: 丨's width across x, 一's height across y.
        self.stem = (self._stem("丨", 0), self._stem("一", 1))

    def _stem(self, char: str, axis: int) -> tuple[float, float]:
        part = self.glyph(char)
        heavy = np.concatenate([c.heavy for c in part.contours])
        return part.box[axis + 2] - part.box[axis], float(heavy[:, axis].max() - heavy[:, axis].min())

    def has(self, char: str) -> bool:
        return len(char) == 1 and ord(char) in self.cmap

    def glyph(self, char: str) -> Part | None:
        if char not in self._parts:
            part = None
            if self.has(char):
                name = self.cmap[ord(char)]
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
    def inside(a: Contour, b: Contour) -> bool:
        return a is not b and a.box[0] >= b.box[0] and a.box[1] >= b.box[1] and a.box[2] <= b.box[2] and a.box[3] <= b.box[3]

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


def split(part: Part, axis: int, pieces: int, stem: float) -> list[Part] | None:
    """`part` cut into `pieces` along `axis` (x 0, y 1), in reading order: left to right, top to bottom.

    The cuts are where the outlines cross them least; a cut that passes through an outline by more
    than half a stem gives None, as does an empty piece.
    """
    units = _units(part.contours)
    if len(units) < pieces:
        return None
    spans = [_span(u, axis) for u in units]
    centres = sorted({(a + b) / 2 for a, b in spans})
    cuts = [(centres[k] + centres[k + 1]) / 2 for k in range(len(centres) - 1)]

    def crossing(cut: float) -> float:
        return sum(max(0.0, min(cut - a, b - cut)) for a, b in spans)

    best = min(((sum(crossing(c) for c in chosen), chosen) for chosen in combinations(cuts, pieces - 1)), default=None)
    if best is None or best[0] > stem / 2:
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
@dataclass
class Placed:
    """A part drawn into `target` from `source`, thickened by `weight`: the share of the way to
    `HEAVY` its points move, along x and along y."""

    part: Part
    source: Box
    target: Box
    weight: tuple[float, float] = (0.0, 0.0)

    def contours(self) -> list[np.ndarray]:
        scale = np.array([(self.target[2] - self.target[0]) / max(self.source[2] - self.source[0], 1),
                          (self.target[3] - self.target[1]) / max(self.source[3] - self.source[1], 1)])
        centre = np.array([(self.source[0] + self.source[2]) / 2, (self.source[1] + self.source[3]) / 2])
        goal = np.array([(self.target[0] + self.target[2]) / 2, (self.target[1] + self.target[3]) / 2])
        weight = np.array(self.weight)
        return [(c.light + (c.heavy - c.light) * weight - centre) * scale + goal for c in self.part.contours]


@dataclass
class Composer:
    font: Font
    sequences: dict[str, str]
    #: A part shrunk by s is drawn with stems s ** thinning as thick as at full size.
    thinning: float = 0.15
    #: Characters no part is cut from: the character being drawn, when an encoded one is redrawn to test.
    exclude: set[str] = field(default_factory=set)
    #: Whether parts are cut from hosts and their strokes brought back to weight. With both off, each
    #: operand's own glyph is squeezed into its share, the plainest way to compose, for comparison.
    hosted: bool = True
    weighted: bool = True

    @cached_property
    def by_sequence(self) -> dict[str, str]:
        """The drawn character each sequence describes."""
        return {seq: char for char, seq in self.sequences.items() if self.font.has(char)}

    @cached_property
    def hosts(self) -> dict[tuple[str, int, str], list[tuple[str, tuple, tuple]]]:
        """(operator, position, operand) → the drawn characters with that operand there, at any depth,
        each with its tree and the path of positions down to the node that holds it."""
        out: dict[tuple[str, int, str], list] = {}

        def walk(char: str, tree: tuple, node: Node, path: tuple) -> None:
            if not isinstance(node, tuple) or (node[0] not in AXIS and node[0] not in ENCLOSE):
                return
            for i, child in enumerate(node[1:]):
                out.setdefault((node[0], i, key(child)), []).append((char, tree, path))
                walk(char, tree, child, path + (i,))

        for char, seq in self.sequences.items():
            if self.font.has(char):
                try:
                    tree = parse(seq)
                except ValueError:
                    continue
                walk(char, tree, tree, ())
        return out

    @cached_property
    def face(self) -> Box:
        """The box a full ideograph fills."""
        boxes = [g.box for g in map(self.font.glyph, FULL) if g]
        return tuple(float(v) for v in np.median(np.array(boxes), axis=0))

    def ink(self, node: Node, part: Part | None = None) -> float:
        """How much a part holds: its outlines' length, which grows with its strokes. An operand the
        font has no glyph for is measured by its own sequence, else as 永."""
        if part is None and isinstance(node, str):
            part = self.font.glyph(node)
            if part is None and node in self.sequences:
                try:
                    return self.ink(parse(self.sequences[node]))
                except ValueError:
                    pass
        if part is None:
            return sum(self.ink(n) for n in node[1:]) if isinstance(node, tuple) else self.ink("永")
        return sum(float(np.linalg.norm(np.diff(np.vstack([c.light, c.light[:1]]), axis=0), axis=1).sum())
                   for c in part.contours)

    def likeness(self, part: Part, node: Node) -> float:
        """How much a piece looks like its operand's own glyph, each stretched to fill a square; 1
        for an operand with no glyph. Proportions far from the operand's (a lone stroke of 口) count
        as no likeness, since stretching would hide them."""
        own = self.font.glyph(node) if isinstance(node, str) else None
        if own is None:
            return 1.0
        aspect = lambda box: max(box[2] - box[0], 1) / max(box[3] - box[1], 1)
        if abs(math.log(aspect(part.box) / aspect(own.box))) > 1.2:
            return 0.0
        a, b = _shape(part), _shape(own)
        return float((a & b).sum()) / max(float((a | b).sum()), 1.0)

    def _cut(self, part: Part, node: tuple) -> tuple[list[Part], float] | None:
        """`part`, drawn as `node`, cut into its operands with the mean gap between them; None when
        no clean cut exists or a piece does not look like its operand."""
        if node[0] in ENCLOSE:
            best = None
            for outer, inner in enclosures(part, node[0]):
                a, b = self.likeness(outer, node[1]), self.likeness(inner, node[2])
                if a >= LIKENESS and b >= LIKENESS and (best is None or a + b > best[0]):
                    best = (a + b, [outer, inner])
            return None if best is None else (best[1], 0.0)
        axis = AXIS[node[0]]
        pieces = split(part, axis, len(node) - 1, self.font.stem[axis][0])
        if pieces is None or any(self.likeness(p, n) < LIKENESS for p, n in zip(pieces, node[1:])):
            return None
        spans = sorted((p.box[axis], p.box[axis + 2]) for p in pieces)
        return pieces, float(np.mean([spans[k + 1][0] - spans[k][1] for k in range(len(spans) - 1)]))

    def _host_part(self, char: str, tree: tuple, path: tuple, index: int) -> Host | None:
        part, node = self.font.glyph(char), tree
        for step in path:
            cut = self._cut(part, node)
            if cut is None:
                return None
            part, node = cut[0][step], node[1 + step]
        cut = self._cut(part, node)
        return None if cut is None else Host(cut[0], index, part.box, cut[1])

    def _host(self, op: str, index: int, node: Node, siblings: list[Node], region: Box) -> Host | None:
        if not self.hosted:
            return None
        want = sum(self.ink(s) for s in siblings)
        scored = []
        for char, tree, path in self.hosts.get((op, index, key(node)), []):
            if char in self.exclude:
                continue
            here = tree
            for step in path:
                here = here[1 + step]
            others = [n for k, n in enumerate(here[1:]) if k != index]
            exact = [key(n) for n in others] == [key(s) for s in siblings]
            ratio = abs(math.log(max(sum(self.ink(s) for s in others), 1) / max(want, 1)))
            scored.append((not exact, ratio, len(path), char, tree, path))
        best = None
        for exact, ratio, depth, char, tree, path in sorted(scored, key=lambda r: r[:3])[:16]:
            found = self._host_part(char, tree, path, index)
            if found is None:
                continue
            frame = found.frame
            scale = (abs(math.log(max(frame[2] - frame[0], 1) / max(region[2] - region[0], 1)))
                     + abs(math.log(max(frame[3] - frame[1], 1) / max(region[3] - region[1], 1))))
            rank = (exact, ratio + scale + 0.1 * depth)
            if best is None or rank < best[0]:
                best = (rank, found)
        return best[1] if best else None

    def compose(self, sequence: str) -> list[Placed]:
        """The parts that draw `sequence`; LookupError when an operand can be drawn no way."""
        return self._node(parse(sequence), self.face)

    def _node(self, node: Node, region: Box) -> list[Placed]:
        whole = node if isinstance(node, str) else self.by_sequence.get(key(node))
        if whole is not None and whole not in self.exclude or isinstance(node, str):
            glyph = self.font.glyph(whole)
            if glyph is not None:
                return [self._weighted(glyph, glyph.box, region)]
            if whole in self.sequences and whole not in self.exclude:
                return self._node(parse(self.sequences[whole]), region)
            raise LookupError(f"No glyph or sequence draws {whole}.")
        op, children = node[0], list(node[1:])
        if op in ENCLOSE:
            return self._enclosed(op, children[0], children[1], region)
        if op not in AXIS:
            raise LookupError(f"{op} is not laid out.")
        return self._aligned(op, children, region)

    def _aligned(self, op: str, children: list[Node], region: Box) -> list[Placed]:
        axis = AXIS[op]
        found = [self._host(op, i, c, children[:i] + children[i + 1:], region) for i, c in enumerate(children)]
        # Shares of the region along the axis in reading order (top first), and across it.
        along, across, gaps = [], [], []
        for f in found:
            if f is None:
                along.append(None)
                across.append((0.0, 1.0))
                continue
            box, frame = f.piece.box, f.frame
            a, b = ((box[axis] - frame[axis]) / (frame[axis + 2] - frame[axis]),
                    (box[axis + 2] - frame[axis]) / (frame[axis + 2] - frame[axis]))
            along.append((1 - b, 1 - a) if axis == 1 else (a, b))
            across.append(((box[1 - axis] - frame[1 - axis]) / (frame[3 - axis] - frame[1 - axis]),
                           (box[3 - axis] - frame[1 - axis]) / (frame[3 - axis] - frame[1 - axis])))
            gaps.append(f.gap / (frame[axis + 2] - frame[axis]))
        ink = [self.ink(c, f.piece if f else None) ** 0.5 for c, f in zip(children, found)]
        hosted = [k for k, v in enumerate(along) if v]
        per_ink = np.mean([(along[k][1] - along[k][0]) / ink[k] for k in hosted]) if hosted else 1.0 / sum(ink)
        lengths = [(v[1] - v[0]) if v else per_ink * ink[k] for k, v in enumerate(along)]
        gap = float(np.mean(gaps)) if gaps else 0.04
        start = along[0][0] if along[0] else 0.0
        end = along[-1][1] if along[-1] else 1.0
        fit = (end - start - gap * (len(children) - 1)) / sum(lengths)
        lo, span = region[axis], region[axis + 2] - region[axis]
        clo, cspan = region[1 - axis], region[3 - axis] - region[1 - axis]
        placed, at = [], start
        for child, f, length, (c0, c1) in zip(children, found, lengths, across):
            a, b = at, at + length * fit
            at = b + gap
            if axis == 1:
                a, b = 1 - b, 1 - a
            target = [0.0] * 4
            target[axis], target[axis + 2] = lo + a * span, lo + b * span
            target[1 - axis], target[3 - axis] = clo + c0 * cspan, clo + c1 * cspan
            if f:
                placed.append(self._weighted(f.piece, f.piece.box, tuple(target)))
            else:
                placed.extend(self._node(child, tuple(target)))
        return placed

    def _enclosed(self, op: str, outer: Node, inner: Node, region: Box) -> list[Placed]:
        placed = []
        f = self._host(op, 0, outer, [inner], region)
        if f:
            placed.append(self._weighted(f.piece, f.piece.box, mapped(f.piece.box, f.frame, region)))
            room = mapped(f.pieces[1].box, f.frame, region)
        else:
            placed.extend(self._node(outer, region))
            x0, y0, x1, y1 = INSIDE[op]
            w, h = region[2] - region[0], region[3] - region[1]
            room = (region[0] + x0 * w, region[1] + y0 * h, region[0] + x1 * w, region[1] + y1 * h)
        g = self._host(op, 1, inner, [outer], room)
        if g:
            placed.append(self._weighted(g.piece, g.piece.box, room))
        else:
            placed.extend(self._node(inner, room))
        return placed

    def _weighted(self, part: Part, source: Box, target: Box) -> Placed:
        target = list(target)
        for axis in (0, 1):
            extent = source[axis + 2] - source[axis]
            if extent <= 2 * self.font.stem[axis][0]:
                middle = (target[axis] + target[axis + 2]) / 2
                target[axis], target[axis + 2] = middle - extent / 2, middle + extent / 2
        if not self.weighted:
            return Placed(part, source, tuple(target))
        weight = []
        for axis in (0, 1):
            s = (target[axis + 2] - target[axis]) / max(source[axis + 2] - source[axis], 1)
            light, heavy = self.font.stem[axis]
            want = light * s ** self.thinning / s
            weight.append(float(np.clip((want - light) / (heavy - light), -0.6, 1.0)))
        return Placed(part, source, tuple(target), (weight[0], weight[1]))


@dataclass
class Host:
    """A host cut into the operands of the node holding the wanted one: the pieces, which of them is
    wanted, the box of the node's ink, and the mean gap between the pieces."""

    pieces: list[Part]
    index: int
    frame: Box
    gap: float

    @property
    def piece(self) -> Part:
        return self.pieces[self.index]


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
