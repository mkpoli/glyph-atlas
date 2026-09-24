"""Diagnose and repair the systematic misassignment of a line's characters to its detections.

The defect this module exists for is measured, not suspected. The aligner matches the tokens of a
transcription to a line's detections with a monotone dynamic program, so the two sequences are walked
forward together and the order the detections arrive in decides which character gets which box. It
used to order them by `(-x, y)`, the raw float x centre; on the Ainu manuscripts most of a line's
detections sit within a few pixels of the same x, so the y key almost never decided anything and the
detector's own output is in no order at all. Over the 759 derived lines that hold four or more
in-box detections, the detections advanced down the column for **5** of them. A reviewer sees the
result as a crop that shows the neighbouring character: `hk:…:33:L10` transcribes
慎しめる体なり偖左の手にて碗をとり右手に and the unit whose text is 手 held the crop of を.
`align` walks a vertical line in `align.reading_order` now; this pass is what puts units that were
aligned before that back on the ink they name.

Two layers, and only the second one guesses.

**The deterministic layer** is geometry and the transcription's own structure. A vertical line is read
down its column, so a line's detections are ordered by `ainu.columns_of`'s columns, right to left,
and by y inside each column: measured over the 759 lines, that takes the number whose detections
advance down the line from 5 to 724, and it is a statement about the page and not about a model. A
whitespace token is not ink and must not hold a box, which the corpus shows it did: the two leading
ideographic spaces of these lines each held a detection.

**The proposals** are the ones a model decides: which detection the first transcribed character sits
on, and which characters have no ink on the page at all. `phase_costs` scores the whole line at every
rigid offset, so the question "is this line one detection out?" is asked of the sequence and not of a
crop, and the margin that decides it is measured per *matched pair* as well as in total, so a phase
cannot win by matching fewer characters. Everything a model decides is written as a machine proposal
with its margins; nothing here sets a unit's review state to a human one.

Why not simply run the aligner again in the corrected order: because its cost model prefers skipping to
matching whenever the classifier is unsure, and on this hand it is unsure often. A skip costs 6 nats
and a match costs up to 9.2 (the floor), so the cheapest path abandons pairs. Measured on the boxed
lines of six pages, the rigid phase places the characters so that the classifier reads the crop as the
unit's own code point for 37.0 percent of them, the aligner's own path on the same crops for 26.3 —
and on the two units a person has corrected by hand, `hk:…:33:L10` seq 13 and `hk:…:33:L9` seq 20, the
rigid phase puts 手 back on 手 while the aligner's path does not.

Nothing here writes to the dataset it reads. `plan` produces records; `apply` writes them to a
*derived* directory; `undo` reverses them from the log. A unit a person reviewed is never moved — its
box is the ink their correction was read against — and a line whose row a person changed is carried
into the derived dataset as the store has it. Every record names the old and the new box, the
hypothesis, the margins, the neighbouring characters and the checksums of the source, the run and the
models, so a correction can be replayed or argued with without trusting this module's summary of itself.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import sqlite3
from collections import Counter
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, Field

from . import ainu, align, images, tables
from .align import Detection, Run, Token, load_run
from .schema import Box, Confidence, Line, ReviewState, Unit, UnitKind

#: The policy name the records carry, so a later run can find what it wrote.
POLICY = "alignment-repair-v1"
#: The file a derived dataset's repair log is written to.
LOG_NAME = "repairs.jsonl"
#: The file a whole plan is written to, so `apply` can be reviewed before it runs.
PLAN_NAME = "repair-plan.json"
#: The report the pass writes beside its output.
REPORT_NAME = "repair-report.json"
#: The table a derived dataset's human-readable correction list is written to.
TABLE_NAME = "repairs.tsv"
#: The reading log the review store writes its events to.
REVIEWS_NAME = "reviews.jsonl"
#: The store a review server keeps its own copy of the tables in.
STORE_NAME = "review.sqlite"
#: How many detections before the first transcribed character a phase may propose. The corpus shows
#: one or two; a larger offset is a different fault — a wrong line box, or a column paired with the
#: wrong transcription — and this pass is not allowed to guess at it.
MAX_PHASE = 2
#: The margin, in nats, by which a shifted phase has to beat the unshifted one before the pass will
#: propose the shift. One nat is a factor of e in likelihood.
MIN_PHASE_MARGIN = 2.0
#: The margin in *mean cost a matched pair* the shifted phase also has to win by. A phase that matches
#: fewer characters can look cheaper in total by paying skip penalties instead of match costs, so the
#: total alone is not evidence; this is the number that is not for sale.
MIN_PER_PAIR_MARGIN = 0.0
#: The share of the characters both phases place on which the shifted phase has to be the better one.
#: A mean can also be gamed, by dropping the worst pair; a count of per-character wins cannot, because
#: it is taken over the characters the two phases agree to place.
MIN_PHASE_SHARE = 0.6
#: The margin by which a unit's own crop has to beat the next best crop of its line before the record
#: is called *confirmed* rather than a geometric placement. The classifier is weak on this hand, so
#: this labels a record; it does not decide whether the deterministic layer applies.
MIN_UNIT_MARGIN = 1.0
#: How much worse the chosen crop may be than the best other crop of the line before the pass refuses
#: to move that unit. Below this the ink where the character should be looks like a different
#: character of the line, which is what a fault this pass does not understand looks like.
CONTRADICTION_MARGIN = -1.0
#: The states of a unit the classifier was not trained to score one crop at a time, which raise the
#: bar for calling a record confirmed: a ligature or an iteration mark is not one character a crop.
AMBIGUOUS_KINDS = {UnitKind.LIGATURE, UnitKind.ITERATION_MARK}
#: The states a person, not the pipeline, wrote. A unit carrying one is never moved by this pass.
HUMAN_REVIEW = (
    ReviewState.REVIEWED,
    ReviewState.DOUBLE_REVIEWED,
    ReviewState.ADJUDICATED,
    ReviewState.DISPUTED,
    ReviewState.TRANSCRIBER,
)
#: What a unit's `meta` carries after this pass has looked at it.
META_KEY = "alignment_repair"
#: The tables a derived dataset copies as they stand.
COPIED_TABLES = ("documents", "pages", "page_texts", "lines", "groups")


class RepairError(RuntimeError):
    """A repair that cannot be planned or applied as asked."""


# --- the deterministic layer: order and ink --------------------------------------------------------


def order_evidence(detections: Sequence[Detection]) -> dict[str, Any]:
    """How far a line's detections are from reading order, for the record.

    Three numbers, all computable without a model. `inversions` counts the neighbouring pairs that
    step back up the page, so a line in reading order has none; `columns` is how many columns the
    derivation's own grouping found, because a line that groups into two columns is a different
    question from a line that groups into one; `span` is the height the detections cover, which is
    what says whether a y sequence is a column or a scatter.
    """
    if not detections:
        return {"detections": 0, "inversions": 0, "columns": 0, "span": 0}
    y = [detection.centre[1] for detection in detections]
    return {
        "detections": len(detections),
        "inversions": sum(1 for first, second in pairwise(y) if second <= first),
        "columns": len(ainu.columns_of([detection.box for detection in detections])),
        "span": int(max(y) - min(y)),
    }


def is_ink(token: Token) -> bool:
    """Whether a token is a written character rather than the space a transcription indents with.

    A space is not ink: nothing on the page stands for it, so it may not hold a box. This is the
    deterministic half of the repair and the corpus shows why it matters — the two leading ideographic
    spaces of these lines each held a detection in the run that is being repaired.
    """
    return bool(token.text.strip())


# --- the proposal layer: phases, scored as whole sequences ----------------------------------------


@dataclass
class Phase:
    """One rigid offset of the written characters against the detections of a line.

    `first` is how many detections stand before the first transcribed character: 0 says the line's
    first character is the column's first ink, +1 says the column opens with a character the
    transcription does not have, -1 says the transcription opens with one the column does not. The
    assignment is rigid — written character *k* takes detection *k + first* — which is the point: a
    local skip is what the aligner's cost model reaches for when it is unsure, and a whole-sequence
    hypothesis is what makes the "no shift or an adjacent shift" question answerable.
    """

    first: int
    cost: float
    matched: int
    unplaced: int
    unused: int
    per_pair: float | None
    assignment: dict[int, int] = field(default_factory=dict)
    #: Per-character wins against the phase this one is compared with, and the characters both place.
    wins: int = 0
    common: int = 0

    @property
    def win_share(self) -> float | None:
        return self.wins / self.common if self.common else None

    @property
    def coverage(self) -> float:
        total = self.matched + self.unplaced
        return self.matched / total if total else 0.0


def phase_costs(costs: Sequence[Sequence[float]], ink: Sequence[int], detections: int,
                weights: dict[str, float], *, limit: int = MAX_PHASE) -> list[Phase]:
    """The cost of every rigid phase of a line, cheapest first.

    A phase explains the whole line at once: every written character is either matched to the
    detection at its own offset or paid for as a character with no ink, and every detection is either
    used or paid for as ink with no character. The costs are the aligner's own match costs and its own
    skip weights, so a phase's total is comparable with the aligner's, and `per_pair` is the mean cost
    over the pairs the phase does match — the number a phase cannot improve by matching less.
    """
    if not ink or detections <= 0:
        return []
    phases: list[Phase] = []
    for first in range(-limit, limit + 1):
        total = 0.0
        matched = 0
        assignment: dict[int, int] = {}
        used: set[int] = set()
        for position, token_index in enumerate(ink):
            column = position + first
            if 0 <= column < detections:
                total += costs[token_index][column]
                assignment[token_index] = column
                used.add(column)
                matched += 1
        unplaced = len(ink) - matched
        unused = detections - len(used)
        total += unplaced * weights["skip-token"] + unused * weights["skip-detection"]
        phases.append(Phase(first=first, cost=total, matched=matched, unplaced=unplaced,
                            unused=unused,
                            per_pair=(sum(costs[i][j] for i, j in assignment.items()) / matched
                                      if matched else None),
                            assignment=assignment))
    return sorted(phases, key=lambda phase: phase.cost)


def choose_phase(phases: Sequence[Phase], costs: Sequence[Sequence[float]], ink: Sequence[int], *,
                 base: int = 0, min_total: float = MIN_PHASE_MARGIN,
                 min_per_pair: float = MIN_PER_PAIR_MARGIN,
                 min_share: float = MIN_PHASE_SHARE) -> tuple[Phase, Phase, str]:
    """The phase to place the line by, the unshifted phase it is compared against, and why.

    A shift is proposed only when it beats the unshifted phase in three currencies at once: by
    `min_total` nats of total cost, by `min_per_pair` nats on the mean matched pair, and on at least
    `min_share` of the characters both phases place, taken one character at a time. The first two are
    what the aligner's own numbers say and the third is the one that cannot be bought: a phase that
    pays skip penalties instead of match costs can look cheaper, a phase that drops its worst pair can
    look better per pair, but a phase cannot win a majority of the individual characters by covering
    fewer of them.
    """
    if not phases:
        raise RepairError("no phase to choose")
    unshifted = next((phase for phase in phases if phase.first == base), phases[0])
    best = phases[0]
    if best.first == base:
        return unshifted, unshifted, "the unshifted phase is the cheapest"
    common = [token for token in ink
              if token in best.assignment and token in unshifted.assignment]
    best.wins = sum(1 for token in common
                    if costs[token][best.assignment[token]] < costs[token][unshifted.assignment[token]])
    best.common = len(common)
    unshifted.wins = best.common - best.wins
    unshifted.common = best.common
    total_margin = unshifted.cost - best.cost
    pair_margin = ((unshifted.per_pair - best.per_pair)
                   if (unshifted.per_pair is not None and best.per_pair is not None) else None)
    if total_margin < min_total:
        return unshifted, unshifted, f"the shift wins by only {total_margin:.2f} nats in total"
    if pair_margin is None or pair_margin < min_per_pair:
        return unshifted, unshifted, ("the shift wins in total by covering fewer characters "
                                      f"({best.matched} against {unshifted.matched})")
    share = best.win_share
    if share is None or share < min_share:
        return unshifted, unshifted, (f"the shift wins on only {best.wins} of the {best.common} "
                                      f"characters both phases place")
    return best, unshifted, (f"the shift wins by {total_margin:.2f} nats in total, "
                             f"{pair_margin:.2f} nats a matched pair, and on {best.wins} of the "
                             f"{best.common} characters both phases place")


@dataclass
class Gap:
    """Where a rigid assignment leaves ink and characters unpaired."""

    head_detections: int = 0
    tail_detections: int = 0
    interior_detections: int = 0
    unplaced_characters: int = 0

    @property
    def clean(self) -> bool:
        """Whether the assignment is a 1:1 run of the line, allowing the ends to differ.

        An interior detection with no character is the detector having found something between two
        characters — a mark, a smear, or a character the transcription does not carry — and the
        characters after it keep their own ink under a rigid phase only if the surplus is at an end.
        """
        return self.interior_detections == 0

    def describe(self) -> str:
        parts = []
        for name, value in (("head-det", self.head_detections), ("tail-det", self.tail_detections),
                            ("interior-det", self.interior_detections),
                            ("unplaced-char", self.unplaced_characters)):
            if value:
                parts.append(f"{name}:{value}")
        return " ".join(parts) or "clean"


def gap_of(assignment: dict[int, int], ink: Sequence[int], detections: int) -> Gap:
    """The unpaired ink and characters of a rigid assignment."""
    gap = Gap(unplaced_characters=len(ink) - len(assignment))
    used = sorted(assignment.values())
    if not used:
        gap.interior_detections = detections
        return gap
    first, last = used[0], used[-1]
    for index in range(detections):
        if index in used:
            continue
        if index < first:
            gap.head_detections += 1
        elif index > last:
            gap.tail_detections += 1
        else:
            gap.interior_detections += 1
    return gap


@dataclass
class UnitView:
    """One token of a line with the detection the corrected order gives it and the evidence for it."""

    index: int
    token: Token
    unit: Unit | None
    ink: bool
    detection: Detection | None
    detection_index: int | None
    old_box: Box | None = None
    cost_new: float | None = None
    cost_old: float | None = None
    alternative: float | None = None
    margin: float | None = None


@dataclass
class LineView:
    """One line's placement under the corrected reading order, with the evidence around it."""

    line: Line
    tokens: list[Token]
    detections: list[Detection]
    units: list[UnitView]
    phases: list[Phase]
    chosen: Phase
    unshifted: Phase
    reason: str
    gap: Gap
    before: dict[str, Any]
    after: dict[str, Any]
    container: int = 0

    @property
    def shift(self) -> int:
        return self.chosen.first - self.unshifted.first

    @property
    def total_margin(self) -> float | None:
        if self.chosen is self.unshifted:
            return None
        return self.unshifted.cost - self.chosen.cost

    @property
    def pair_margin(self) -> float | None:
        if self.chosen is self.unshifted:
            return None
        if self.chosen.per_pair is None or self.unshifted.per_pair is None:
            return None
        return self.unshifted.per_pair - self.chosen.per_pair


