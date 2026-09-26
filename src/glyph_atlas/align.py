"""Align a transcription line to the character boxes detected on its page.

The line comes from the transcription, the boxes from the detector. The two are aligned by dynamic
programming over the tokens of the line and the detections inside its container: a token may take one
detection (a match), two adjacent detections (a split), or none; two tokens may share one detection
(a merge) when a cut through the detected ink scores well; and a detection may be skipped when the
transcription has no token for it. The cost of a match is the classifier's negative log probability
of the token's code points over the detection's crop, so the alignment leans on the same evidence the
reviewer will see.

Every decision is recorded on the unit it produces: `confidence.detection` from the detector,
`confidence.text` from the match, and `confidence.segmentation` from how the box was formed. A unit
whose best path is not clearly better than the second best is kept with `review=rejected` rather than
being called accepted, because a confident-looking box on the wrong ink is the failure this pipeline
is most exposed to.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, Field

from . import koji, refs
from .schema import (
    Box,
    Candidate,
    Classification,
    Confidence,
    Group,
    Line,
    ReviewState,
    Script,
    Unit,
    UnitKind,
)

KANA = Script.HIRAGANA
FLAG_SCRIPTS = {Script.HIRAGANA, Script.HENTAIGANA, Script.KATAKANA}
MARK_KINDS = {UnitKind.ITERATION_MARK, UnitKind.VOICING_MARK, UnitKind.PUNCTUATION, UnitKind.LIGATURE}
# A match costs at least this much, so that a classifier that answers zero for everything cannot make
# an impossible alignment free.
PROBABILITY_FLOOR = 1e-4


class Detector(Protocol):
    """What the alignment needs from the detector: boxes and scores on a page image."""

    def boxes(self, image: str | Path) -> Sequence[tuple[Box, float]]: ...


class Classifier(Protocol):
    """What the alignment needs from the character classifier."""

    def score_set(self, crop: Any, code_points: set[str]) -> float: ...


@dataclass
class Detection:
    """One detected box with its score, in page coordinates."""

    box: Box
    score: float
    image: str | Path | None = None

    @property
    def centre(self) -> tuple[float, float]:
        return (self.box.x + self.box.w / 2, self.box.y + self.box.h / 2)


class Token(BaseModel):
    """One unit of transcription the alignment can place: a character, a mark or a gap."""

    text: str
    start: int
    end: int
    reading: str | None = None
    unicode: str | None = None
    code_points: set[str] = Field(default_factory=set)
    kind: UnitKind = UnitKind.CHAR
    script: Script = Script.UNKNOWN
    role: str = "main"
    column: int | None = None


@dataclass
class Container:
    """A run of tokens that share one geometric region of the line, with its detections."""

    tokens: list[Token]
    detections: list[Detection]
    column: int | None = None


@dataclass
class Decision:
    """One transition of the alignment, kept so the second-best path can be told from the best."""

    kind: str
    tokens: tuple[int, ...]
    detections: tuple[int, ...]
    cost: float
    probability: float = 1.0


@dataclass
class Placement:
    """The result for one token: its boxes and how they were formed."""

    token: Token
    detections: list[Detection] = field(default_factory=list)
    segmentation: float = 1.0
    text_probability: float = 1.0
    kind: str = "match"
    accepted: bool = True
    group: bool = False


def check_classifier(run: Run) -> None:
    """Refuse a classifier export other than the one the run names by hash."""
    if run.classifier_sha256 is None:
        return
    with Path(run.classifier).open("rb") as handle:
        found = hashlib.file_digest(handle, "sha256").hexdigest()
    if found != run.classifier_sha256:
        raise ValueError(f"{run.classifier} is not the classifier run {run.name} was measured with "
                         f"({run.classifier_version}, sha256 {run.classifier_sha256[:12]}); "
                         "restore that export or define a new run")


class Run(BaseModel):
    """A named alignment configuration; its hash names the units it writes."""

    name: str
    detector: str = "models/detector/artifacts/detector.onnx"
    classifier: str = "models/classifier/artifacts/classifier.onnx"
    detector_version: str = "unset"
    classifier_version: str = "unset"
    #: The sha256 of the classifier export the run was measured with. The path names wherever the
    #: served model lives, so the run refuses a different file there rather than writing its scores
    #: under this run's unit ids. It checks the file and is not part of the hash.
    classifier_sha256: str | None = None
    policy: str = "align-v1"
    weights: dict[str, float] = Field(
        default_factory=lambda: {
            "skip-token": 8.0,
            "skip-detection": 6.0,
            "split": 12.0,
            "merge": 14.0,
            "gap": 3.0,
        }
    )
    accept: float = 0.9
    margin: float = 1.0
    ruby: bool = False
    #: The detector's score cutoff. It belongs to the run rather than to the detector's constructor
    #: default, because the run's units are only comparable when every stage detects at one operating
    #: point: the pilot's boxes were all found at 0.02, and an aligner that silently built its
    #: detector at the library default of 0.3 found nothing on pages where every detection sits near
    #: the cut, which is what happened to the Ainu records' cursive columns.
    score: float | None = None
    #: The IoU above which the detector's cross-tile suppression drops a duplicate. Part of the run
    #: for the same reason `score` is: it decides which boxes the alignment ever sees.
    nms: float | None = None
    #: The share of the page's typical ink a detection must hold (`detect.INK`). Unset, the run's
    #: detector keeps every box, as every run did before the gate existed, and the run's hash is the
    #: one it always had; set, it is part of the hash, because it decides which boxes the alignment sees.
    ink: float | None = None

    def fingerprint(self) -> str:
        """The id every unit of this run carries, from the configuration that decides the output.

        `name` is a label and `score` is the detector's operating point: the score was written down
        after the pilot's units were already measured, and folding it into the hash now would rewrite
        the ids of every unit in `work/honkoku-lines` for a value that was always 0.02 in practice.
        A run that changes the operating point changes the file it names, and the score is recorded
        beside the units it produced.
        """
        payload = self.model_dump(mode="json")
        # Geometry and whitespace handling change placements even with the same models.
        # Keep their new crops distinct from the pilot's ids and saved review evidence.
        payload["algorithm"] = "reading-order-ink-tokens-v2"
        payload.pop("name", None)
        payload.pop("score", None)
        payload.pop("classifier_sha256", None)
        if payload.get("ink") is None:
            payload.pop("ink", None)
        return hashlib.sha1(
            json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()[:12]


def load_run(path: Path, name: str | None = None) -> Run:
    """Read a run configuration from `models/align/runs/<name>.yaml`."""
    import yaml

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw.setdefault("name", name or path.stem)
    return Run.model_validate(raw)


def tokens_of(line: Line, policy: str = "align-v1") -> list[Token]:
    """The tokens of a line in reading order, with the code points each may stand for."""
    parsed = koji.parse(line.text_raw)
    tokens: list[Token] = []
    columns: dict[int, int] = {}
    for char in parsed.chars:
        if char.role in {"ruby", "ruby-left", "note", "okurigana", "kaeriten", "cancelled", "inserted"}:
            continue
        column = None
        for element in char.path:
            column = columns.get(element)
            if column is not None:
                break
        token = _token_of(char.text, char.start, char.end, char.role, column, policy)
        if tokens and tokens[-1].kind is UnitKind.VOICING_MARK and tokens[-1].end == char.start:
            # A combining mark belongs to the character it follows.
            base = tokens[-1]
            base.text += char.text
            base.end = char.end
            base.code_points |= token.code_points
            base.reading = (base.reading or "") + char.text
            continue
        tokens.append(token)
    return tokens


def _token_of(text: str, start: int, end: int, role: str, column: int | None, policy: str) -> Token:
    if role == "gap":
        return Token(text="", start=start, end=end, kind=UnitKind.GAP, role=role)
    if role == "unreadable":
        return Token(text=text, start=start, end=end, kind=UnitKind.UNREADABLE, role=role)
    code_points = refs.to_code_points(text)
    ordinary = code_points[0] if code_points else None
    candidates = refs.candidates(text) if len(text) == 1 else []
    if candidates:
        code_points = set(candidates)
    script = _script_of(text, candidates)
    kind = _kind_of(text, script)
    return Token(
        text=text,
        start=start,
        end=end,
        reading=text,
        unicode=ordinary,
        code_points=code_points,
        kind=kind,
        script=script,
        role=role,
        column=column,
    )


def _script_of(text: str, candidates: list[str]) -> Script:
    """The script of a token, from the character layer.

    The layer is what knows: a transcription may hold a character this project never saw, such as
    the alternate katakana of Unicode 18.0, and a range list written here would not. A token that
    is a kana with more than one candidate code point is a hentaigana whatever the layer says about
    the character transcribed, because the alternatives are what the reading leaves open.
    """
    if len(text) != 1:
        return Script.UNKNOWN
    if candidates and refs.script_of(text) not in (Script.KATAKANA, Script.HIRAGANA):
        return Script.HENTAIGANA
    return refs.script_of(text)


def _kind_of(text: str, script: Script) -> UnitKind:
    point = ord(text) if len(text) == 1 else None
    if point is not None and point in (0x3005, 0x3031, 0x3032, 0x309D, 0x309E, 0x30FD, 0x30FE):
        return UnitKind.ITERATION_MARK
    if point is not None and point in (0x3099, 0x309A, 0x309B, 0x309C):
        return UnitKind.VOICING_MARK
    if point is not None and point in (0x309F, 0x30FF):
        return UnitKind.LIGATURE
    if point is not None and (0x3001 <= point <= 0x3003 or point in (0x30FB, 0xFF0C, 0xFF0E)):
        return UnitKind.PUNCTUATION
    if text in {"ゟ", "ヿ", "𬼂"}:
        return UnitKind.LIGATURE
    if script is Script.UNKNOWN and len(text) > 1:
        return UnitKind.LIGATURE
    return UnitKind.CHAR


def containers_of(line: Line, detections: Sequence[Detection], policy: str = "align-v1") -> list[Container]:
    """Group the tokens and the detections into the regions of the line they share.

    A 割書 line splits into columns by the token's column and the detection's x; a line without one is
    a single container. A detection whose centre falls outside the line box and outside every column
    is not part of the line.

    A vertical line's detections are walked in `reading_order`, because the alignment is monotone in
    the order it is given and that is the order the line is read in. A horizontal line keeps the
    detector's own sequence sorted left to right.
    """
    tokens = tokens_of(line, policy)
    inside = [
        detection
        for detection in detections
        if line.box is None or _inside(line.box, detection.centre)
    ]
    columns = sorted({token.column for token in tokens if token.column is not None})
    if not columns:
        found = _line_order(line, inside)
        return [Container(tokens=tokens, detections=found)]
    groups: list[Container] = []
    for column in columns:
        column_tokens = [token for token in tokens if token.column == column]
        groups.append(Container(tokens=column_tokens, detections=[], column=column))
    loose = [token for token in tokens if token.column is None]
    if loose:
        groups.append(Container(tokens=loose, detections=[]))
    for detection in inside:
        target = min(groups, key=lambda group: abs(_column_x(group, tokens) - detection.centre[0]))
        target.detections.append(detection)
    for group in groups:
        group.detections = _line_order(line, group.detections)
    return [group for group in groups if group.tokens or group.detections]


def _line_order(line: Line, detections: Sequence[Detection]) -> list[Detection]:
    """One line's detections in the order the line is read."""
    if line.vertical:
        return reading_order(detections)
    return sorted(detections, key=lambda detection: (detection.centre[0], detection.centre[1]))


