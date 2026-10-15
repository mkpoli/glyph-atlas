"""Cut the glyphs of ko.wikisource's transcribed scans, labelled from each page's own transcription.

A transcribed 諺解 page reads as one stream: every hanja is followed by its small hangul reading in
the same ruled column, and the 언해 runs between them are hangul with hanja and readings mixed in.
The wiki page holds that stream in reading order. The detector's boxes are read in the same order,
columns right to left and each top to bottom, and the two sequences are aligned by dynamic
programming: a token may take one box, a token may go without a box (the detector missed it), and
a box may go without a token (a page header, a stain, a mark the transcription leaves out).

The cost of a match is the negative log probability of the token under its own model: the atlas
classifier for hanja and the hangul classifier, by printed shape, for hangul. A token neither model
has a class for costs a fixed amount and is judged by its neighbours. A match becomes a unit only
when its model ranks the token among its five best readings of the box, or, for a token no model
knows, when the matches on both sides of it agree and nothing was skipped between them. A page whose
judged matches agree less than `MIN_AGREEMENT` of the time is left out whole.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

import numpy as np

from . import clusters, eonhae
from .schema import Box

#: Share of a page's judged matches that must agree before any of its units is kept.
MIN_AGREEMENT = 0.70
#: Agreeing matches a page needs, so that two lucky boxes do not carry a page.
MIN_AGREEING = 3
#: Cost of a token left without a box, and of a box left without a token. A match is taken over
#: skipping both when its probability is above exp(-2 * SKIP).
SKIP = 3.0
#: Cost of matching a token no model has a class for.
UNMODELLED = 2.5
#: Lowest probability a match is scored at, so that one confident miss does not wreck a path.
FLOOR = 1e-4
#: A box starts a new column when its centre lies further than this many median box widths from the
#: column's centre.
COLUMN_GAP = 0.6

Move = Literal["match", "skip_token", "skip_box"]


@dataclass(frozen=True)
class Step:
    """One move of the alignment: token `token` (or None) against box `box` (or None)."""

    move: Move
    token: int | None
    box: int | None


@dataclass(frozen=True)
class Match:
    """A token aligned to a box, with its model's verdict: True, False, or None for no model."""

    token: int
    box: int
    verdict: bool | None
    top5: tuple[str, ...]
    model: Literal["atlas", "hangul", "none"]


def page_tokens(text: str) -> list[str]:
    """The printed characters of a page's wikitext, in reading order, with old hangul clusters."""
    return [char for char in eonhae.characters(text) if eonhae.names_a_character(char)]


def reading_order(boxes: Sequence[Box]) -> list[list[int]]:
    """Box indices grouped into columns, right to left, each top to bottom."""
    if not boxes:
        return []
    width = float(np.median([box.w for box in boxes]))
    columns: list[list[int]] = []
    centres: list[float] = []
    for index in sorted(range(len(boxes)), key=lambda i: -(boxes[i].x + boxes[i].w / 2)):
        centre = boxes[index].x + boxes[index].w / 2
        if centres and abs(centres[-1] - centre) <= COLUMN_GAP * width:
            columns[-1].append(index)
            members = columns[-1]
            centres[-1] = sum(boxes[i].x + boxes[i].w / 2 for i in members) / len(members)
        else:
            columns.append([index])
            centres.append(centre)
    return [sorted(column, key=lambda i: boxes[i].y) for column in columns]


def align(costs: np.ndarray) -> list[Step]:
    """The cheapest monotone path through `costs[token, box]`, with `SKIP` for every unmatched side."""
    n, m = costs.shape
    table = np.full((n + 1, m + 1), np.inf)
    back = np.zeros((n + 1, m + 1), dtype=np.int8)
    table[0, :] = SKIP * np.arange(m + 1)
    table[:, 0] = SKIP * np.arange(n + 1)
    back[0, 1:] = 2
    back[1:, 0] = 1
    for i in range(1, n + 1):
        row = costs[i - 1]
        for j in range(1, m + 1):
            match = table[i - 1, j - 1] + row[j - 1]
            skip_token = table[i - 1, j] + SKIP
            skip_box = table[i, j - 1] + SKIP
            if match <= skip_token and match <= skip_box:
                table[i, j], back[i, j] = match, 0
            elif skip_token <= skip_box:
                table[i, j], back[i, j] = skip_token, 1
            else:
                table[i, j], back[i, j] = skip_box, 2
    steps: list[Step] = []
    i, j = n, m
    while i or j:
        move = back[i, j]
        if move == 0:
            steps.append(Step("match", i - 1, j - 1))
            i, j = i - 1, j - 1
        elif move == 1:
            steps.append(Step("skip_token", i - 1, None))
            i -= 1
        else:
            steps.append(Step("skip_box", None, j - 1))
            j -= 1
    return steps[::-1]