def view_line(line: Line, detections: Sequence[Detection], *, run: Run,
              classifier: Any = None, crop_of: Any = None, units: Sequence[Unit] = (),
              scores: ScoreCache | None = None, limit: int = MAX_PHASE,
              min_phase_margin: float = MIN_PHASE_MARGIN) -> list[LineView]:
    """Score one line under the corrected reading order and return one view a container.

    The detections are ordered by `align.reading_order`; the placement is the cheapest rigid phase of
    the written characters against them. Nothing here re-runs the aligner: its dynamic program prefers
    skipping to matching when the classifier is unsure, which on this hand is most of the time, and a
    repair inherited from it would carry that bias into every box it moved.
    """
    views: list[LineView] = []
    inside = [detection for detection in detections
              if line.box is None or _inside(line.box, detection.centre)]
    if not inside:
        return views
    containers = align.containers_of(line, detections, run.policy)
    by_token = _units_by_token(units)
    offset = 0
    for number, container in enumerate(containers):
        start, offset = offset, offset + len(container.tokens)
        if not container.tokens or not container.detections:
            continue
        costs = _costs(container.tokens, list(container.detections), classifier=classifier,
                       crop_of=crop_of, line=line, scores=scores)
        ink = [index for index, token in enumerate(container.tokens) if is_ink(token)]
        phases = phase_costs(costs, ink, len(container.detections), run.weights, limit=limit)
        if not phases:
            continue
        chosen, unshifted, reason = choose_phase(phases, costs, ink, min_total=min_phase_margin)
        views.append(
            LineView(
                line=line,
                tokens=list(container.tokens),
                detections=list(container.detections),
                units=_unit_views(container, costs, chosen, by_token, start),
                phases=phases,
                chosen=chosen,
                unshifted=unshifted,
                reason=reason,
                gap=gap_of(chosen.assignment, ink, len(container.detections)),
                before=order_evidence(inside),
                after=order_evidence(list(container.detections)),
                container=number,
            )
        )
    return views


def _costs(tokens: Sequence[Token], detections: Sequence[Detection], *, classifier: Any,
           crop_of: Any, line: Line, scores: ScoreCache | None) -> list[list[float]]:
    """The aligner's own cost matrix, from the cache when it is there and the model when it is not."""
    if classifier is None or crop_of is None or not detections:
        floor = -math.log(align.PROBABILITY_FLOOR)
        return [[floor] * len(detections) for _ in tokens]
    if scores is not None:
        probabilities = scores.probabilities(line.page_id, [detection.box for detection in detections],
                                             classifier=classifier, crop_of=crop_of)
    else:
        crops = [crop_of(line.page_id, detection.box) for detection in detections]
        probabilities = classifier.probabilities_many(crops)
    index = {name: position for position, name in enumerate(classifier.classes)}
    floor = -math.log(align.PROBABILITY_FLOOR)
    return [align.token_costs(probabilities, token, index, floor) for token in tokens]


def _units_by_token(units: Sequence[Unit]) -> dict[int, Unit]:
    """The units of a line by the token each was placed for.

    A unit's `seq` is its placement's position in the line, and every token gets exactly one
    placement — a token with no ink gets a boxless one — so the aligned units in `seq` order are the
    tokens in reading order, one for one. The 振り仮名 units are not placements: they carry an `r`
    where a placement carries its number and are left out here.
    """
    return {index: unit
            for index, unit in enumerate(sorted((unit for unit in units if not _is_ruby(unit)),
                                                key=lambda unit: unit.seq or 0))}


def _unit_views(container: Any, costs: Sequence[Sequence[float]], chosen: Phase,
                by_token: dict[int, Unit], offset: int) -> list[UnitView]:
    """Every token of a container with the detection the chosen phase gives it and its margin."""
    views: list[UnitView] = []
    for index, token in enumerate(container.tokens):
        position = chosen.assignment.get(index)
        detection = container.detections[position] if position is not None else None
        row = costs[index] if index < len(costs) else []
        picked = row[position] if position is not None and position < len(row) else None
        others = [value for at, value in enumerate(row) if at != position]
        alternative = min(others) if others else None
        unit = by_token.get(offset + index)
        old_box = unit.box if unit is not None else None
        old_cost = None
        if old_box is not None:
            old_position = next((at for at, candidate in enumerate(container.detections)
                                 if _same_box(candidate.box, old_box)), None)
            if old_position is not None and old_position < len(row):
                old_cost = row[old_position]
        views.append(
            UnitView(index=index, token=token, unit=unit, ink=is_ink(token), detection=detection,
                     detection_index=position, old_box=old_box, cost_new=picked, cost_old=old_cost,
                     alternative=alternative,
                     margin=(alternative - picked) if (picked is not None and alternative is not None)
                     else None)
        )
    return views


def _is_ruby(unit: Unit) -> bool:
    return bool(re.search(r":r\d+$", unit.id))


def _box_key(box: Box | None) -> tuple[int, int, int, int] | None:
    return None if box is None else (box.x, box.y, box.w, box.h)


def _same_box(left: Box | None, right: Box | None) -> bool:
    return _box_key(left) == _box_key(right)


def _inside(box: Box, point: tuple[float, float]) -> bool:
    return box.x <= point[0] <= box.x + box.w and box.y <= point[1] <= box.y + box.h


# --- proposals -----------------------------------------------------------------------------------


class Neighbour(BaseModel):
    """A character next to a unit in the line, so a record shows what the correction sits between."""

    seq: int | None = None
    text: str | None = None
    reading: str | None = None
    unicode: str | None = None
    box: Box | None = None