def reading_order(detections: Sequence[Detection]) -> list[Detection]:
    """A vertical line's detections in the order the line is read: columns right to left, down each.

    The aligner matches the tokens of a transcription to a line's detections with a monotone dynamic
    program, so both sequences are walked forward together and the order the detections arrive in
    decides which character gets which box. Sorting on the raw float x centre fails on these
    manuscripts: most of a line's detections sit within a few pixels of the same x, so the y key
    almost never decides anything and the detector's own output is in no order at all. Measured over
    the 759 derived lines that hold four or more in-box detections, the detections advance down the
    column for 5 of them under the x sort and for 724 under this order, and a unit labelled 手 held
    the crop of を until this became the order the aligner walks in.

    The rule is the derivation's own: `ainu.columns_of` groups detections into columns by a gap
    measured against the page's median character width, and a column is read top to bottom. The
    grouping is what keeps the order safe — a detection cannot move into the neighbouring column,
    only within its own from one y to the next.
    """
    from . import ainu

    if len(detections) < 2:
        return list(detections)
    columns = ainu.columns_of([detection.box for detection in detections])
    return [
        detections[index]
        for column in columns
        for index in sorted(column, key=lambda position: detections[position].centre[1])
    ]


def _column_x(group: Container, tokens: Sequence[Token]) -> float:
    if group.column is None:
        return float("inf")
    return float(group.column)


