"""Propose where the ink of a joined vertical crop divides into the characters a reviewer read.

A detector box sometimes holds two, three or four characters set as one blob — シヤ, さへ, に四, キナ,
菰を, シヨロ — and the alignment can only record it as one unit, so a reviewer has to draw the child
boxes by hand. This module proposes them: given the crop and the characters somebody read in it, it
returns crop-local child boxes with the evidence for each cut.

What it does and does not decide. The *reading* comes from outside — a human's feedback or a sequence
OCR — and this module never invents it; it answers where the boundaries are for a reading it was
given. That is the half a language model cannot supply: a bigram prior scores a pair of characters,
and every candidate cut of the same crop yields the same pair, so the prior is flat over the
boundaries. The boundaries come from the ink (a horizontal profile down the crop and the low-ink
valleys in it) and from a character model scoring the sub-crops a cut would produce — and a child the
model's most likely answer for is not the expected character is refused however much mass that
character was given.

Vertical text stacks characters top to bottom, so a boundary between two of them is a *horizontal*
line: the profile is the count of dark pixels in each row, and a valley is a row the strokes do not
cross. An equal division of the crop is never proposed. A crop with no valley is reported as
unresolved even when the expected text says exactly how many characters it holds, because "two
characters long" is not evidence of where the boundary is; that is the mistake this module exists to
avoid.

The character model is injected, so this module holds no model and runs none:

    def scorer(crops: Sequence[Image.Image]) -> Sequence[Mapping[str, float]]
        One mapping for each crop, code point ("U+30B7") to probability mass. Called in one batch per
        candidate boundary, so a caller that runs a model on a GPU decides how large a batch it takes.

    def ligature(text: str) -> LigatureCheck
        The code point of the ligature `text` is, when the character layer says it is one, and
        whether the layer could answer at all. The default reads `data/vocab/ligatures.yaml` through
        `refs.ligature`; a tree without that overlay answers `available=False`, and a split is then
        withheld rather than accepted, because a single encoded ligature (𪜈, ゟ, ヿ, the Unicode 18
        digraphs) must never be cut into the characters it is made of and an unavailable overlay
        cannot rule that out.

    def candidates_of(character: str) -> Sequence[str]
        The code points the expected character may be. The default is the character's own code points
        in both Unicode spellings — が is U+304C and U+304B U+3099, one character — and not the other
        forms that read the same; `variant_candidates` is the phonetic set for a caller that wants it.

Nothing here touches a dataset, a store or a model. It reads one crop and returns a proposal; whether
to apply it, and how to record it, belongs to the caller.
"""

from __future__ import annotations

import math
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol

from PIL import Image
from pydantic import BaseModel, Field

from .schema import Box

#: How many characters a joined crop may be read as. Two is the common case; four is where a human
#: stops being able to say which character is which without cutting the ink first.
MIN_CHARACTERS = 2
MAX_CHARACTERS = 4
#: How far apart, in grey levels, a crop's paper class and ink class have to be before anything in it
#: counts as ink. Below this the crop is blank paper, a wash, or scanner noise, and it reads as blank
#: however dark the paper itself is.
MIN_SEPARATION = 24.0


class CharacterScorer(Protocol):
    """What the splitter needs from a character model.

    One call takes a batch of crops and returns, for each, a mapping from code point to probability
    mass. A crop the model cannot identify answers whatever its abstention class is worth; the
    splitter only ever sums the mass of the code points a character may be, so an abstention is simply
    a low score rather than a special case.
    """

    def __call__(self, crops: Sequence[Image.Image]) -> Sequence[Mapping[str, float]]: ...


class LigatureGuard(Protocol):
    """What the splitter needs from the character layer to keep a ligature whole."""

    def __call__(self, text: str) -> LigatureCheck: ...