class RepairRecord(BaseModel):
    """One unit's diagnosis, and the correction this pass proposes or withholds.

    The record is the unit of review and the unit of reversal: it names the unit, the two boxes, the
    hypothesis, the margins that decided it, the characters on either side and the checksums of what
    it was computed from. `reading` and `unicode` are kept apart on purpose — the reading is what the
    transcription says the character reads, the code point is which character it is — and this pass
    changes neither: a box correction is a claim about ink, not about text. `machine` and `verified`
    are explicit so a viewer never has to infer from a status string whether a person has seen this,
    and `layer` says whether a rule or a model decided it.
    """

    id: str
    unit_id: str
    line_id: str
    page_id: str | None = None
    document_id: str | None = None
    seq: int | None = None
    text_source: str | None = None
    reading: str | None = None
    unicode: str | None = None
    kind: UnitKind = UnitKind.CHAR
    granularity: str = "char"
    ink: bool = True
    old_box: Box | None = None
    new_box: Box | None = None
    #: Everything the old box decided, kept so `undo` restores the row and not just the rectangle.
    old_crop: str | None = None
    old_crop_sha256: str | None = None
    old_confidence: Confidence | None = None
    old_review: ReviewState = ReviewState.MACHINE
    status: Literal["applied", "confirmed", "uncertain", "unchanged", "human", "undone"] = "unchanged"
    layer: Literal["deterministic", "proposal", "human"] = "deterministic"
    reason: str = ""
    hypothesis: Literal["reading-order", "phase-shift", "keep"] = "reading-order"
    phase: int = 0
    phase_margin: float | None = None
    phase_pair_margin: float | None = None
    unit_margin: float | None = None
    cost_old: float | None = None
    cost_new: float | None = None
    before: Neighbour | None = None
    after: Neighbour | None = None
    machine: bool = True
    verified: bool = False
    evidence: dict[str, Any] = Field(default_factory=dict)

    @property
    def reliable(self) -> bool:
        """Whether the placement is one this pass stands behind.

        Derived from the status rather than stored beside it, so the two cannot disagree: only a
        machine record that was applied or confirmed is one this pass will call usable, and a withheld
        unit keeps the box it had and is not a reliable exemplar of its character. A viewer that shows
        crops as examples, or that decides which crops to quiz, reads this rather than parsing the
        status string; `meta["alignment_repair"]` carries the same answer into the tables.
        """
        return self.machine and self.status in ("applied", "confirmed")

    @property
    def changed(self) -> bool:
        return not _same_box(self.old_box, self.new_box)


class LineDecision(BaseModel):
    """One line's diagnosis: what the corrected order says and what the pass did about it."""

    line_id: str
    page_id: str | None = None
    document_id: str | None = None
    tokens: int = 0
    detections: int = 0
    ink: int = 0
    status: Literal["unchanged", "reorder", "shift", "uncertain", "human", "empty"] = "unchanged"
    reason: str = ""
    phase: int = 0
    shift: int = 0
    phase_margin: float | None = None
    phase_pair_margin: float | None = None
    coverage: float | None = None
    gap: str = ""
    inversions_before: int = 0
    inversions_after: int = 0
    columns_before: int = 0
    columns_after: int = 0
    moved: int = 0
    confirmed: int = 0
    withheld: int = 0
    human_units: int = 0
    conflicts: int = 0
    reverted: int = 0
    collisions: int = 0


class RepairPlan(BaseModel):
    """A whole pass: the lines it decided, the records it proposes, and what it ran on."""

    policy: str = POLICY
    created: str = ""
    source: dict[str, Any] = Field(default_factory=dict)
    run: dict[str, Any] = Field(default_factory=dict)
    detector: dict[str, Any] = Field(default_factory=dict)
    classifier: dict[str, Any] = Field(default_factory=dict)
    thresholds: dict[str, Any] = Field(default_factory=dict)
    human: dict[str, Any] = Field(default_factory=dict)
    counts: dict[str, Any] = Field(default_factory=dict)
    lines: list[LineDecision] = Field(default_factory=list)
    records: list[RepairRecord] = Field(default_factory=list)


def final_box(record: RepairRecord) -> Box | None:
    """The box a record leaves its unit holding, whatever the pass decided about it."""
    if record.status in ("applied", "confirmed"):
        return record.new_box
    return record.old_box


def resolve_collisions(records: Sequence[RepairRecord]) -> dict[str, Any]:
    """Put back every move that would leave two active units holding the same box.

    The per-unit gate decides each character on its own evidence, and that is not enough to keep a
    line a sequence. A moved character can land on the box a withheld one is keeping — the withheld
    unit is the one whose classifier margin did not settle it, which makes it exactly the kind of
    character a move would collide with — and a viewer that cuts one rectangle for two labels is
    worse than either decision alone. Every move that would duplicate a box is reverted here; the
    record keeps the box it wanted and says why it did not get it, so the correction survives as a
    proposal instead of being silently dropped. A collision between two records that were not moved
    is left alone and counted: it is the state the source was already in, and this pass is not
    allowed to make it worse, only to not add to it.
    """
    reverted = 0
    baseline: list[str] = []
    while True:
        owners: dict[tuple[int, int, int, int] | None, list[RepairRecord]] = {}
        for record in records:
            box = final_box(record)
            if box is None or record.unit_id == "":
                continue
            owners.setdefault(_box_key(box), []).append(record)
        clashing = [(key, group) for key, group in owners.items() if len(group) > 1]
        if not clashing:
            break
        progress = False
        for key, group in clashing:
            moved = [record for record in group
                     if record.status in ("applied", "confirmed")]
            for record in moved:
                record.status = "uncertain"
                record.reason += ("; put back: the move would give two characters the same box "
                                  f"{_box_text(record.new_box)}")
                reverted += 1
                progress = True
            if not moved and key is not None:
                baseline.append(_box_text(Box(x=key[0], y=key[1], w=key[2], h=key[3])))
        if not progress:
            break
    return {"reverted": reverted, "baseline-duplicates": len(baseline)}


def collisions(units: Iterable[Unit]) -> dict[str, list[str]]:
    """Every box that more than one active unit of a line holds.

    The invariant a repair has to keep: one crop, one character. It is checked on the finished
    dataset rather than trusted from the plan, because the plan is a proposal and the dataset is what
    a viewer reads.
    """
    seen: dict[tuple[str, tuple[int, int, int, int]], list[str]] = {}
    for unit in units:
        if not unit.active or unit.box is None or unit.line_id is None:
            continue
        key = (unit.line_id, _box_key(unit.box))  # type: ignore[arg-type]
        seen.setdefault(key, []).append(unit.id)
    return {f"{line}:{_box_text(Box(x=box[0], y=box[1], w=box[2], h=box[3]))}": ids
            for (line, box), ids in seen.items() if len(ids) > 1}