def _inside(box: Box, point: tuple[float, float]) -> bool:
    return box.x <= point[0] <= box.x + box.w and box.y <= point[1] <= box.y + box.h


def align_line(
    line: Line,
    detections: Sequence[Detection],
    *,
    run: Run,
    classifier: Classifier | None = None,
    crop_of: Any = None,
) -> tuple[list[Unit], list[Group]]:
    """Align one line and return its units and groups.

    `crop_of` turns a detection into the crop the classifier scores, and must accept
    `(page_id, box)`; when it is None every match costs the probability floor, which is what a run
    without a classifier does.
    """
    fingerprint = run.fingerprint()
    units: list[Unit] = []
    groups: list[Group] = []
    sequence = 0
    for container in containers_of(line, detections, run.policy):
        placements = _align_container(container, run, classifier, crop_of, line)
        for placement in placements:
            if placement.group:
                group_id = f"{line.id}:{fingerprint}:g{len(groups)}"
                member_ids = []
                for detection in placement.detections:
                    sequence += 1
                    member = _unit_of(line, placement.token, [detection], run, fingerprint, sequence,
                                      placement, granularity="sequence")
                    member.group_id = group_id
                    units.append(member)
                    member_ids.append(member.id)
                groups.append(
                    Group(id=group_id, page_id=line.page_id, box=_union([d.box for d in placement.detections]),
                          unit_ids=member_ids)
                )
                continue
            sequence += 1
            units.append(_unit_of(line, placement.token, placement.detections, run, fingerprint, sequence,
                                  placement))
    # 振り仮名 is a reading of its base rather than text of the line, so it is not aligned to a
    # detection; it is still a record of the page and a reviewer has to be able to see and correct it.
    for index, token in enumerate(ruby_tokens(line), start=1):
        units.append(
            Unit(
                id=f"{line.id}:{fingerprint}:r{index}",
                document_id=None,
                page_id=line.page_id,
                line_id=line.id,
                seq=sequence + index,
                box=None,
                kind=UnitKind.CHAR,
                text_source=token.text,
                reading=token.text,
                unicode=token.unicode,
                classification=Classification.UNASSESSED,
                script=token.script,
                method="detect-align",
                review=ReviewState.MACHINE,
                upstream={"source": "detect-align", "run": run.name, "role": token.role},
            )
        )
    return units, groups