class Limits(BaseModel):
    """Every threshold the proposal uses, so a caller can see and change them.

    The defaults are conservative: a cut has to sit in a row with less than half the crop's median
    ink, deeper than the rows around it, away from both ends, and the characters either side have to
    be read clearly enough to beat their alternatives by a margin.
    """

    #: A cut may sit only in a row whose ink is at most this share of the crop's median row ink.
    max_cut_ink_share: float = 0.5
    #: How much less ink than its neighbourhood a row has to hold to be a valley at all.
    min_valley_depth: float = 0.35
    #: The share of the mean character height the smallest child may have, and the largest.
    min_child_share: float = 0.35
    max_child_share: float = 2.0
    #: A child narrower or shorter than this many pixels is not a character box.
    min_child_pixels: int = 4
    #: The ink in the crop's first or last row, as a share of its widest row, above which a stroke
    #: runs out of the box and a child is clipped. Measured against the widest row rather than the
    #: median because a sparse character's median row is a thin stroke: what says "cut through the
    #: body" is an edge as inked as the character's heaviest row *and* not thinning towards the edge.
    max_edge_ink_share: float = 0.6
    #: The mass of the expected characters below which the cut is weak.
    min_character_mass: float = 0.05
    #: Nats by which the best boundary has to beat the next one before it is called decided.
    min_margin: float = 0.5
    #: At most this many blank runs are taken as candidate boundaries, deepest first.
    max_boundaries: int = 8
    #: At most this many candidate boundaries are scored.
    max_candidates: int = 64
    #: Rows are smoothed over this window before the runs are read, so a one-row speck is not one.
    smooth: int = 3


class LigatureCheck(BaseModel):
    """What the character layer says about a string: whether it is one ligature, and whether it knows."""

    code_point: str | None = Field(
        default=None, description="the ligature this text is, when the layer says it is one")
    available: bool = Field(
        default=True, description="whether the layer could answer at all; false means unknown")


class CutScore(BaseModel):
    """One proposed boundary and the ink evidence for it."""

    y: int
    ink_share: float = Field(description="the row's ink as a share of the crop's median row ink")
    depth: float = Field(description="how much less ink the row holds than its neighbourhood")
    prominence: float = Field(description="the weaker of the two rises away from the row")


class ChildScore(BaseModel):
    """One proposed child and what the character model read in it."""

    text: str
    box: Box
    mass: float = Field(description="the mass of the code points this character may be")
    top: str | None = Field(default=None, description="the code point with the most mass")
    canonical: bool = Field(
        default=False, description="whether the most mass went to the character's own code point")
    ink: int = Field(default=0, description="dark pixels in the child")


class Alternative(BaseModel):
    """A boundary set that was scored and did not win, kept so a reader can see the ambiguity."""

    cuts: list[int]
    cost: float
    margin: float | None = None


class SplitProposal(BaseModel):
    """What the splitter concluded about one crop, and everything it decided on.

    `boxes` are local to the crop: add the parent box's `x` and `y` to place them on the page
    (`to_page`). `accepted` false is not a failure — it is the module declining to cut ink it cannot
    justify — and `reason` says which rule stopped it and what the alternatives were.
    """

    accepted: bool
    reason: str = ""
    text: list[str] = Field(default_factory=list, description="one grapheme a child, as read")
    boxes: list[Box] = Field(default_factory=list, description="child boxes, local to the crop")
    cuts: list[int] = Field(default_factory=list, description="boundary y positions, local to the crop")
    cut_scores: list[CutScore] = Field(default_factory=list)
    children: list[ChildScore] = Field(default_factory=list)
    alternatives: list[Alternative] = Field(default_factory=list)
    margin: float | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)

    def crops(self, source: Image.Image) -> list[Image.Image]:
        """The child crops of an accepted proposal, cut from the source crop."""
        return [source.crop((box.x, box.y, box.x + box.w, box.y + box.h)) for box in self.boxes]


def to_page(boxes: Sequence[Box], parent: Box) -> list[Box]:
    """Child boxes local to a crop, placed on the page the crop was cut from."""
    return [Box(x=parent.x + box.x, y=parent.y + box.y, w=box.w, h=box.h) for box in boxes]


# --- the reading, and the guard that keeps a ligature whole ---------------------------------------