def decide(views: Sequence[LineView], *, line: Line | None = None,
           confirm_margin: float = MIN_UNIT_MARGIN,
           contradiction_margin: float = CONTRADICTION_MARGIN,
           place_missing: bool = False,
           pinned: Collection[str] = ()) -> tuple[LineDecision, list[RepairRecord]]:
    """Turn one line's views into a decision and one record a unit.

    The deterministic layer moves a character onto the ink the corrected reading order gives it: the
    order is a statement about the page, and a monotone alignment fed an order that is not the reading
    order cannot be pairing the characters. A whitespace token is not ink and is never given a box.

    The proposal layer is what a model decided, and it is labelled as one: a phase shift, and every
    character the phase leaves without ink. A unit is withheld when the classifier actively contradicts
    the placement, when it is not ink and holds no box, when it has no box and this pass was not asked
    to place new ink, or when a person reviewed it. Withheld is not a failure: it is the pass declining
    to make a decision it cannot support, so that a reviewer is asked once about a real question
    instead of about every crop.

    A line is placed as one sequence or it is not placed. The moves of a line are only correct
    together: under one phase, character k takes detection k, so moving two characters while a third
    keeps its old box leaves the moved ones beside the wrong neighbours. A reviewer sees exactly that
    as a crop showing the neighbouring character, measured on the quiz where 26 of 47 wrong-character
    answers named the previous or next unit. So one unsettled character of a line takes the line's
    moves back to proposals. Freeing a box from a whitespace token survives that rule: a space is not
    ink whatever the sequence does.
    """
    rows = [view for view in views if view.units]
    if not rows:
        return LineDecision(line_id=line.id if line else "", status="empty",
                            reason="no detection inside the line box"), []
    merged = _merge(rows)
    line = line or merged.line
    decision = LineDecision(
        line_id=line.id,
        page_id=line.page_id,
        tokens=len(merged.tokens),
        detections=len(merged.detections),
        ink=sum(1 for token in merged.tokens if is_ink(token)),
        phase=merged.chosen.first,
        shift=merged.shift,
        phase_margin=merged.total_margin,
        phase_pair_margin=merged.pair_margin,
        coverage=merged.chosen.coverage,
        gap=merged.gap.describe(),
        inversions_before=merged.before.get("inversions", 0),
        inversions_after=merged.after.get("inversions", 0),
        columns_before=merged.before.get("columns", 0),
        columns_after=merged.after.get("columns", 0),
        reason=merged.reason,
    )
    # Which boxes the corrected order gives to a character, so a character the phase leaves without
    # ink knows whether the box it holds is one another character now owns.
    # A pinned unit keeps its own box, so the ink the order would give it is not taken by anyone.
    taken = {_box_key(view.detection.box) for view in merged.units
             if view.detection is not None and not _human(view, pinned)}
    records: list[RepairRecord] = []
    unpaired: list[RepairRecord] = []
    for view in merged.units:
        record = _record(merged, view, line)
        old_box = view.old_box
        new_box = record.new_box
        if _human(view, pinned):
            record.status = "human"
            record.layer = "human"
            record.hypothesis = "keep"
            record.machine = False
            record.verified = True
            record.reason = ("a person reviewed this unit; its box is the ink their answer was read "
                             "against")
            if record.changed:
                record.reason += " and the corrected order disagrees with it"
                decision.conflicts += 1
            decision.human_units += 1
            records.append(record)
            continue
        if view.unit is not None and not _human(view, pinned) and (
                view.unit.text_source or "") != (view.token.text or ""):
            record.status = "uncertain"
            record.reason = (f"the unit reads {view.unit.text_source!r} where the token is "
                             f"{view.token.text!r}: the line's units do not pair one for one with its "
                             "tokens, so no box of it can be placed")
            records.append(record)
            unpaired.append(record)
            continue
        if not view.ink:
            # Deterministic: a space is not written, so it holds no box whatever the run wrote.
            if old_box is None:
                record.status = "unchanged"
                record.layer = "deterministic"
                record.reason = "a space is not ink and holds no box"
            else:
                record.status = "applied"
                record.layer = "deterministic"
                record.hypothesis = "reading-order"
                record.reason = "a space is not ink; the box it held is freed for a character"
                decision.moved += 1
            records.append(record)
            continue
        if _same_box(old_box, new_box):
            record.status = "unchanged"
            record.layer = "deterministic"
            record.reason = "the corrected order agrees with the recorded box"
            records.append(record)
            continue
        if old_box is None and not place_missing:
            record.status = "uncertain"
            record.reason = ("the character has no box now and this pass does not place new ink "
                             "unless it is asked to")
            decision.withheld += 1
            records.append(record)
            continue
        if new_box is None:
            if _box_key(old_box) in taken:
                record.status = "applied"
                record.layer = "deterministic"
                record.hypothesis = "reading-order"
                record.reason = ("the corrected order gives this box to another character of the "
                                 "line")
                decision.moved += 1
            else:
                record.status = "uncertain"
                record.reason = ("the corrected order leaves this character without ink; a reviewer "
                                 "settles where the missing character is")
                decision.withheld += 1
            records.append(record)
            continue
        if record.unit_margin is not None and record.unit_margin < contradiction_margin:
            record.status = "uncertain"
            record.reason = (f"the ink at the corrected position scores {abs(record.unit_margin):.2f} "
                             f"nats worse than another crop of the line; the pass does not move a "
                             f"character onto ink that looks like a different one")
            decision.withheld += 1
            records.append(record)
            continue
        record.layer = "proposal" if merged.shift else "deterministic"
        if merged.shift:
            record.hypothesis = "phase-shift"
            record.status = "applied"
            record.reason = (f"the line reads {merged.shift:+d} detections against the transcription: "
                             f"{merged.reason}")
            decision.moved += 1
        elif record.unit_margin is not None and record.unit_margin >= confirm_margin:
            record.status = "confirmed"
            record.reason = (f"the corrected order gives this character different ink and its crop "
                             f"beats the next best by {record.unit_margin:.2f} nats")
            decision.moved += 1
            decision.confirmed += 1
        else:
            record.status = "applied"
            record.reason = ("the corrected order gives this character different ink; the classifier "
                             "does not separate the two crops, so this is the order alone")
            decision.moved += 1
        records.append(record)
    resolved = resolve_collisions(records)
    # A line is read as one sequence, so it is placed as one sequence. `resolve_collisions` keeps one
    # box one owner; this keeps one line one decision. A move is only correct together with the moves
    # the same phase makes to its neighbours, so one unsettled character takes every ink move of the
    # line back to a proposal. A space's box is freed regardless: that is not a placement. A character
    # whose box already agreed keeps it, but the pass no longer vouches for it: agreeing with an order
    # the line cannot be placed in is no evidence, and an unvouched crop stays out of the quiz.
    blockers = unpaired + [record for record in records
                           if record.ink and record.status == "uncertain" and record not in unpaired]
    demoted = 0
    if blockers:
        for record in records:
            if record.ink and record.status in ("applied", "confirmed"):
                record.status = "uncertain"
                record.layer = "deterministic"
                record.hypothesis = "reading-order"
                record.reason = ("the line is placed as one sequence or not at all; this move stays "
                                 "a proposal: " + blockers[0].reason)
                demoted += 1
            elif record.ink and record.status == "unchanged":
                record.status = "uncertain"
                record.reason = ("the line is placed as one sequence or not at all; this box is kept "
                                 "but not vouched for: " + blockers[0].reason)
    decision.moved = sum(1 for record in records if record.status in ("applied", "confirmed"))
    decision.confirmed = sum(1 for record in records
                             if record.status == "confirmed" and record.machine)
    decision.withheld = sum(1 for record in records if record.status == "uncertain")
    decision.human_units = sum(1 for record in records if record.status == "human")
    decision.conflicts = sum(1 for record in records
                             if record.status == "human" and record.changed)
    decision.reverted = resolved["reverted"] + demoted  # type: ignore[attr-defined]
    decision.collisions = resolved["baseline-duplicates"]  # type: ignore[attr-defined]
    moved_ink = sum(1 for record in records
                    if record.ink and record.status in ("applied", "confirmed"))
    if moved_ink and merged.shift:
        decision.status = "shift"
        decision.reason = f"a {merged.shift:+d} detection shift: {merged.reason}"
    elif moved_ink:
        decision.status = "reorder"
        decision.reason = (f"{moved_ink} characters placed on the ink the corrected order names "
                           f"({decision.confirmed} confirmed by the classifier)")
    elif decision.conflicts:
        decision.status = "human"
        decision.reason = "every correction this pass would make lands on a human-reviewed unit"
    elif decision.human_units:
        decision.status = "human"
        decision.reason = "the line's differing characters are all human-reviewed"
    elif demoted:
        decision.status = "uncertain"
        decision.reason = ("the line cannot be placed as one sequence: "
                           f"{decision.withheld} of its characters are unsettled, so every box of "
                           "it stays as it is")
    elif decision.withheld:
        decision.status = "uncertain"
        decision.reason = f"{decision.withheld} characters differ but nothing settles them"
    else:
        decision.status = "unchanged"
        decision.reason = "the corrected order agrees with every recorded box"
    return decision, records


def _merge(views: Sequence[LineView]) -> LineView:
    """One line's containers as one view, for the decision and the records.

    A 割書 line is read container by container; the decision is about the line, so the units are
    concatenated and the phases are pooled by cost. The chosen phase of the whole line is the cheapest
    of the containers' and the coverage is the pooled one, because a line whose second column is thin
    is a line with a thin column, not two unrelated lines.
    """
    if len(views) == 1:
        return views[0]
    first = views[0]
    phases = sorted((phase for view in views for phase in view.phases), key=lambda item: item.cost)
    gap = Gap(head_detections=sum(view.gap.head_detections for view in views),
              tail_detections=sum(view.gap.tail_detections for view in views),
              interior_detections=sum(view.gap.interior_detections for view in views),
              unplaced_characters=sum(view.gap.unplaced_characters for view in views))
    return LineView(
        line=first.line,
        tokens=[token for view in views for token in view.tokens],
        detections=[detection for view in views for detection in view.detections],
        units=[unit for view in views for unit in view.units],
        phases=phases,
        chosen=first.chosen,
        unshifted=first.unshifted,
        reason=first.reason,
        gap=gap,
        before=first.before,
        after=first.after,
    )


def _human(view: UnitView, pinned: Collection[str] = ()) -> bool:
    """Whether a person, not the pipeline, wrote what this unit says now.

    `pinned` names the units the review store holds events for. `apply_plan` lays the store's row
    over each of them, so a move decided for one would be logged and then silently overwritten.
    """
    return view.unit is not None and (view.unit.review in HUMAN_REVIEW or view.unit.id in pinned)


def _record(view: LineView, unit: UnitView, line: Line) -> RepairRecord:
    """The diagnosis of one unit, before the decision says whether to apply it."""
    token = unit.token
    previous = view.units[unit.index - 1] if unit.index > 0 else None
    following = view.units[unit.index + 1] if unit.index + 1 < len(view.units) else None
    record = RepairRecord(
        id="",
        unit_id=unit.unit.id if unit.unit is not None else "",
        line_id=line.id,
        page_id=line.page_id,
        seq=unit.unit.seq if unit.unit is not None else unit.index + 1,
        text_source=token.text or None,
        reading=token.reading,
        unicode=token.unicode,
        kind=token.kind,
        granularity=unit.unit.granularity if unit.unit is not None else "char",
        ink=unit.ink,
        old_box=unit.old_box,
        new_box=unit.detection.box if unit.detection is not None else None,
        old_review=unit.unit.review if unit.unit is not None else ReviewState.MACHINE,
        phase=view.chosen.first,
        phase_margin=view.total_margin,
        phase_pair_margin=view.pair_margin,
        unit_margin=unit.margin,
        cost_old=unit.cost_old,
        cost_new=unit.cost_new,
        before=_neighbour(previous),
        after=_neighbour(following),
    )
    if token.kind in AMBIGUOUS_KINDS and record.unit_margin is not None:
        # A ligature or an iteration mark is not one character on one crop, so the classifier's
        # margin for it is not on the same scale as an ordinary character's.
        record.unit_margin = min(record.unit_margin, MIN_UNIT_MARGIN - 1e-9)
    return record


def _neighbour(unit: UnitView | None) -> Neighbour | None:
    if unit is None:
        return None
    return Neighbour(seq=unit.unit.seq if unit.unit is not None else unit.index + 1,
                     text=unit.token.text or None, reading=unit.token.reading,
                     unicode=unit.token.unicode,
                     box=unit.detection.box if unit.detection is not None else unit.old_box)


class CropReader:
    """A callable `(page_id, box)` that cuts a crop out of the cached page image.

    One page is decoded at a time and held, because a page holds hundreds of detections and opening a
    9 MB scan once per crop is the difference between a page taking a second and taking a minute.
    Nothing is downloaded: a page whose image is not cached is an error for this pass, because a
    repair is not allowed to change what the detector saw.
    """

    def __init__(self, directory: Path, *, image_cache: Path | None = None) -> None:
        self.directory = Path(directory)
        self.image_cache = Path(image_cache) if image_cache is not None else None
        self.pages = {page.id: page for page in tables.Dataset(self.directory).read("pages")}
        self._page_id: str | None = None
        self._image: Any = None
        self.missing: set[str] = set()

    def __call__(self, page_id: str | None, box: Box) -> Any:
        page = self.pages.get(page_id or "")
        if page is None:
            raise RepairError(f"{page_id}: no such page in {self.directory}")
        if self._page_id != page_id or self._image is None:
            path = images.path_for(page.image, root=self.image_cache)
            if path is None:
                self.missing.add(page_id or "")
                raise RepairError(f"{page_id}: the page image is not in the cache; the repair pass "
                                  f"reads what the alignment read and never downloads")
            from PIL import Image

            with Image.open(path) as handle:
                handle.load()
                self._image = handle.copy()
            self._page_id = page_id
        return self._image.crop((box.x, box.y, box.x + box.w, box.y + box.h)).convert("RGB")


def detections_for(directory: Path, cache: Path | None = None) -> dict[str, list[Box]]:
    """The detections the units were aligned against, from the census cache beside the dataset.

    The repair has to see the boxes the detector found when the units were written, not today's
    detections: a box that moved since would make every comparison between the recorded unit and the
    corrected one a comparison of two different pages.
    """
    path = cache if cache is not None else Path(directory) / "detections.jsonl"
    if not path.exists():
        raise RepairError(f"{path} is missing; a repair pass needs the detections the units came from")
    return ainu.read_detections(path)