def ruby_tokens(line: Line) -> list[Token]:
    """The 振り仮名 of a line, in reading order, as tokens with no box.

    `tokens_of` leaves ruby out because an alignment cannot place it on a detection; the record still
    needs it, so the caller asks for it separately and the unit ids carry an `r` where an aligned unit
    carries its sequence number.
    """
    parsed = koji.parse(line.text_raw)
    return [
        _token_of(char.text, char.start, char.end, char.role, None, "align-v1")
        for char in parsed.chars
        if char.role in {"ruby", "ruby-left"}
    ]


def _unit_of(
    line: Line,
    token: Token,
    detections: Sequence[Detection],
    run: Run,
    fingerprint: str,
    sequence: int,
    placement: Placement,
    granularity: str = "char",
) -> Unit:
    box = _union([detection.box for detection in detections]) if detections else None
    unicode = token.unicode
    classification = Classification.UNASSESSED
    candidates: list[Candidate] = []
    if token.script is not Script.HIRAGANA and token.code_points:
        if len(token.code_points) == 1:
            unicode = next(iter(token.code_points))
            classification = Classification.IDENTIFIED
        else:
            classification = Classification.AMBIGUOUS
    if token.script is Script.HIRAGANA and token.unicode:
        classification = Classification.UNASSESSED
    if token.script is Script.UNKNOWN and len(token.text) == 1:
        classification = Classification.IDENTIFIED
    if token.kind in MARK_KINDS and token.unicode:
        classification = Classification.IDENTIFIED
    if token.code_points:
        candidates = [
            Candidate(unicode=point, p=1.0 / len(token.code_points)) for point in sorted(token.code_points)
        ]
    confidence = Confidence(
        detection=max((detection.score for detection in detections), default=None),
        segmentation=placement.segmentation if detections else None,
        text=placement.text_probability if detections else None,
        model=run.classifier_version,
    )
    return Unit(
        id=f"{line.id}:{fingerprint}:{sequence}",
        document_id=None,
        page_id=line.page_id,
        line_id=line.id,
        seq=sequence,
        box=box,
        kind=token.kind,
        granularity=granularity,  # type: ignore[arg-type]
        text_source=token.text or None,
        reading=token.reading,
        unicode=unicode,
        classification=classification,
        script=token.script,
        candidates=candidates,
        method="detect-align",
        confidence=confidence,
        review=ReviewState.MACHINE if placement.accepted else ReviewState.REJECTED,
        upstream={"source": "detect-align", "run": run.name},
    )


def _union(boxes: Sequence[Box]) -> Box:
    x = min(box.x for box in boxes)
    y = min(box.y for box in boxes)
    right = max(box.x + box.w for box in boxes)
    bottom = max(box.y + box.h for box in boxes)
    return Box(x=x, y=y, w=right - x, h=bottom - y)