def graphemes(text: str) -> list[str]:
    """The characters of a reading, a base character with the marks that follow it as one.

    The text is normalised to NFC first, so か + U+3099 and が are the same character and a caller
    does not have to know which spelling its OCR or its reviewer produced. Marks NFC leaves
    decomposed — a combination Unicode has no precomposed form for — are then folded into the base
    they follow, and so are zero-width joiners and variation selectors: a mark is not ink of its own,
    and the splitter cuts ink.
    """
    found: list[str] = []
    for char in unicodedata.normalize("NFC", text):
        if found and (unicodedata.combining(char) or char in "\u200d\ufe00\ufe0f"):
            found[-1] = unicodedata.normalize("NFC", found[-1] + char)
        else:
            found.append(char)
    return found


def default_ligature_guard(text: str) -> LigatureCheck:
    """Ask the character layer whether `text` is one encoded ligature.

    The layer's answer is `data/vocab/ligatures.yaml` through `refs.ligature` and `refs.ligatures`:
    𪜈 is ト + モ set as one character, ゟ is よ + り, and the Unicode 18 katakana digraphs are the
    same relation. That overlay is the authority, not a range list written here and not the grapheme
    relation, which answers a different question. A tree without the overlay answers `available=False`
    and the caller's split is withheld: not knowing is a reason not to cut.
    """
    from . import refs

    single = getattr(refs, "ligature", None)
    table = getattr(refs, "ligatures", None)
    if single is None or table is None:
        return LigatureCheck(available=False)
    if len(text) == 1:
        found = single(refs.to_code_point(text))
        return LigatureCheck(code_point=None if found is None else refs.to_code_point(text))
    code_points = "".join(text)
    for code_point, ligature in table().items():
        if "".join(refs.to_char(item) for item in ligature.components) == code_points:
            return LigatureCheck(code_point=code_point)
    return LigatureCheck()


def default_candidates(character: str) -> list[str]:
    """The code points one explicit character is, in both of Unicode's spellings of it.

    The expected reading names a *written identity*, so the default does not widen it to the other
    forms that read the same: a hentaigana of し is a different character and a model that answers it
    for a crop the reviewer called し has answered something else. What is included is the same
    character spelled the other way — が as U+304C and as U+304B U+3099 — because a class list may
    hold either and both are one character. The composed spelling is read first and the two inputs
    give the same set whichever way the caller wrote the character. A caller that wants a phonetic set
    instead injects `variant_candidates`.
    """
    from . import refs

    spellings = [unicodedata.normalize("NFC", character),
                 unicodedata.normalize("NFD", character)]
    found: list[str] = []
    for spelling in spellings:
        for char in spelling:
            code_point = refs.to_code_point(char)
            if code_point not in found:
                found.append(code_point)
    return found


def variant_candidates(character: str) -> list[str]:
    """Every form that reads as `character`, the ordinary one first: the opt-in phonetic set.

    This is `refs.candidates`: the hentaigana and alternate katakana a transcriber may have written
    for the same reading. It is not the default, because scoring a crop against every form of a
    reading turns "the model cannot tell し from a hentaigana of し" into an accepted split; a caller
    reading forms rather than identities asks for it explicitly.
    """
    from . import refs

    if len(character) == 1:
        found = refs.candidates(character)
        if found:
            return list(found)
    return default_candidates(character)


# --- the ink -------------------------------------------------------------------------------------