def sample_crops(directory: Path | str, found: dict[str, list[Box]], *, count: int = 32,
                 image_cache: Path | None = None) -> list[Any]:
    """Some real crops of a dataset, for checking one classifier backend against another.

    The check has to be on this corpus: the two backends agree to the last bit on any crop they were
    both handed, and what a repair needs to know is whether they agree on *these* pages, which is
    where a preprocessing difference would show.
    """
    reader = CropReader(directory, image_cache=image_cache)
    crops: list[Any] = []
    for page_id in sorted(found):
        for box in found[page_id]:
            if len(crops) >= count:
                return crops
            try:
                crops.append(reader(page_id, box))
            except RepairError:
                break
    return crops


def public_path(path: Path | str | None) -> str | None:
    """A path as it may be written into a plan, a report or a log.

    These files are published and copied between machines, so a path in one is written relative to
    the working directory when it can be, and with the home directory shortened to `~` when it
    cannot. A plan that names a user's home directory is a plan that carries their name.
    """
    if path is None:
        return None
    candidate = Path(path)
    for base, prefix in ((Path.cwd(), Path()), (Path.home(), Path("~"))):
        try:
            return str(prefix / candidate.resolve().relative_to(base.resolve())) or "."
        except ValueError:
            continue
    return candidate.name if candidate.is_absolute() else str(candidate)


def file_evidence(path: Path) -> dict[str, Any]:
    """What a file has to be, for a later run to know it is repairing the same bytes."""
    if not path.exists():
        return {"path": public_path(path), "exists": False}
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    stat = path.stat()
    return {"path": public_path(path), "exists": True, "bytes": stat.st_size,
            "mtime": int(stat.st_mtime), "sha256": digest.hexdigest()}



# --- the derived dataset's plan ------------------------------------------------------------------