def _align_container(
    container: Container,
    run: Run,
    classifier: Classifier | None,
    crop_of: Any,
    line: Line,
) -> list[Placement]:
    """Align one container and return its placements in token order."""
    tokens = container.tokens
    detections = container.detections
    n, m = len(tokens), len(detections)
    if n == 0:
        return []
    if m == 0:
        return _placements(
            tokens,
            detections,
            [Decision("skip-token", (index,), (), 0.0) for index in range(n)],
            run,
            0.0,
        )

    costs = _match_costs(tokens, detections, classifier, crop_of, line)
    weights = run.weights
    # The table holds two costs and two back pointers per state rather than the paths themselves.
    # Carrying the path through every state built a new tuple at every transition and kept the two
    # cheapest paths at each of them; a profile of two pilot pages put that at a billion function
    # calls for a matrix of a few dozen detections by a handful of tokens. The two cheapest paths are
    # still what decides the acceptance margin, so both are kept: the second is the best cost plus the
    # best continuation through a move other than the first one's.
    infinity = float("inf")
    table: list[list[tuple]] = [[((infinity, None), (infinity, None))] * (m + 1) for _ in range(n + 1)]
    table[0][0] = ((0.0, None), (infinity, None))
    for i in range(n + 1):
        for j in range(m + 1):
            (best_cost, _), _ = table[i][j]
            if best_cost == infinity:
                continue
            for move in _moves(i, j, n, m, tokens, costs, weights):
                _offer(table, move.next_i, move.next_j, best_cost + move.step, i, j, move)
    (total, _), (runner_up, _) = table[n][m]
    margin = runner_up - total if runner_up != infinity else infinity
    decisions = _walk(table, n, m)
    return _placements(tokens, detections, decisions, run, margin)


class _Move:
    """One transition: what it consumes, what it costs and where it lands."""

    __slots__ = ("detections", "kind", "next_i", "next_j", "step", "tokens")

    def __init__(self, kind: str, tokens: tuple[int, ...], detections: tuple[int, ...], step: float,
                 next_i: int, next_j: int) -> None:
        self.kind = kind
        self.tokens = tokens
        self.detections = detections
        self.step = step
        self.next_i = next_i
        self.next_j = next_j

    def decision(self) -> Decision:
        penalty = 0.0
        if self.kind in ("split", "merge"):
            penalty = 0.0
        return Decision(self.kind, self.tokens, self.detections, self.step,
                        _probability(self.step - penalty))


def _moves(
    i: int,
    j: int,
    n: int,
    m: int,
    tokens: Sequence[Token],
    costs: list[list[float]],
    weights: dict[str, float],
) -> list[_Move]:
    """Every transition out of one state.

    A state counts the placements produced and the cells consumed, so every transition moves both
    counters forward: these are alignments of two sequences, not sequences against each other. A
    placement carrying two tokens consumes two token cells, one carrying two detections consumes two
    detection cells.
    """
    moves: list[_Move] = []
    if i < n and tokens[i].text.isspace():
        # Transcription layout occupies no ink. Retain its token, without letting a
        # match or a split consume the next character's detection.
        return [_Move("skip-token", (i,), (), 0.0, i + 1, j)]
    if i < n:
        moves.append(_Move("skip-token", (i,), (), weights["skip-token"], i + 1, j))
    if j < m:
        moves.append(_Move("skip-detection", (), (j,), weights["skip-detection"], i, j + 1))
    if i < n and j < m:
        match = costs[i][j]
        moves.append(_Move("match", (i,), (j,), match, i + 1, j + 1))
    if i + 1 < n and j < m and not tokens[i + 1].text.isspace():
        split = (costs[i][j] + costs[i + 1][j]) / 2 + weights["split"]
        moves.append(_Move("split", (i, i + 1), (j,), split, i + 2, j + 1))
    if i < n and j + 1 < m:
        merge = min(costs[i][j], costs[i][j + 1]) + weights["merge"]
        moves.append(_Move("merge", (i,), (j, j + 1), merge, i + 1, j + 2))
    if i < n and tokens[i].kind is UnitKind.GAP:
        moves.append(_Move("gap", (i,), (), weights["gap"], i + 1, j))
    return moves


def _offer(table: list[list[tuple]], i: int, j: int, cost: float, from_i: int, from_j: int,
           move: _Move) -> None:
    """Offer a path to a state: it becomes the state's best, its second best, or neither."""
    (best_cost, best_move), (second_cost, _) = table[i][j]
    if cost < best_cost:
        if best_move is not None:
            # The path that was best is now a candidate for second, and it is a different path
            # because it arrived through a different move.
            table[i][j] = ((cost, (from_i, from_j, move)), (best_cost, best_move))
            return
        table[i][j] = ((cost, (from_i, from_j, move)), (second_cost, None))
        return
    if best_move is not None and best_move[2].kind == move.kind and best_move[:2] == (from_i, from_j):
        return
    if cost < second_cost:
        table[i][j] = ((best_cost, best_move), (cost, (from_i, from_j, move)))


def _walk(table: list[list[tuple]], n: int, m: int) -> list[Decision]:
    """Reconstruct the cheapest path from the table, in the order the line reads.

    Each state stores where its two cheapest paths came from, so the walk never enumerates a path
    twice: from the end state it follows the back pointer of the best path, and when a step would
    enter a state the walk has already left it takes that state's second-best pointer instead, which
    is what keeps the walk finite.
    """
    decisions: list[Decision] = []
    visited: set[tuple[int, int]] = {(n, m)}
    i, j = n, m
    while (i, j) != (0, 0):
        (_, best), (_, second) = table[i][j]
        step = best
        if step is None:
            break
        if (step[0], step[1]) in visited and second is not None:
            step = second
        from_i, from_j, move = step
        decisions.append(move.decision())
        visited.add((from_i, from_j))
        i, j = from_i, from_j
    decisions.reverse()
    return decisions