def ink_profile(crop: Image.Image, *, smooth: int = 3,
                min_separation: float = MIN_SEPARATION) -> list[float]:
    """The dark pixels in each row of a crop, counting a grey pixel by how much darker it is than paper.

    Vertical text is read down the crop, so a boundary is a row and the profile is per row. **Paper is
    not ink.** A century-old scan is brown, and counting `1 - grey/255` reads its paper as a low but
    nonzero ink everywhere, which fills the blank runs between characters and leaves no valley to cut
    in. The two levels are found in the crop instead: Otsu's threshold splits it into a dark class and
    a paper class, and a pixel counts for how far it sits between the two class means. Blank paper of
    any colour therefore counts zero, and a crop whose two classes are too close to be ink and paper
    at all — a blank page, or sensor noise — counts zero everywhere.

    A pixel's value is `(paper - grey) / (paper - ink)`, clipped to [0, 1], so it is 0 at the paper
    level, 1 at the ink level and partial for an anti-aliased edge.
    """
    grey = crop.convert("L")
    width, height = grey.size
    pixels = grey.load()
    histogram = [0] * 256
    for y in range(height):
        for x in range(width):
            histogram[pixels[x, y]] += 1
    total = width * height
    levels = _paper_and_ink(histogram, total, min_separation)
    if levels is None:
        return [0.0] * height
    paper, ink = levels
    threshold = _otsu(histogram, total)
    span = max(1.0, paper - ink)
    rows = [0.0] * height
    for y in range(height):
        value = 0.0
        for x in range(width):
            if pixels[x, y] > threshold:
                continue
            darkness = (paper - pixels[x, y]) / span
            if darkness > 0.0:
                value += min(darkness, 1.0)
        rows[y] = value
    return _smoothed(rows, smooth)


def _paper_and_ink(histogram: Sequence[int], total: int,
                   min_separation: float) -> tuple[float, float] | None:
    """The paper and ink grey levels of a crop, or `None` when it holds neither.

    Otsu's threshold is the grey that best splits the histogram in two; the mean of each side is the
    level of that class. A crop whose two levels are closer than `min_separation` greys has no ink to
    find: blank paper, a uniform wash, or the noise floor of a scanner, and all of it reads zero.
    """
    if total <= 0:
        return None
    threshold = _otsu(histogram, total)
    dark_count = dark_sum = 0
    light_count = light_sum = 0
    for level, count in enumerate(histogram):
        if not count:
            continue
        if level <= threshold:
            dark_count += count
            dark_sum += level * count
        else:
            light_count += count
            light_sum += level * count
    if dark_count == 0 or light_count == 0:
        return None
    ink = dark_sum / dark_count
    paper = light_sum / light_count
    if paper - ink < min_separation:
        return None
    return paper, ink


def _otsu(histogram: Sequence[int], total: int) -> int:
    """The grey level that best separates a histogram into two classes, by Otsu's criterion."""
    overall = sum(level * count for level, count in enumerate(histogram))
    best_level, best_variance = 0, -1.0
    weight = 0
    running = 0.0
    for level, count in enumerate(histogram):
        weight += count
        if weight == 0:
            continue
        remaining = total - weight
        if remaining == 0:
            break
        running += level * count
        below = running / weight
        above = (overall - running) / remaining
        variance = weight * remaining * (below - above) ** 2
        if variance > best_variance:
            best_variance, best_level = variance, level
    return best_level