class ScoreCache:
    """The classifier's probabilities for a page's crops, kept so a second pass pays for none of them.

    One pass over the boxed lines of this corpus is about twenty thousand crops; a plan that is
    reviewed, adjusted and run again should not score them again, and neither should a comparison of
    two placement rules. The cache is keyed by the model file's hash, the page and the exact crop
    geometry, so a different export or a box that moved is a miss rather than a wrong answer.
    """

    def __init__(self, directory: Path | str | None = None) -> None:
        self.directory = Path(directory) if directory else None
        self.hits = 0
        self.misses = 0
        self.model: str | None = None

    def probabilities(self, page_id: str | None, boxes: Sequence[Box], *, classifier: Any,
                      crop_of: Any) -> Any:
        """The class probabilities of one page's crops, in the order the boxes were given."""
        if classifier is None or crop_of is None:
            raise RepairError("a score cache needs a classifier and a crop reader")
        self.model = self._model_of(classifier)
        key = self._key(page_id, boxes)
        if self.directory is not None:
            found = self._read(key)
            if found is not None:
                self.hits += 1
                return found
        crops = [crop_of(page_id, box) for box in boxes]
        probabilities = np.asarray(classifier.probabilities_many(crops), dtype=np.float64)
        if self.directory is not None:
            self._write(key, page_id, boxes, probabilities, len(classifier.classes))
        self.misses += 1
        return probabilities

    def _model_of(self, classifier: Any) -> str:
        if hasattr(classifier, "describe"):
            described = classifier.describe()
            return str(described.get("sha256") or described.get("checkpoint") or "torch")
        path = Path(getattr(classifier, "onnx_path", ""))
        return file_evidence(path).get("sha256") or public_path(path) or ""

    def _key(self, page_id: str | None, boxes: Sequence[Box]) -> str:
        payload = "|".join([self.model or "", page_id or "",
                            ";".join(f"{box.x},{box.y},{box.w},{box.h}" for box in boxes)])
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def path_for(self, key: str) -> Path:
        return Path(self.directory) / key[:2] / f"{key}.npy"  # type: ignore[arg-type]

    def _read(self, key: str) -> Any:
        path = self.path_for(key)
        meta = path.with_suffix(".json")
        if not path.exists() or not meta.exists():
            return None
        try:
            record = json.loads(meta.read_text(encoding="utf-8"))
            if record.get("model") != self.model or record.get("key") != key:
                return None
            return np.load(path)
        except (OSError, ValueError):
            return None

    def _write(self, key: str, page_id: str | None, boxes: Sequence[Box], probabilities: Any,
               classes: int) -> None:
        path = self.path_for(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        np.save(path, probabilities)
        path.with_suffix(".json").write_text(json.dumps(
            {"key": key, "model": self.model, "page_id": page_id, "classes": classes,
             "boxes": [[box.x, box.y, box.w, box.h] for box in boxes]}
        ), encoding="utf-8")

    def describe(self) -> dict[str, Any]:
        return {"directory": public_path(self.directory) if self.directory else None,
                "hits": self.hits, "misses": self.misses, "model": self.model}


def plan_directory(
    directory: Path | str,
    *,
    run: Run | None = None,
    classifier: Any = None,
    crop_of: Any = None,
    cache: Path | None = None,
    image_cache: Path | None = None,
    scores: Path | None = None,
    pages: Sequence[str] | None = None,
    limit: int | None = None,
    confirm_margin: float = MIN_UNIT_MARGIN,
    contradiction_margin: float = CONTRADICTION_MARGIN,
    min_phase_margin: float = MIN_PHASE_MARGIN,
    place_missing: bool = False,
    human: HumanState | None = None,
) -> RepairPlan:
    """Diagnose every boxed line of `directory` and return the plan, writing nothing to the source.

    The lines and units are read with the review store's own state over them where a person has
    changed them: a unit a reviewer corrected exists in `review.sqlite` and may not be in the tables
    yet, and a pass that read only the tables would move the very box their answer was about.
    """
    directory = Path(directory)
    dataset = tables.Dataset(directory)
    if dataset.tables["lines"] is None or dataset.tables["units"] is None:
        raise RepairError(f"{directory} needs lines and units to repair")
    run = run or load_run(Path("models/align/runs/pilot-v1.yaml"))
    found = detections_for(directory, cache)
    if crop_of is None:
        crop_of = CropReader(directory, image_cache=image_cache)
    human = human if human is not None else human_state(directory)
    units_by_line: dict[str, list[Unit]] = {}
    for unit in dataset.read("units"):
        if unit.active:
            units_by_line.setdefault(unit.line_id or "", []).append(unit)
    pinned = frozenset(human.units)
    for unit_id, unit in human.units.items():
        line_units = units_by_line.setdefault(unit.line_id or "", [])
        line_units[:] = [existing for existing in line_units if existing.id != unit_id]
        if unit.active:
            line_units.append(unit)
    page_documents = {page.id: page.document_id for page in dataset.read("pages")}
    wanted = set(pages) if pages is not None else None
    store = ScoreCache(scores)
    plan = RepairPlan(
        created=datetime.now(UTC).isoformat(timespec="seconds"),
        thresholds={"confirm_margin": confirm_margin, "contradiction_margin": contradiction_margin,
                    "min_phase_margin": min_phase_margin, "max_phase": MAX_PHASE,
                    "min_per_pair_margin": MIN_PER_PAIR_MARGIN, "place_missing": place_missing},
        source={"directory": public_path(directory), "tables": {
            name: file_evidence(path) for name, path in sorted(dataset.tables.items())
            if path is not None and name in ("lines", "units", "pages", "documents", "page_texts")}},
        run={"name": run.name, "fingerprint": run.fingerprint(), "policy": run.policy,
             "weights": dict(run.weights), "accept": run.accept, "margin": run.margin,
             "score": run.score, "nms": run.nms},
        detector={"cache": file_evidence(cache or directory / "detections.jsonl")},
        human={"store": human.describe()},
    )
    counts: dict[str, Any] = {
        "lines": 0, "lines-with-box": 0, "inspected": 0, "skipped-no-detection": 0,
        "units": 0, "units-boxed": 0, "records": 0, "moved": 0, "confirmed": 0, "withheld": 0,
        "unchanged": 0, "human": 0, "conflicts": 0, "placed": 0, "withdrawn": 0, "spaces-freed": 0,
        "deterministic": 0, "proposal": 0, "phase-traced": 0,
        "inversions-before": 0, "inversions-after": 0, "lines-in-order-before": 0,
        "lines-in-order-after": 0, "phases": {}, "statuses": {}, "documents": 0,
        "ink-tokens": 0, "detections": 0, "detections-used": 0,
    }
    documents: set[str] = set()
    inspected = 0
    for line in dataset.read("lines"):
        counts["lines"] += 1
        if wanted is not None and line.page_id not in wanted:
            continue
        units = units_by_line.get(line.id, [])
        if line.box is None or not units:
            continue
        counts["lines-with-box"] += 1
        if limit is not None and inspected >= limit:
            continue
        boxes = found.get(line.page_id or "")
        if not boxes:
            counts["skipped-no-detection"] += 1
            continue
        try:
            views = view_line(line, [Detection(box=box, score=0.0) for box in boxes], run=run,
                              classifier=classifier, crop_of=crop_of, units=units, scores=store,
                              min_phase_margin=min_phase_margin)
        except RepairError:
            counts["skipped-no-detection"] += 1
            continue
        decision, records = decide(views, line=line, confirm_margin=confirm_margin,
                                   contradiction_margin=contradiction_margin,
                                   place_missing=place_missing, pinned=pinned)
        document = page_documents.get(line.page_id or "")
        decision.document_id = document
        for record in records:
            record.document_id = document
        inspected += 1
        if document:
            documents.add(document)
        counts["inspected"] += 1
        counts["units"] += len(records)
        counts["ink-tokens"] += decision.ink
        counts["detections"] += decision.detections
        counts["detections-used"] += sum(len(view.chosen.assignment) for view in views)
        counts["units-boxed"] += sum(1 for record in records if record.old_box is not None)
        counts["records"] += len(records)
        counts["statuses"][decision.status] = counts["statuses"].get(decision.status, 0) + 1
        counts["phases"][decision.phase] = counts["phases"].get(decision.phase, 0) + 1
        if decision.shift:
            counts["phase-traced"] += 1
        for record in records:
            counts[f"layer-{record.layer}"] = counts.get(f"layer-{record.layer}", 0) + 1
            if record.status in ("applied", "confirmed"):
                counts["moved"] += 1
                counts[record.status] = counts.get(record.status, 0) + 1
                if not record.ink:
                    counts["spaces-freed"] += 1
                if record.new_box is None:
                    counts["withdrawn"] += 1
                elif record.old_box is None:
                    counts["placed"] += 1
            elif record.status == "uncertain":
                counts["withheld"] += 1
            elif record.status == "unchanged":
                counts["unchanged"] += 1
            elif record.status == "human":
                counts["human"] += 1
            if record.status == "human" and record.changed:
                counts["conflicts"] += 1
        counts["inversions-before"] += decision.inversions_before
        counts["inversions-after"] += decision.inversions_after
        counts["lines-in-order-before"] += int(decision.inversions_before == 0)
        counts["lines-in-order-after"] += int(decision.inversions_after == 0)
        plan.lines.append(decision)
        plan.records.extend(records)
    for index, record in enumerate(plan.records, start=1):
        record.id = f"ar{index:06d}"
    counts["documents"] = len(documents)
    counts["classified"] = count_classifier_evidence(plan.records)
    counts["scores"] = store.describe()
    counts["lines-not-inspected"] = counts["lines-with-box"] - counts["inspected"]
    plan.counts = counts
    return plan


def verify_dataset(directory: Path | str) -> dict[str, Any]:
    """Check a derived dataset's invariants and report them.

    Three things a repair must not have done: given two active units of one line the same box, left a
    moved unit drawing a crop file of the box it no longer has, or claimed a reliability it does not
    have. A viewer reads the dataset and not the plan, so the check is on the dataset.
    """
    directory = Path(directory)
    dataset = tables.Dataset(directory)
    if dataset.tables["units"] is None:
        raise RepairError(f"{directory}: no units table")
    units = list(dataset.read("units"))
    stale = []
    for unit in units:
        note = _note_of(unit)
        if note is None:
            continue
        if note.get("status") in ("applied", "confirmed") and unit.crop_sha256:
            stale.append(unit.id)
    human = human_state(directory)
    logs = _log_records(directory)
    conflicts = [record for record in logs if record.status == "human" and record.changed]
    carried = [unit for unit in units if unit.review in HUMAN_REVIEW]
    return {
        "directory": public_path(directory),
        "units": len(units),
        "active": sum(1 for unit in units if unit.active),
        "boxed": sum(1 for unit in units if unit.box is not None),
        "collisions": collisions(units),
        "stale-crops": stale,
        "noted": sum(1 for unit in units if _note_of(unit) is not None),
        "reliable": sum(1 for unit in units if (unit.meta or {}).get(META_KEY, {}).get("reliable")),
        "withheld": sum(1 for unit in units if (unit.meta or {}).get(META_KEY, {}).get("withheld")),
        "human-in-table": len(carried),
        "human-in-store": len(human.units),
        "human-lines": len(human.lines),
        "human-conflicts": len(conflicts),
        "log": len(logs),
    }


def _log_records(directory: Path) -> list[RepairRecord]:
    path = Path(directory) / LOG_NAME
    if not path.exists():
        return []
    return read_log(path)[1]


def read_plan(path: Path | str) -> RepairPlan:
    """Read a plan `plan_directory` wrote, so `apply` can run on a reviewed file."""
    return RepairPlan.model_validate_json(Path(path).read_text(encoding="utf-8"))


def count_classifier_evidence(records: Sequence[RepairRecord]) -> dict[str, int]:
    """How many records the classifier separated, in the buckets a reader wants to see.

    The separation is measured rather than assumed: the pass is only allowed to call a correction
    confirmed where the margin is wide, and this is the count of what it found at each width.
    """
    buckets = {"margin<-1": 0, "-1..0": 0, "0..1": 0, "1..2": 0, ">=2": 0, "none": 0}
    for record in records:
        if not record.changed:
            continue
        margin = record.unit_margin
        if margin is None:
            buckets["none"] += 1
        elif margin < -1:
            buckets["margin<-1"] += 1
        elif margin < 0:
            buckets["-1..0"] += 1
        elif margin < 1:
            buckets["0..1"] += 1
        elif margin < 2:
            buckets["1..2"] += 1
        else:
            buckets[">=2"] += 1
    return buckets


# --- human state ---------------------------------------------------------------------------------


@dataclass
class HumanState:
    """What people have decided, read without writing to the store they decided it in.

    The review server keeps its own copy of the lines and units and replays the events over it, so a
    correction a person made may exist only in `review.sqlite` and not yet in the tables. A repair
    that read only the tables would move the very unit their answer was about, so the state is read
    from the store — read-only, through a `mode=ro` connection — and the units a person touched are
    carried into the derived dataset as they stand.

    The journal holds more than units and lines: a page's transcription correction is an event on the
    *page* (`review.corrections`, `field="correction"`), and a viewer's decision can be on a document
    or a group. Those have no row for this pass to overlay — a correction is a layer over the source
    text and the store's own projection leaves it in the journal — so they are carried as they stand
    into `reviews.jsonl` while the overlay stays unit-and-line only. A derived dataset that dropped
    them would be a dataset whose editorial decisions are silently narrower than the source's.
    """

    units: dict[str, Unit] = field(default_factory=dict)
    lines: dict[str, Line] = field(default_factory=dict)
    #: Every event of the journal, whatever it is about, because `reviews.jsonl` is the log a store
    #: rebuilds from. The overlay above is only the part of it this pass can put on a row.
    rows: list[dict[str, Any]] = field(default_factory=list)
    targets: Counter = field(default_factory=Counter)
    path: Path | None = None
    events: int = 0
    readable: bool = False
    reason: str = ""

    def describe(self) -> dict[str, Any]:
        return {"path": public_path(self.path), "readable": self.readable,
                "reason": self.reason, "units": len(self.units), "lines": len(self.lines),
                "events": self.events, "targets": dict(sorted(self.targets.items()))}

    def touched(self, unit_id: str) -> bool:
        return unit_id in self.units


def human_state(directory: Path | str) -> HumanState:
    """The rows a person changed, read read-only from the review store beside a dataset."""
    directory = Path(directory)
    path = directory / STORE_NAME
    state = HumanState(path=path)
    if not path.exists():
        state.reason = "no review store beside the dataset"
        return state
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error as error:  # pragma: no cover - a store that cannot be opened at all
        state.reason = f"{error}"
        return state
    try:
        connection.row_factory = sqlite3.Row
        rows = connection.execute("SELECT * FROM events ORDER BY seq").fetchall()
        state.events = len(rows)
        state.rows = [dict(row) for row in rows]
        state.targets = Counter(row["target_type"] for row in rows)
        unit_ids = {row["target_id"] for row in rows if row["target_type"] == "unit"}
        line_ids = {row["target_id"] for row in rows if row["target_type"] == "line"}
        for unit_id in unit_ids:
            row = connection.execute("SELECT data FROM units WHERE id = ?", (unit_id,)).fetchone()
            if row is not None:
                state.units[unit_id] = Unit.model_validate_json(row["data"])
        for line_id in line_ids:
            row = connection.execute("SELECT data FROM lines WHERE id = ?", (line_id,)).fetchone()
            if row is not None:
                state.lines[line_id] = Line.model_validate_json(row["data"])
        state.readable = True
        state.reason = "read read-only"
    except (sqlite3.Error, ValueError) as error:
        state.reason = f"{error}"
    finally:
        connection.close()
    return state


# --- the derived dataset -------------------------------------------------------------------------


def apply_plan(plan: RepairPlan, out: Path | str, *, source: Path | str | None = None,
               statuses: Sequence[str] = ("applied", "confirmed")) -> dict[str, Any]:
    """Write the derived dataset: the source's tables with this pass's boxes applied.

    The derived directory is a dataset in its own right — the same tables, the same unit ids, the same
    transcriptions — and the differences are exactly the records whose status is in `statuses`, plus a
    note on every unit the pass looked at and could not settle.

    Four rules decide what a written row looks like.

    - A box that moves invalidates everything that was computed from the box. The unit's `crop` and
      `crop_sha256` are cleared, because a viewer that prefers a pre-cut crop file would otherwise
      draw the old ink under the new label, and its `confidence` is cleared, because that number was
      the old placement's. The log keeps all of them, so `undo` restores the row exactly.
    - A unit a person reviewed is written as the review store has it, whatever this pass decided about
      the line it is on.
    - A unit the pass withheld carries the same note with `reliable=false`, and a unit it moved
      carries `reliable=true` with the margins; a viewer reads that flag instead of guessing from the
      status which crops may be shown as exemplars.
    - The final tables are checked for two labels on one box after the human rows have been laid over
      the repaired ones, because a human row is the one row a machine may not overwrite and it can
      land on a box this pass just moved a character onto. A move that would duplicate a box is put
      back, and the log says so.
    """
    source = Path(source) if source is not None else Path(plan.source.get("directory", ""))
    if not source or not Path(source).exists():
        raise RepairError(f"{source}: the source dataset of this plan is not there")
    out = Path(out)
    if out.resolve() == Path(source).resolve():
        raise RepairError(f"{out}: a repair writes a derived dataset, never over its source")
    out.mkdir(parents=True, exist_ok=True)
    dataset = tables.Dataset(Path(source))
    human = human_state(source)
    chosen = [record for record in plan.records if record.status in statuses]
    notes = [record for record in plan.records
             if record.status in ("applied", "confirmed", "uncertain")]
    moved = {record.unit_id: record for record in chosen}
    noted = {record.unit_id: record for record in notes}
    # A dataset may hold pages and lines and no units at all — a transcription that has not been
    # aligned, with corrections on its pages — and the journal still has to travel with it.
    has_units = dataset.tables.get("units") is not None
    units = list(dataset.read("units")) if has_units else []
    written: list[RepairRecord] = []
    for unit in units:
        record = moved.get(unit.id)
        if record is not None and unit.review not in HUMAN_REVIEW:
            _move_unit(unit, record)
            written.append(record)
        elif unit.id in noted and unit.review not in HUMAN_REVIEW:
            unit.meta = {**(unit.meta or {}), META_KEY: _meta_of(noted[unit.id])}
    # A unit the store holds and the tables do not is a unit a person made; it has to survive.
    positions = {unit.id: index for index, unit in enumerate(units)}
    overlaid = 0
    for unit_id, unit in human.units.items():
        position = positions.get(unit_id)
        if position is None:
            positions[unit_id] = len(units)
            units.append(_without_repair_note(unit))
        else:
            units[position] = _without_repair_note(unit)
        overlaid += 1
    counts: dict[str, Any] = {"units": len(units), "requested": len(chosen),
                              "human-units": overlaid, "human-lines": len(human.lines),
                              "noted": len(noted)}
    # The overlay is in place, so the invariant is checked on what is about to be written, not on
    # what the per-unit gate believed when it decided.
    after = enforce_injective(units)
    counts.update({"applied": sum(1 for unit in units if META_KEY in (unit.meta or {})
                                  and (unit.meta or {})[META_KEY].get("status") in statuses),
                   "reliable": sum(1 for unit in units if (unit.meta or {}).get(META_KEY, {})
                                   .get("reliable")),
                   "withheld-noted": sum(1 for unit in units if (unit.meta or {}).get(META_KEY, {})
                                         .get("withheld")),
                   "reverted-after-overlay": after["reverted"],
                   "baseline-duplicates": after["baseline"]})
    # The log has to name the withheld units too: undo takes their note off as well as putting a
    # moved box back, and a log that only held the moves could not do it.
    already = {record.unit_id for record in written}
    for unit in units:
        record = noted.get(unit.id)
        if record is not None and unit.id not in already and META_KEY in (unit.meta or {}):
            written.append(record)
            already.add(unit.id)
    for name in COPIED_TABLES:
        path = dataset.tables.get(name)
        if path is None or name == "units":
            continue
        target = out / Path(path).name
        if Path(path).is_dir():
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(path, target)
        else:
            shutil.copy2(path, target)
        counts[f"copied-{name}"] = 1
    # The lines a person changed are written as the store has them, box included, so the derived
    # dataset states the same editorial facts as the source it came from.
    source_lines = dataset.tables.get("lines")
    lines_path = out / Path(source_lines).name if source_lines is not None else out / "lines.parquet"
    if human.lines and lines_path.exists():
        lines = list(tables.read(lines_path, Line))
        for position, line in enumerate(lines):
            edited = human.lines.get(line.id)
            if edited is not None:
                lines[position] = edited
        tables.write(lines_path, lines, Line, shard=lines_path.is_dir(),
                     command=f"atlas repair apply {source}")
        counts["lines-with-human"] = len(human.lines)
    if has_units:
        tables.write(out / "units.parquet", units, Unit, command=f"atlas repair apply {source}")
    if human.rows:
        # The events themselves, so a store opened on the derived dataset replays the human decisions
        # instead of trusting this pass's copy of their result.
        with (out / REVIEWS_NAME).open("w", encoding="utf-8") as handle:
            for row in human.rows:
                handle.write(json.dumps(decode_event(row), ensure_ascii=False,
                                        sort_keys=True) + "\n")
        counts["reviews"] = len(human.rows)
    counts["log"] = write_log(out / LOG_NAME, plan, written)
    counts["table"] = write_table(out / TABLE_NAME, written)
    (out / PLAN_NAME).write_text(plan.model_dump_json(indent=2), encoding="utf-8")
    report = {"policy": plan.policy, "created": plan.created, "source": plan.source,
              "counts": plan.counts, "applied": counts, "thresholds": plan.thresholds,
              "classifier": plan.classifier, "human": plan.human}
    (out / REPORT_NAME).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    counts["out"] = public_path(out)
    return counts


def _move_unit(unit: Unit, record: RepairRecord) -> None:
    """Put a record's new box on a unit, and drop everything the old box decided.

    A crop file is a picture of the old rectangle and a confidence is a number about it; both would
    be read as facts about the new one. They are cleared rather than regenerated, because a repair
    pass has no crop writer and a stale picture is worse than none: the viewer falls back to cutting
    the box out of the page, which is the ink the record names. The log keeps the values so `undo`
    restores the row exactly as it was. A record the pass withheld does not move the unit at all: only
    `applied` and `confirmed` carry a box, and the note they leave says which it was.
    """
    if record.status not in ("applied", "confirmed"):
        unit.meta = {**(unit.meta or {}), META_KEY: _meta_of(record)}
        return
    record.old_crop = unit.crop
    record.old_crop_sha256 = unit.crop_sha256
    record.old_confidence = unit.confidence
    unit.box = record.new_box
    if record.new_box is None or not _same_box(record.old_box, record.new_box):
        unit.crop = None
        unit.crop_sha256 = None
        unit.confidence = None
    unit.meta = {**(unit.meta or {}), META_KEY: _meta_of(record)}


def enforce_injective(units: Sequence[Unit]) -> dict[str, Any]:
    """Put back any repair that leaves two units of a line holding the same box.

    Checked on the finished rows rather than on the plan, because the human overlay is applied after
    the machine records: a person's row is authoritative and can hold the box a move just took, and a
    viewer that cuts one rectangle for two labels is worse than either decision alone. Only a unit
    this pass moved is ever put back — a collision the source already had is reported and left.
    """
    reverted = 0
    baseline = 0
    for _ in range(len(units) + 1):
        owners: dict[tuple[str, tuple[int, int, int, int]], list[Unit]] = {}
        for unit in units:
            if not unit.active or unit.box is None or unit.line_id is None:
                continue
            owners.setdefault((unit.line_id, _box_key(unit.box)), []).append(unit)  # type: ignore[arg-type]
        clashing = [group for group in owners.values() if len(group) > 1]
        if not clashing:
            break
        progress = False
        for group in clashing:
            repaired = [unit for unit in group if _moved(unit)]
            for unit in repaired:
                note = _note_of(unit) or {}
                old = note.get("old_box")
                unit.box = Box.model_validate(old) if old else None
                note = {**note, "status": "uncertain", "reliable": False, "withheld": True,
                        "reason": (note.get("reason", "") +
                                   "; put back after the human rows were laid over: the box would be "
                                   "held by two units").strip("; ")}
                unit.meta = {**(unit.meta or {}), META_KEY: note}
                unit.crop = None
                unit.crop_sha256 = None
                unit.confidence = None
                reverted += 1
                progress = True
            if not repaired:
                baseline += 1
        if not progress:
            break
    return {"reverted": reverted, "baseline": baseline}


def _moved(unit: Unit) -> bool:
    """Whether this pass moved the unit off the box it had, so that putting it back changes it.

    A withheld unit carries a note too, but it still holds its own box and its own crop; treating it
    as a move would wipe a crop the log never recorded, and undo could not bring it back.
    """
    note = _note_of(unit)
    if note is None or note.get("status") not in ("applied", "confirmed"):
        return False
    old = note.get("old_box")
    return _box_key(unit.box) != _box_key(Box.model_validate(old) if old else None)


def _note_of(unit: Unit) -> dict[str, Any] | None:
    note = (unit.meta or {}).get(META_KEY)
    if isinstance(note, dict) and note.get("status") in ("applied", "confirmed", "uncertain"):
        return note
    return None


def decode_event(row: dict[str, Any]) -> dict[str, Any]:
    """One event of the store's own table, with its JSON columns decoded.

    The store keeps `old` and `new` as JSON *text* in SQLite, so a row copied out verbatim and
    written to a log is a string of JSON inside JSON: a store reading it back sees the literal
    `"reviewed"`, quotes included, and refuses it. Decoding those two is what makes the log a log of
    events rather than a copy of a table, and only the fields of `schema.Review` are kept, because
    that is the model the store reads the log with: `evidence` stays the string it is, and the
    server's own `result`, the client id and the store's `seq` are not part of an event.
    """
    decoded: dict[str, Any] = {}
    for key, value in row.items():
        if key not in ("id", "target_type", "target_id", "field", "old", "new", "role", "actor",
                       "evidence", "at"):
            continue
        if key in ("old", "new") and isinstance(value, str):
            try:
                decoded[key] = json.loads(value)
                continue
            except ValueError:
                pass
        decoded[key] = value
    return decoded


def _without_repair_note(unit: Unit) -> Unit:
    """A unit as the store has it, with anything this pass wrote about it removed.

    The store is the editorial state; this pass is a machine proposal. A human row is written as it
    stands — its box is the ink their answer was read against — and the repair's note is dropped,
    because a note about a box the row no longer has is worse than no note.
    """
    unit.meta = {key: value for key, value in (unit.meta or {}).items() if key != META_KEY}
    return unit


def _meta_of(record: RepairRecord) -> dict[str, Any]:
    """What a repaired unit carries in its own record, so a viewer need not read the log."""
    return {
        "policy": POLICY,
        "record": record.id,
        "status": record.status,
        "layer": record.layer,
        "hypothesis": record.hypothesis,
        "phase": record.phase,
        "phase_pair_margin": record.phase_pair_margin,
        "old_box": record.old_box.model_dump(mode="json") if record.old_box else None,
        "unit_margin": record.unit_margin,
        "phase_margin": record.phase_margin,
        "machine": True,
        "verified": False,
        "reliable": record.reliable,
        "withheld": record.status == "uncertain",
        "quiz": record.reliable,
        # The reason is the point of the note: a viewer that suppresses a crop has to be able to say
        # what the pass could not decide about it.
        "reason": record.reason,
    }


def write_log(path: Path, plan: RepairPlan, records: Sequence[RepairRecord]) -> int:
    """Write the reversible log: the header, then one record a changed box."""
    with path.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps({"kind": "glyph-atlas-alignment-repair", "version": 1,
                                 "policy": plan.policy, "created": plan.created,
                                 "source": plan.source, "run": plan.run, "classifier": plan.classifier,
                                 "detector": plan.detector, "thresholds": plan.thresholds,
                                 "human": plan.human}, ensure_ascii=False) + "\n")
        for record in records:
            handle.write(record.model_dump_json() + "\n")
    return len(records)