def _probability(cost: float) -> float:
    import math

    return math.exp(-max(cost, 0.0))


# One page's crops, keyed by the detection's identity. A page is aligned line by line and every line
# is scored against every detection on it, so without this the same rectangle is cut and decoded once
# per line: a profile of two pilot pages put that at 72% of the run.
_CROP_CACHE: dict[tuple[int, str | None, int, int, int, int], Any] = {}


def _crop_once(crop_of: Any, page_id: str | None, detection: Detection) -> Any:
    """The crop of one detection, cut once for the run."""
    box = detection.box
    key = (id(detection), page_id, box.x, box.y, box.w, box.h)
    crop = _CROP_CACHE.get(key)
    if crop is None:
        crop = crop_of(page_id, box)
        if len(_CROP_CACHE) > 8192:
            _CROP_CACHE.clear()
        _CROP_CACHE[key] = crop
    return crop


def clear_crop_cache() -> None:
    """Forget the crops of a run; a long run over many pages calls this between documents."""
    _CROP_CACHE.clear()


def _match_costs(
    tokens: Sequence[Token],
    detections: Sequence[Detection],
    classifier: Classifier | None,
    crop_of: Any,
    line: Line,
) -> list[list[float]]:
    """The negative log probability of every token over every detection.

    The crops of the detections are cut once and scored in one batch per token set: a page holds
    hundreds of detections and a line a handful of tokens, so scoring one crop at a time re-reads the
    same rectangle for every token.
    """
    import math

    floor = -math.log(PROBABILITY_FLOOR)
    if classifier is None or crop_of is None or not detections:
        return [[floor] * len(detections) for _ in tokens]
    try:
        crops = [_crop_once(crop_of, line.page_id, detection) for detection in detections]
    except (OSError, ValueError):
        return [[floor] * len(detections) for _ in tokens]
    # One batch of crops holds the whole line's detections, and every token's cost is a sum over the
    # class probabilities it may stand for, so the model runs once per line rather than once per
    # (token, crop) pair. The profile that made this necessary: 8,855 model runs for 16 lines, 36 of
    # the page's 37 seconds.
    many = getattr(classifier, "probabilities_many", None)
    classes = getattr(classifier, "classes", None)
    if many is None or classes is None:
        return [
            [
                -math.log(max(float(classifier.score_set(crop, token.code_points)), PROBABILITY_FLOOR))
                for crop in crops
            ]
            for token in tokens
        ]
    try:
        probabilities = many(crops)
    except (OSError, ValueError, RuntimeError):
        return [[floor] * len(detections) for _ in tokens]
    index = {name: position for position, name in enumerate(classes)}
    return [_token_costs(probabilities, token, index, floor) for token in tokens]


def token_costs(probabilities: Any, token: Token, index: dict[str, int], floor: float) -> list[float]:
    """One token's cost against every crop, from a batch of class probabilities.

    The token's cost is the negative log of the probability mass its code points carry on that crop:
    a kana with several candidate forms asks for the sum of their probabilities, because the
    transcription leaves open which form was written. A token whose code points are all outside the
    class list costs the floor, since the classifier was not trained on it and has nothing to say.
    """
    return _token_costs(probabilities, token, index, floor)


def _token_costs(probabilities: Any, token: Token, index: dict[str, int], floor: float) -> list[float]:
    """One token's cost against every crop, from the class probabilities of the batch."""
    import math

    positions = [index[point] for point in token.code_points if point in index]
    if not positions:
        return [floor] * len(probabilities)
    mass = probabilities[:, positions].sum(axis=1)
    return [-math.log(max(float(value), PROBABILITY_FLOOR)) for value in mass]


def _placements(
    tokens: Sequence[Token],
    detections: Sequence[Detection],
    decisions: Iterable[Decision],
    run: Run,
    margin: float,
) -> list[Placement]:
    placements: list[Placement] = []
    for decision in decisions:
        if decision.kind == "skip-detection":
            continue
        if decision.kind == "skip-token":
            token = tokens[decision.tokens[0]]
            placements.append(
                Placement(token=token, detections=[], kind="skip", accepted=False,
                          segmentation=0.0, text_probability=0.0)
            )
            continue
        if decision.kind == "gap":
            token = tokens[decision.tokens[0]]
            placements.append(Placement(token=token, detections=[], kind="gap", accepted=True))
            continue
        boxes = [detections[index] for index in decision.detections]
        accepted = decision.probability >= run.accept and margin >= run.margin
        placements.append(
            Placement(
                token=tokens[decision.tokens[0]],
                detections=boxes,
                kind=decision.kind,
                segmentation=_segmentation(decision),
                text_probability=decision.probability,
                accepted=accepted,
            )
        )
    return placements