def _smoothed(rows: Sequence[float], smooth: int) -> list[float]:
    """A profile averaged over `smooth` rows, so a one-row speck is not read as a valley."""
    height = len(rows)
    if smooth <= 1 or height <= smooth:
        return list(rows)
    half = smooth // 2
    return [sum(rows[max(0, y - half):min(height, y + half + 1)])
            / len(rows[max(0, y - half):min(height, y + half + 1)]) for y in range(height)]


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _median(values: Sequence[float]) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def _valley_runs(profile: Sequence[float], *, min_depth: float, max_ink_share: float,
                 median: float, margin: int) -> list[tuple[int, float, float]]:
    """The blank runs of a profile, one boundary each, with the evidence for the cut.

    A run is a stretch of rows the strokes do not cross: every row in it holds at most
    `max_ink_share` of the crop's median row and no more ink than the rows beside the run. Its
    boundary is the emptiest row (the middle one when several tie, because where a cut sits inside a
    blank run is not something the ink says). Reporting one boundary a run — rather than one a row —
    is what keeps a wide gap from looking like many competing hypotheses: the rows of a gap are one
    place to cut, and the alternatives a caller is shown are genuinely different places.
    """
    runs: list[list[int]] = []
    bound = min(max_ink_share, 1.0 - min_depth) * median
    for y in range(margin, len(profile) - margin):
        here = profile[y]
        left = profile[max(0, y - margin):y]
        right = profile[y + 1:min(len(profile), y + margin + 1)]
        if not left or not right or here > bound:
            continue
        if here > max(left) or here > max(right):
            continue
        if runs and y == runs[-1][-1] + 1:
            runs[-1].append(y)
        else:
            runs.append([y])
    found: list[tuple[int, float, float]] = []
    for run in runs:
        lowest = min(profile[y] for y in run)
        emptiest = [y for y in run if profile[y] == lowest]
        cut = emptiest[len(emptiest) // 2]
        left = profile[max(0, run[0] - margin):run[0]]
        right = profile[run[-1] + 1:min(len(profile), run[-1] + 1 + margin)]
        if not left or not right or median <= 0:
            continue
        depth = 1.0 - lowest / median
        prominence = (min(max(left), max(right)) - lowest) / median
        if depth >= min_depth and prominence > 0:
            found.append((cut, depth, prominence))
    return found


def _candidates(boundaries: Sequence[tuple[int, float, float]], count: int, height: int,
                limits: Limits) -> list[list[int]]:
    """Every way of choosing `count - 1` boundary rows out of the blank runs, deepest first.

    The runs are taken deepest first and at most `max_boundaries` of them are enumerated, so a crop
    with a hundred faint minima costs no more than one with eight. The child-size rule is applied here
    rather than by equal division: a set of cuts is kept when every child is at least
    `min_child_share` and at most `max_child_share` of the mean character height. Two characters of
    the same height are one shape a cut may take, not the shape it must take.
    """
    ordered = sorted(boundaries, key=lambda item: (-item[1], -item[2], item[0]))[:limits.max_boundaries]
    ordered.sort(key=lambda item: item[0])
    smallest = max(limits.min_child_pixels, round(height / count * limits.min_child_share))
    largest = max(smallest, round(height / count * limits.max_child_share))

    def acceptable(cuts: Sequence[int]) -> bool:
        edges = [0, *cuts, height]
        return all(smallest <= edges[index + 1] - edges[index] <= largest
                   for index in range(len(edges) - 1))

    found: list[list[int]] = []
    for combination in _combinations([item[0] for item in ordered], count - 1):
        if acceptable(combination):
            found.append(list(combination))
    return found[:limits.max_candidates]


def _combinations(values: Sequence[int], choose: int) -> list[tuple[int, ...]]:
    """Every increasing choice of `choose` values, without itertools' recursion on a big list."""
    if choose == 0:
        return [()]
    found: list[tuple[int, ...]] = []
    for index in range(len(values) - choose + 1):
        for rest in _combinations(values[index + 1:], choose - 1):
            found.append((values[index], *rest))
    return found


# --- the proposal --------------------------------------------------------------------------------


def _text_cost(children: Sequence[ChildScore], floor: float) -> float:
    """The negative log mass of the characters a boundary set would produce, and how sure it is.

    Every child is scored on the mass of the code points its character may be, so a child the model
    reads as something else costs a lot; the tie between two boundary sets is broken here and nowhere
    else, because the ink profile alone cannot say which side of a valley a stroke belongs to.
    """
    total = 0.0
    for child in children:
        total += -math.log(max(child.mass, floor))
    return total


def propose_split(
    crop: Image.Image,
    expected: str,
    *,
    scorer: CharacterScorer,
    ligature: LigatureGuard = default_ligature_guard,
    candidates_of: Callable[[str], Sequence[str]] = default_candidates,
    limits: Limits | None = None,
) -> SplitProposal:
    """Propose where `crop` divides into the characters of `expected`.

    The reading is the caller's: a reviewer's feedback or an OCR sequence. What this decides is the
    boundaries — by the ink first and the character model second — and it returns `accepted=False`
    with a reason whenever the ink does not show them, the cut would cross a stroke, a child would be
    empty or clipped, the model does not read the children as the expected characters, or two
    boundaries explain the crop about equally well.
    """
    limits = limits or Limits()
    reading = graphemes(expected)
    check = ligature(expected)
    evidence: dict[str, Any] = {
        "expected": expected,
        "graphemes": reading,
        "ligature_guard": "answered" if check.available else "unavailable",
    }
    if not MIN_CHARACTERS <= len(reading) <= MAX_CHARACTERS:
        return SplitProposal(accepted=False, text=reading, evidence=evidence,
                             reason=f"the reading is {len(reading)} characters; a joined crop is "
                                    f"{MIN_CHARACTERS} to {MAX_CHARACTERS}")
    if not check.available:
        return SplitProposal(accepted=False, text=reading, evidence=evidence,
                             reason="the ligature overlay is not available, so a single encoded "
                                    "ligature cannot be ruled out; no split is proposed")
    if check.code_point is not None:
        return SplitProposal(accepted=False, text=reading, evidence=evidence,
                             reason=f"{expected} is {check.code_point} in the character layer: one "
                                    f"encoded ligature, not a join")
    width, height = crop.size
    if width <= 0 or height <= 0:
        return SplitProposal(accepted=False, text=reading, evidence=evidence,
                             reason="the crop has no pixels")
    raw = ink_profile(crop, smooth=1)
    profile = _smoothed(raw, limits.smooth)
    median = _median(profile)
    # What "a row with ink" is worth, for the thresholds that are shares of it. The median answers
    # for a crop whose ink covers most of its rows; a crop that is mostly blank paper — a short
    # character in a tall box — has a median of zero, and then the mean of the rows that do hold ink
    # is the honest reference rather than a division by nothing.
    reference = median if median > 0 else _mean([value for value in profile if value > 0])
    total_ink = sum(raw)
    evidence.update({"crop": [width, height], "median_row_ink": round(median, 2),
                     "row_ink_reference": round(reference, 2),
                     "total_ink": round(total_ink, 1), "limits": limits.model_dump()})
    if total_ink <= 0 or reference <= 0:
        return SplitProposal(accepted=False, text=reading, evidence=evidence,
                             reason="the crop holds no ink")
    margin = max(2, round(height / len(reading) * 0.2))
    boundaries = _valley_runs(profile, min_depth=limits.min_valley_depth,
                              max_ink_share=limits.max_cut_ink_share, median=reference, margin=margin)
    evidence["boundaries"] = [{"y": y, "depth": round(depth, 3), "prominence": round(prominence, 3)}
                              for y, depth, prominence in boundaries[:limits.max_boundaries]]
    if not boundaries:
        return SplitProposal(accepted=False, text=reading, evidence=evidence,
                             reason="no row of the crop holds less ink than the rows around it, so "
                                    "the ink shows no boundary; an equal division is not proposed")
    by_row = {y: (depth, prominence) for y, depth, prominence in boundaries}
    cuts = _candidates(boundaries, len(reading), height, limits)
    evidence["candidates"] = len(cuts)
    if not cuts:
        return SplitProposal(accepted=False, text=reading, evidence=evidence,
                             reason="every low-ink row would leave a child too small or too large to "
                                    "be a character")
    scored: list[tuple[float, list[int], list[ChildScore], list[CutScore]]] = []
    for combination in cuts:
        edges = [0, *combination, height]
        boxes = [Box(x=0, y=edges[index], w=width, h=edges[index + 1] - edges[index])
                 for index in range(len(edges) - 1)]
        children = [crop.crop((box.x, box.y, box.x + box.w, box.y + box.h)) for box in boxes]
        masses = scorer(children)
        if len(masses) != len(children):
            raise ValueError(f"the scorer answered {len(masses)} crops for {len(children)} children")
        scores = []
        for character, mass, box, top_edge in zip(reading, masses, boxes, edges[1:], strict=True):
            code_points = list(candidates_of(character))
            value = sum(float(mass.get(point, 0.0)) for point in code_points)
            top = max(mass, key=lambda point: mass[point]) if mass else None
            scores.append(ChildScore(
                text=character, box=box, mass=value, top=top,
                canonical=bool(top) and top in code_points,
                ink=round(sum(raw[box.y:top_edge])),
            ))
        cut_scores = [CutScore(y=y, ink_share=round(profile[y] / reference, 3) if reference else 0.0,
                               depth=round(depth, 3), prominence=round(prominence, 3))
                      for y, (depth, prominence) in by_row.items() if y in set(combination)]
        scored.append((_text_cost(scores, 1e-4), combination, scores, cut_scores))
    scored.sort(key=lambda item: item[0])
    best_cost, best_cuts, best_children, best_cut_scores = scored[0]
    runner = scored[1] if len(scored) > 1 else None
    text_margin = runner[0] - best_cost if runner else None
    evidence["floor"] = 1e-4
    proposal = SplitProposal(
        accepted=False, text=reading, boxes=[child.box for child in best_children],
        cuts=list(best_cuts), cut_scores=best_cut_scores, children=best_children,
        alternatives=[Alternative(cuts=list(item[1]), cost=round(item[0], 4),
                                  margin=round(item[0] - best_cost, 4))
                      for item in scored[1:4]],
        margin=None if text_margin is None else round(text_margin, 4),
        evidence={**evidence, "best_cost": round(best_cost, 4)},
    )
    clipped = _clipped(raw, max(raw), limits.max_edge_ink_share)
    if clipped is not None:
        proposal.reason = f"the ink reaches {clipped} of the crop, so a child is cut off by the box"
        return proposal
    refusal = child_refusal(best_children, min_mass=limits.min_character_mass)
    if refusal is not None:
        proposal.reason = refusal
        return proposal
    if text_margin is not None and text_margin < limits.min_margin:
        proposal.reason = (f"two boundaries explain the ink about equally well "
                           f"(margin {text_margin:.3f} nats under {limits.min_margin:g})")
        proposal.evidence["ambiguous_with"] = proposal.alternatives[0].cuts if proposal.alternatives \
            else None
        return proposal
    proposal.accepted = True
    proposal.reason = (f"{len(best_children)} characters on {-best_cost:.2f} nats of model mass and "
                       f"{len(best_cuts)} low-ink cut"
                       f"{'s' if len(best_cuts) != 1 else ''}")
    return proposal


def child_refusal(children: Sequence[ChildScore], *, min_mass: float) -> str | None:
    """Why these children are not a split, or `None` when they are.

    Three rules, all of them about a child on its own.

    - A child with no ink in it is a blank rectangle, not a character.
    - A child the model's **most likely** answer for is not the character expected there is a cut in
      the wrong place. A mass alone does not say this: a crop can give the expected character 0.9 and
      something else 0.95, and the split then rests on the character the model did *not* choose.
    - A child whose expected character does not hold enough mass is not read clearly enough to be
      worth cutting for. A caller with a calibrated model sets this high — at 0.85, a child that the
      model gives a quarter of its mass to is refused — and a caller with a weak model sets it low and
      gets the geometry and the top-1 rule alone.

    They are here rather than inline so a caller can see exactly what a split is allowed to produce,
    and so each can be argued with on its own.
    """
    if any(child.ink == 0 for child in children):
        return "one of the proposed characters would hold no ink at all"
    off = [child for child in children if not child.canonical]
    if off:
        return (f"the model reads {off[0].top or 'nothing'} where {off[0].text!r} was expected, so the "
                f"box holds a different character")
    weak = [child for child in children if child.mass < min_mass]
    if weak:
        return (f"the model reads {weak[0].text!r} at {weak[0].mass:.3f} mass in the proposed box, "
                f"under the {min_mass:g} a split needs")
    return None


def _clipped(raw: Sequence[float], widest: float, share: float) -> str | None:
    """Whether a stroke runs out of the crop's first or last row, which clips a child.

    A joined crop is the ink of whole characters, so an edge row as inked as the crop's widest row —
    and no thinner than the row inside it — is a box cut through a character: the child at that end is
    part of one, not one. A tight box grazing the tip of a stroke is thinner than the widest row and
    is left alone.
    """
    if len(raw) < 2 or widest <= 0:
        return None
    if raw[0] >= share * widest and raw[0] >= raw[1]:
        return "the first row"
    if raw[-1] >= share * widest and raw[-1] >= raw[-2]:
        return "the last row"
    return None