def write_table(path: Path, records: Sequence[RepairRecord]) -> int:
    """Write the same records as a tab-separated table, for a person reading the pass's work."""
    fields = ("id", "status", "unit_id", "seq", "text_source", "reading", "unicode", "old_box",
              "new_box", "phase", "unit_margin", "phase_margin", "hypothesis", "reason")
    with path.open("w", encoding="utf-8") as handle:
        handle.write("\t".join(fields) + "\n")
        for record in records:
            row = {
                "id": record.id, "status": record.status, "unit_id": record.unit_id,
                "seq": record.seq, "text_source": record.text_source, "reading": record.reading,
                "unicode": record.unicode,
                "old_box": _box_text(record.old_box), "new_box": _box_text(record.new_box),
                "phase": record.phase,
                "unit_margin": "" if record.unit_margin is None else f"{record.unit_margin:.3f}",
                "phase_margin": "" if record.phase_margin is None else f"{record.phase_margin:.3f}",
                "hypothesis": record.hypothesis, "reason": record.reason,
            }
            handle.write("\t".join(_cell(row[field]) for field in fields) + "\n")
    return len(records)


def _cell(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("\t", " ").replace("\n", " ")


def _box_text(box: Box | None) -> str:
    return "" if box is None else f"{box.x},{box.y},{box.w},{box.h}"


def undo_plan(out: Path | str, *, statuses: Sequence[str] = ("applied", "confirmed")) -> dict[str, Any]:
    """Reverse this pass in a derived dataset, from its own log.

    Undo is driven by the rows rather than by the log's own state: a unit is put back when it still
    carries the note this pass wrote on it, and a unit that does not is left alone. That makes the
    command idempotent — running it twice is running it once — and it means the log stays the record
    of what the pass proposed, which is what a reviewer reads, instead of a record of how many times
    somebody has pressed undo. The box, the crop file name, its checksum and the confidence all come
    back, because a row and not a rectangle is what was changed.
    """
    out = Path(out)
    log = out / LOG_NAME
    if not log.exists():
        raise RepairError(f"{log} is missing; there is no correction to undo")
    _, records = read_log(log)
    units_path = out / "units.parquet"
    if not units_path.exists():
        raise RepairError(f"{units_path} is missing")
    units = list(tables.read(units_path, Unit))
    by_id = {record.unit_id: record for record in records}
    restored = 0
    cleared = 0
    for unit in units:
        record = by_id.get(unit.id)
        note = _note_of(unit)
        if note is None:
            continue
        if record is not None and record.status in statuses and record.changed:
            unit.box = record.old_box
            unit.crop = record.old_crop
            unit.crop_sha256 = record.old_crop_sha256
            unit.confidence = record.old_confidence
            restored += 1
        else:
            cleared += 1
        unit.meta = {key: value for key, value in (unit.meta or {}).items() if key != META_KEY}
    tables.write(units_path, units, Unit, command=f"atlas repair undo {out}")
    return {"restored": restored, "notes-cleared": cleared, "records": len(records),
            "out": public_path(out)}


def replay_plan(out: Path | str, *, statuses: Sequence[str] = ("applied", "confirmed")) -> dict[str, Any]:
    """Apply this pass's log again to a derived dataset whose corrections were undone.

    The same rule read the other way: a unit that carries no note gets the one the log holds, and a
    unit the log moved is moved again. Like `undo` it is idempotent, and it never touches a unit a
    person reviewed.
    """
    out = Path(out)
    log = out / LOG_NAME
    if not log.exists():
        raise RepairError(f"{log} is missing; there is no correction to replay")
    _, records = read_log(log)
    units_path = out / "units.parquet"
    if not units_path.exists():
        raise RepairError(f"{units_path} is missing")
    units = list(tables.read(units_path, Unit))
    by_id = {record.unit_id: record for record in records}
    applied = 0
    noted = 0
    for unit in units:
        record = by_id.get(unit.id)
        if record is None or unit.review in HUMAN_REVIEW or _note_of(unit) is not None:
            continue
        if record.status in statuses and record.changed:
            _move_unit(unit, record)
            applied += 1
        else:
            unit.meta = {**(unit.meta or {}), META_KEY: _meta_of(record)}
            noted += 1
    enforce_injective(units)
    tables.write(units_path, units, Unit, command=f"atlas repair replay {out}")
    return {"replayed": applied, "noted": noted, "records": len(records),
            "out": public_path(out)}


def read_log(path: Path) -> tuple[dict[str, Any], list[RepairRecord]]:
    """Read a repair log: its header and its records."""
    header: dict[str, Any] = {}
    records: list[RepairRecord] = []
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle):
            if not line.strip():
                continue
            payload = json.loads(line)
            if number == 0 and payload.get("kind"):
                header = payload
                continue
            records.append(RepairRecord.model_validate(payload))
    return header, records