class Models:
    """The two classifiers' readings of every box, and what each says of a token."""

    def __init__(
        self,
        atlas: np.ndarray,
        atlas_classes: Sequence[str],
        hangul: np.ndarray,
        hangul_index: eonhae.HangulShapeIndex,
        hangul_classes: Sequence[str],
    ) -> None:
        self.atlas = np.asarray(atlas, dtype=np.float64)
        self.atlas_classes = list(atlas_classes)
        self.atlas_column = {name: i for i, name in enumerate(self.atlas_classes)}
        self.known = set(self.atlas_classes)
        self.hangul = np.asarray(hangul, dtype=np.float64)
        self.hangul_index = hangul_index
        self.hangul_classes = list(hangul_classes)

    def model_of(self, token: str) -> Literal["atlas", "hangul", "none"]:
        script = eonhae.script_of(token)
        if script == "hangul" and clusters.shape_key(token) in self.hangul_index.key_indices:
            return "hangul"
        if script == "han" and eonhae.codepoints(token) in self.atlas_column:
            return "atlas"
        return "none"

    def probabilities(self, token: str, boxes: Sequence[int]) -> np.ndarray | None:
        """The probability of `token` on each of `boxes` under its own model, or None for no model."""
        model = self.model_of(token)
        rows = list(boxes)
        if model == "hangul":
            columns = list(self.hangul_index.key_indices[clusters.shape_key(token)])
            return self.hangul[np.ix_(rows, columns)].sum(axis=1)
        if model == "atlas":
            return self.atlas[rows, self.atlas_column[eonhae.codepoints(token)]]
        return None

    def costs(self, tokens: Sequence[str], order: Sequence[int]) -> np.ndarray:
        """`costs[t, k]`: the cost of token `t` taking the `k`-th box in reading order."""
        out = np.full((len(tokens), len(order)), UNMODELLED)
        for t, token in enumerate(tokens):
            found = self.probabilities(token, order) if len(order) else None
            if found is not None:
                out[t] = -np.log(np.maximum(found, FLOOR))
        return out

    def verdict(self, token: str, box: int) -> tuple[bool | None, tuple[str, ...], str]:
        model = self.model_of(token)
        if model == "hangul":
            row = self.hangul[box]
            agrees = clusters.shape_key(token) in eonhae.open_top_keys(row, self.hangul_index)
            return agrees, eonhae.top_class_labels(row, self.hangul_classes), model
        top5 = eonhae.top_class_labels(self.atlas[box], self.atlas_classes)
        if model == "atlas":
            return bool(eonhae.classifier_agrees(token, top5, self.known)), top5, model
        return None, top5, model


def matches(steps: Sequence[Step], tokens: Sequence[str], order: Sequence[int], models: Models) -> list[Match]:
    """Every match of the path, with the box index in the page's own numbering."""
    out: list[Match] = []
    for step in steps:
        if step.move != "match":
            continue
        box = order[step.box]
        verdict, top5, model = models.verdict(tokens[step.token], box)
        out.append(Match(step.token, box, verdict, top5, model))
    return out


def page_agreement(found: Sequence[Match]) -> tuple[float | None, int]:
    """Share of judged matches that agree, and how many agree."""
    judged = [m.verdict for m in found if m.verdict is not None]
    agreeing = sum(judged)
    return (agreeing / len(judged) if judged else None), agreeing


def page_accepted(found: Sequence[Match]) -> bool:
    agreement, agreeing = page_agreement(found)
    return agreement is not None and agreement >= MIN_AGREEMENT and agreeing >= MIN_AGREEING


def emitted(steps: Sequence[Step], found: Sequence[Match]) -> list[Match]:
    """The matches that become units: agreeing ones, and unjudged ones held by agreeing neighbours
    with no skip between them."""
    by_token = {m.token: m for m in found}
    path = [by_token.get(step.token) if step.move == "match" else None for step in steps]
    out: list[Match] = []
    for position, match in enumerate(path):
        if match is None or match.verdict is False:
            continue
        if match.verdict is True:
            out.append(match)
            continue
        before = path[position - 1] if position > 0 else None
        after = path[position + 1] if position + 1 < len(path) else None
        if before is not None and after is not None and before.verdict is True and after.verdict is True:
            out.append(match)
    return out


def unit_id(page_id: str, box: Box) -> str:
    """Stable unit id from the page and the page-space box."""
    return "ks:" + hashlib.sha1(f"{page_id}|{eonhae.box_key(box)}".encode()).hexdigest()[:20]


def line_id(page_id: str, column: int) -> str:
    return "ks:" + hashlib.sha1(f"{page_id}|column|{column}".encode()).hexdigest()[:20]