def _segmentation(decision: Decision) -> float:
    if decision.kind == "match":
        return decision.probability
    return max(0.0, min(1.0, decision.probability))


def run_directory(
    directory: Path,
    run: Run,
    *,
    document: str | None = None,
    pages: list[str] | None = None,
    limit: int | None = None,
    detector: Detector | None = None,
    classifier: Classifier | None = None,
    replace: bool = True,
) -> dict[str, int]:
    """Align every line of `directory` with boxes from the detector and write the units back.

    The page image is taken from the cache when it is there and fetched when it is not, so a run over
    a dataset whose pages were never downloaded still works. `units.parquet` is rewritten with the
    units of this run and the groups that go with them; units another run or a reviewer wrote are
    left where they are. Returns the counts.
    """
    from . import images, net, tables

    # A classifier handed in scores under this run's ids as much as the run's own would.
    handed = getattr(classifier, "onnx_path", None)
    if classifier is None or handed is not None:
        check_classifier(run if classifier is None else run.model_copy(update={"classifier": str(handed)}))
    dataset = tables.Dataset(directory)
    if dataset.tables["lines"] is None or dataset.tables["pages"] is None:
        raise ValueError(f"{directory} needs lines and pages to align")
    # `pages=[]` selects nothing and `pages=None` selects everything; `if pages` conflated them, so
    # an empty selection — which is what `--limit 0` produces — ran the whole dataset.
    wanted_pages = set(pages) if pages is not None else None
    if wanted_pages is not None and not wanted_pages:
        return {"pages": 0, "lines": 0, "units": 0, "groups": 0, "accepted": 0, "rejected": 0,
                "failed": 0}
    lines_by_page: dict[str, list[Line]] = {}
    # A run over a handful of pages should not validate a million lines: the filter is applied to the
    # raw rows, and the row groups whose statistics cannot hold one of the pages are skipped.
    keep_lines = tables.In("page_id", wanted_pages) if wanted_pages else None
    for batch in dataset.scan("lines", keep=keep_lines):
        for line in batch:
            if line.box is None:
                continue
            if wanted_pages is not None and line.page_id not in wanted_pages:
                continue
            lines_by_page.setdefault(line.page_id, []).append(line)
    page_records = {page.id: page for page in dataset.read("pages")}
    page_documents = {page_id: page.document_id for page_id, page in page_records.items()}
    if document is not None:
        lines_by_page = {
            page: found
            for page, found in lines_by_page.items()
            if page_records.get(page) is not None and page_records[page].document_id == document
        }
    if detector is None:
        from .detect import Detector as OnnxDetector

        detector = OnnxDetector(
            run.detector,
            **({"score": run.score} if run.score is not None else {}),
            **({"nms": run.nms} if run.nms is not None else {}),
            ink=run.ink if run.ink is not None else 0.0,
        )
    if classifier is None:
        from .classify import Classifier as OnnxClassifier

        classifier = OnnxClassifier(run.classifier)
    crop_of = _crop_reader(dataset, page_records)

    counts = {"pages": 0, "lines": 0, "units": 0, "groups": 0, "accepted": 0, "rejected": 0, "failed": 0}
    written: list[Unit] = []
    groups: list[Group] = []
    fingerprint = run.fingerprint()
    for index, (page_id, found) in enumerate(sorted(lines_by_page.items())):
        page = page_records.get(page_id)
        if page is None:
            counts["failed"] += len(found)
            continue
        path = images.path_for(page.image)
        if path is None:
            try:
                images.fetch(page.image)
            except (images.ImageError, net.DownloadError):
                counts["failed"] += len(found)
                continue
            path = images.path_for(page.image)
        if path is None:
            counts["failed"] += len(found)
            continue
        try:
            detections = [Detection(box=box, score=score) for box, score in detector.boxes(path)]
        except (OSError, ValueError, RuntimeError):  # a page the detector cannot read does not stop the run
            counts["failed"] += len(found)
            continue
        counts["pages"] += 1
        for line in found:
            units, page_groups = align_line(line, detections, run=run, classifier=classifier, crop_of=crop_of)
            written.extend(units)
            groups.extend(page_groups)
            counts["lines"] += 1
            counts["units"] += len(units)
            counts["groups"] += len(page_groups)
            counts["accepted"] += sum(1 for unit in units if unit.review is ReviewState.MACHINE)
            counts["rejected"] += sum(1 for unit in units if unit.review is ReviewState.REJECTED)
        # The crops of a page are dead once its lines are aligned, and a page can hold more
        # detections than the cache's ceiling, so the cache is cleared per page rather than left to
        # evict: leaving it cost a 20-page run 21 minutes against 22 seconds.
        clear_crop_cache()
        if limit is not None and counts["lines"] >= limit:
            break
    if written:
        _write_units(directory, written, groups, run, fingerprint, replace=replace,
                     pages={unit.page_id for unit in written if unit.page_id},
                     page_documents=page_documents)
    return counts