def _plan_of(header: dict[str, Any]) -> RepairPlan:
    return RepairPlan(policy=header.get("policy", POLICY), created=header.get("created", ""),
                      source=header.get("source", {}), run=header.get("run", {}),
                      classifier=header.get("classifier", {}), detector=header.get("detector", {}),
                      thresholds=header.get("thresholds", {}), human=header.get("human", {}))


# --- classifier backends -------------------------------------------------------------------------


class TorchClassifier:
    """The classifier the ONNX export was made from, run on a CUDA device.

    The pipeline's classifier is the exported ONNX file, and in this environment onnxruntime offers no
    CUDA provider, so a pass that scores tens of thousands of crops would run on the CPU. The weights
    are the same weights: the checkpoint the export was made from is read, the architecture is rebuilt
    with `timm` and the calibrated probabilities are `softmax(logits / temperature)`, which is what
    the export's own `probs` output computes. `parity` checks the two against each other on real
    crops and the result is written into the plan, because a repair computed with a different
    probability than the alignment used is a repair that cannot be compared with it.
    """

    def __init__(self, checkpoint: Path | str, *, device: str = "cuda", size: int = 96,
                 batch: int = 256, threads: int = 2) -> None:
        import torch

        # The crops are prepared on the CPU and the model runs on the device; the thread count is
        # pinned so a pass over twenty thousand crops does not take the whole machine with it.
        torch.set_num_threads(max(1, threads))
        self.path = Path(checkpoint)
        if not self.path.exists():
            raise RepairError(f"{self.path}: no such checkpoint")
        if device.startswith("cuda") and not torch.cuda.is_available():
            raise RepairError("cuda was asked for and is not available")
        self.device = device
        self.size = size
        self.batch = max(1, batch)
        state = torch.load(self.path, map_location="cpu", weights_only=False)
        self.classes = [str(name) for name in state["classes"]]
        self.temperature = float(state.get("temperature",
                                           state.get("metrics", {}).get("temperature", 1.0)))
        config = state["config"]
        import timm

        model = timm.create_model(config["model"]["architecture"], pretrained=False,
                                  num_classes=len(self.classes))
        model.load_state_dict(state["model"])
        model.eval()
        self._torch = torch
        self._model = model.to(device)
        del state, model
        torch.cuda.empty_cache() if device.startswith("cuda") else None
        self.used = 0

    def probabilities_many(self, crops: Iterable[Any]) -> Any:
        """The class probabilities of crops, as an (n, classes) float64 array."""
        import numpy as np

        from .classify import crop_array

        images = list(crops)
        if not images:
            return np.zeros((0, len(self.classes)), dtype=np.float64)
        torch = self._torch
        rows = []
        for start in range(0, len(images), self.batch):
            block = images[start:start + self.batch]
            array = np.concatenate([crop_array(crop, size=self.size) for crop in block], axis=0)
            tensor = torch.from_numpy(array).to(self.device)
            with torch.inference_mode():
                logits = self._model(tensor)
                values = torch.softmax(logits / self.temperature, dim=-1)
            rows.append(values.float().cpu().numpy().astype(np.float64))
            self.used += len(block)
            del tensor, logits, values
        return np.concatenate(rows, axis=0)

    def score_set(self, crop: Any, code_points: Iterable[str]) -> float:
        """The probability that the crop holds one of `code_points`, `other` standing in for the
        code points the classifier was not trained on. The same rule as `classify.Classifier`."""

        wanted = set(code_points)
        values = self.probabilities_many([crop])[0]
        index = {name: position for position, name in enumerate(self.classes)}
        total = 0.0
        unknown = False
        for name in wanted:
            position = index.get(name)
            if position is None:
                unknown = True
            else:
                total += float(values[position])
        if unknown and "other" in index:
            total += float(values[index["other"]])
        return total

    def describe(self) -> dict[str, Any]:
        import torch

        return {"backend": "torch", "checkpoint": public_path(self.path),
                "sha256": file_evidence(self.path).get("sha256"),
                "classes": len(self.classes), "temperature": self.temperature,
                "device": self.device, "batch": self.batch,
                "device_name": torch.cuda.get_device_name(0) if self.device.startswith("cuda") else None,
                "peak_vram_gb": (torch.cuda.max_memory_allocated() / 1e9
                                 if self.device.startswith("cuda") else None)}


def classifier_for(run: Run, *, backend: str = "onnx", checkpoint: Path | str | None = None,
                   device: str = "cuda", batch: int = 256, root: Path | None = None) -> Any:
    """The classifier a pass scores with: the run's ONNX export, or its checkpoint on a device."""
    if backend == "onnx":
        from .classify import Classifier

        path = Path(run.classifier)
        return Classifier(path if path.exists() else _primary(root, path))
    if backend == "torch":
        path = Path(checkpoint) if checkpoint is not None else \
            _primary(root, Path("models/classifier/artifacts/best.pt"))
        return TorchClassifier(path, device=device, batch=batch)
    raise RepairError(f"unknown classifier backend {backend!r}; expected onnx or torch")


def _primary(root: Path | None, path: Path) -> Path:
    """A model path as this checkout has it, falling back to the primary checkout's artifacts.

    The model files are gitignored and a worktree therefore starts without them, while the same
    artifacts are what every run of the pipeline has used. The fallback is read-only and the file it
    lands on is hashed into the evidence either way.
    """
    if path.exists():
        return path
    if root is not None and (Path(root) / path).exists():
        return Path(root) / path
    return path


def parity(onnx_classifier: Any, torch_classifier: TorchClassifier, crops: Sequence[Any],
           *, tolerance: float = 1e-2) -> dict[str, Any]:
    """How far the checkpoint on the device is from the ONNX export the pipeline serves.

    The export's own tolerance is 1e-4 on the probabilities; a different device and a different
    arithmetic reach a slightly different answer, so what matters for a repair is that the two agree
    on which crop is which character. Both are reported, and a parity worse than `tolerance` is an
    error rather than a footnote: a repair scored by a different model is not a repair of this run.
    """
    import numpy as np

    if not crops:
        return {"crops": 0, "checked": False}
    left = np.asarray(onnx_classifier.probabilities_many(crops))
    right = np.asarray(torch_classifier.probabilities_many(crops))
    difference = float(np.abs(left - right).max())
    agreement = int((left.argmax(axis=1) == right.argmax(axis=1)).sum())
    result = {"crops": len(crops), "checked": True, "max_difference": difference,
              "top1_agreement": agreement, "top1_share": agreement / len(crops),
              "tolerance": tolerance}
    if difference > tolerance:
        raise RepairError(f"the checkpoint and the ONNX export disagree by {difference:.2e} on "
                          f"{len(crops)} crops, over the {tolerance:.0e} this pass allows")
    return result