def _crop_reader(dataset: Any, pages: dict[str, Any]) -> Any:
    """A callable `(page_id, box) -> PIL image` that cuts from the cached page image.

    The page is decoded once and held while its lines are aligned. Opening the file per crop is what
    the first version did, and on a 9 MB, 4,288x2,848 scan the decode of the same image hundreds of
    times is the difference between a page taking about a second and a page taking tens of seconds.
    """
    from PIL import Image

    from . import images

    state: dict[str, Any] = {"page_id": None, "image": None}

    def read(page_id: str | None, box: Box) -> Any:
        page = pages.get(page_id or "")
        if page is None:
            raise ValueError(f"{page_id}: no such page")
        if state["page_id"] != page_id or state["image"] is None:
            path = images.path_for(page.image)
            if path is None:
                raise ValueError(f"{page_id}: the page image is not cached")
            with Image.open(path) as handle:
                handle.load()
                state["image"] = handle.copy()
            state["page_id"] = page_id
        image = state["image"]
        return image.crop((box.x, box.y, box.x + box.w, box.y + box.h)).convert("RGB")

    return read


KEEP_REVIEW = {
    ReviewState.REVIEWED,
    ReviewState.DOUBLE_REVIEWED,
    ReviewState.ADJUDICATED,
    ReviewState.DISPUTED,
}


def _write_units(
    directory: Path,
    units: list[Unit],
    groups: list[Group],
    run: Run,
    fingerprint: str,
    *,
    replace: bool = True,
    pages: set[str] | None = None,
    page_documents: dict[str, str] | None = None,
) -> None:
    """Write this run's units and groups, keeping what must survive.

    Three rules fix what survives. A unit of another run stays, so two configurations can be compared
    on one directory. A unit a person has reviewed or adjudicated stays whatever run wrote it,
    because an alignment is not allowed to throw away editorial work. And with `replace`, this run's
    own units are dropped only on the pages it is about to write, so a run over one group of pages
    adds to the pages outside the group rather than clearing the directory — which is what a
    held-out run over 452 pages did to the calibration pages aligned before it.
    """
    from . import tables

    # The read and the write are one step: two alignments that ran at once over this directory each
    # read the units, each wrote its own back, and the later write threw the earlier run's units
    # away. The lock is held across both, so the second run merges with what the first left.
    with tables.locked(directory):
        dataset = tables.Dataset(directory)
        marker = f":{fingerprint}:"
        keep_units: list[Unit] = []
        if dataset.tables["units"] is not None:
            for batch in dataset.scan("units"):
                for unit in batch:
                    if _keep_unit(unit, marker, replace=replace, pages=pages):
                        keep_units.append(unit)
        keep_groups: list[Group] = []
        if dataset.tables["groups"] is not None:
            for batch in dataset.scan("groups"):
                for group in batch:
                    if marker not in group.id:
                        keep_groups.append(group)
        for unit in units:
            if unit.document_id is None and page_documents is not None:
                unit.document_id = page_documents.get(unit.page_id or "")
            elif unit.document_id is None:
                unit.document_id = _document_of(dataset, unit.page_id)
        tables._write_unlocked(directory / "units.parquet", [*keep_units, *units], Unit)
        if keep_groups or groups:
            tables._write_unlocked(directory / "groups.parquet", [*keep_groups, *groups], Group)


def _keep_unit(unit: Unit, marker: str, *, replace: bool, pages: set[str] | None) -> bool:
    """Whether a unit already in the directory survives the write that is about to happen.

    A unit written by another run (its id does not carry this run's fingerprint) is never this run's
    to drop. A unit a person touched is never dropped by a machine. What is left is this run's own
    output, and with `replace` it goes when its page is one of the pages being written — the pages it
    is not writing keep it, so a group-by-group run accumulates.
    """
    if marker not in unit.id:
        return True
    if unit.review in KEEP_REVIEW:
        return True
    if not replace or pages is None:
        return not replace
    return unit.page_id not in pages


def _document_of(dataset: Any, page_id: str | None) -> str | None:
    if page_id is None or dataset.tables["pages"] is None:
        return None
    for page in dataset.read("pages"):
        if page.id == page_id:
            return page.document_id
    return None
