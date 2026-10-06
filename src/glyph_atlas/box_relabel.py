"""Relabel units aligned in the old detection order, each id staying on its box.

Until 2026-09-24 the aligner walked a vertical line's detections sorted by `(-x, y)`. A line's
detections sit within a few pixels of one x, so that order is close to random down the column, and
the monotone alignment handed the line's characters to its boxes in that random order:
`hl:C667…_49_010` reads 高八百四拾三石五斗四升 and its unit 6, labelled 拾, holds the crop of 斗.
`align.reading_order` fixed the aligner, but a dataset written before the fix keeps the old
pairing, and the run fingerprint did not change with the order until 2026-09-26, so the stale units
carry the same ids a rerun writes.

A published unit id names a box: reviews, round marks and skips were made against that crop. The
repair therefore keeps every id on its box and asks the current aligner which character the box
holds. Each stale line is aligned again over the detections its units were cut from, in reading
order, and every old unit takes the label of the new unit on the same box. Where the new alignment
skips k characters between two it placed and k old boxes lie between them, those boxes take those
characters in order (`gap_fills`). A box still without a character is `unplaced` and loses its
label, since the one it carried came from the scrambled pairing.

The classifier has to read the crops: without the page image every match costs the probability
floor and the alignment places by position alone, so a page whose image is not cached is left as
it is.

A page is realigned as a whole (`align.align_page`): until 2026-10-06 a line took every detection
whose centre lay inside its box, and a Honkoku-Lines box is wide enough to hold most of each
neighbouring column, so about 1,700 published boxes stood under two lines with two labels. Each
detection now belongs to one line, and the box of a unit whose line lost it is unplaced.

`descents` and `shared_lines` are the tests for a stale line. In a line aligned in reading order the
boxes advance down the column with the units' sequence; in one aligned in the old order about half of
the steps go back up. A line that holds a box another line of its page also holds was aligned before
detections were given to one line.

A person's review decides whether a unit keeps its label (`verdicts_of`). A unit a reviewer
confirmed, or whose character a person wrote, keeps it. A review that only said the label is wrong
does not: the label it rejected came from the scrambled pairing, so the unit takes the realigned
label like any other and keeps its review history in `meta["box_relabel"]`. A relabelled or unplaced
unit in a cluster whose form a person decided takes the realigned label when it is of the decided
form's family, and otherwise keeps its label and is marked for review, its crop kept.
"""

from __future__ import annotations

import json
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Collection, Iterable, Mapping, Sequence
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any

from . import align, line_assignment, refs
from .schema import Box, Line, ReviewState, Unit

METHOD = "box-relabel-v1"
#: A line whose boxes step back up the column on more than this share of its steps was aligned in
#: the old order. Over the 14,000 lines of the Honkoku-Lines pilot, lines aligned in reading order
#: step back on at most 15 percent of their steps in 99 percent of cases, and lines aligned in the
#: old order on more than 15 percent in 90 percent of cases.
STALE_SHARE = 0.25
#: A line needs this many boxed units before its order says anything.
MIN_UNITS = 3
#: Review states that say a person confirmed or corrected a unit; one in them keeps its label unless a
#: review of it says otherwise. A disputed unit is one a reviewer called wrong without naming the
#: character, and it takes the realigned label.
CONFIRMED = frozenset({ReviewState.REVIEWED, ReviewState.DOUBLE_REVIEWED, ReviewState.ADJUDICATED})
#: What a person's latest review of a unit said: the label is right, the character is another one the
#: person wrote, or the label is wrong.
MATCH, CORRECTION, WRONG = "match", "correction", "wrong"
#: Why a box is unplaced when it left its line for another line's record.
LEFT_LINE = frozenset({"other-line", "duplicate-line", "held"})
#: The verdicts that keep a unit's label.
HOLDING = frozenset({MATCH, CORRECTION})
#: Kinds that name no written character: a gap of unknown length and an unreadable character.
NO_TEXT = frozenset({"gap", "unreadable"})
#: Two units hold one detection when this share of the smaller box lies inside the other.
SHARED_SHARE = 0.5


def box_key(box: Box | None) -> tuple[int, int, int, int] | None:
    return None if box is None else (box.x, box.y, box.w, box.h)


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip().startswith("{"):
        try:
            parsed = json.loads(value)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def verdict_of(event: Mapping[str, Any]) -> str | None:
    """What one review event says about its unit: `MATCH`, `CORRECTION`, `WRONG`, or None.

    `event` is a row of a review store's journal or of the site's event export: a review (`field`
    `review`) carries its verdict and any character the reviewer wrote in its evidence, and a person's
    edit of another field of the unit is a correction. A pass of the pipeline (`role` `model`), a
    field that records no decision (`review.store.STATELESS`, a crop shown and left alone among
    them), an `unsure` answer and a round of the visual quiz that let a crop pass decide nothing.
    The review state an event sets stands in for its verdict only when its evidence names none.
    """
    from .review.store import STATELESS

    if (event.get("target_type", "unit") != "unit" or event.get("field") in STATELESS
            or event.get("role") == "model"):
        return None
    if event.get("field") != "review":
        return CORRECTION
    evidence = _mapping(event.get("evidence"))
    request = _mapping(evidence.get("request"))
    answer = _mapping(evidence.get("answer"))
    verdict = evidence.get("verdict") or request.get("verdict") or answer.get("verdict")
    character = (evidence.get("suggested_character") or request.get("character") or answer.get("character")
                 or evidence.get("character"))
    if verdict == "wrong":
        return CORRECTION if character else WRONG
    if verdict == "match":
        return None if evidence.get("kind") == "visual-quiz" else MATCH
    if verdict is not None:
        return None
    state = event.get("new")
    if isinstance(state, str) and state.startswith('"'):
        # The review store keeps a value JSON-encoded.
        state = json.loads(state)
    if state in {str(review) for review in CONFIRMED}:
        return MATCH
    if state == str(ReviewState.DISPUTED):
        return WRONG
    return None


def _when(event: Mapping[str, Any]) -> datetime:
    at = event.get("at")
    if not at:
        return datetime.min.replace(tzinfo=UTC)
    when = datetime.fromisoformat(str(at))
    return when if when.tzinfo else when.replace(tzinfo=UTC)


def verdicts_of(events: Iterable[Mapping[str, Any]]) -> dict[str, str]:
    """Each reviewed unit's latest verdict (`verdict_of`), by unit id.

    A row of the site's export holds the event as JSON under `event`, and one marked `undone` was
    withdrawn or rejected and says nothing. The events are taken in the order they were made: an
    event the store imported from the site appears in both under one id and counts once, and events
    without a time keep their place before the timed ones.
    """
    found: dict[str, Mapping[str, Any]] = {}
    for position, row in enumerate(events):
        if row.get("undone"):
            continue
        event = _mapping(row.get("event")) or dict(row)
        event = {**event, "target_id": event.get("target_id") or row.get("target"),
                 "at": event.get("at") or row.get("at")}
        found.setdefault(event.get("id") or f"#{position}", event)
    verdicts: dict[str, str] = {}
    for event in sorted(found.values(), key=_when):
        verdict = verdict_of(event)
        if event["target_id"] and verdict is not None:
            verdicts[event["target_id"]] = verdict
    return verdicts


def read_reviews(path: Path) -> list[dict[str, Any]]:
    """Review events from a JSON list, JSON lines, or a D1 query's output (`[{"results": [...]}]`)."""
    text = Path(path).read_text(encoding="utf-8").strip()
    if not text.startswith("["):
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    rows = json.loads(text)
    if rows and isinstance(rows[0], dict) and "results" in rows[0]:
        return [row for part in rows for row in part["results"]]
    return rows


def holds(unit: Unit, verdicts: Mapping[str, str]) -> bool:
    """Whether a person's review keeps `unit`'s label: its latest verdict, or else its review state."""
    verdict = verdicts.get(unit.id)
    if verdict is not None:
        return verdict in HOLDING
    return unit.review in CONFIRMED


def family(text: str | None) -> str | None:
    """The grapheme family's code point of one character, or of a code point written `U+XXXX`."""
    from .corpus.identity import family_of

    if not text:
        return None
    code_points = text.split() if text.startswith("U+") else refs.to_code_points(unicodedata.normalize("NFC", text))
    found = family_of(" ".join(code_points))
    return found["code_point"] if found else " ".join(code_points)


def descents(placed: Sequence[tuple[int, Box]], vertical: bool | None = None) -> tuple[int, int]:
    """How many steps of a line's boxes, taken in sequence order, go back against reading order, out
    of how many steps there are. `placed` holds each boxed unit's (seq, box).

    A horizontal line is read left to right, which is the order the aligner has always walked one
    in. `vertical` is the line's own flag; without it, boxes that spread wider than they run tall
    and stand one to a column are taken for a horizontal line.
    """
    from . import ainu

    ordered = sorted(placed, key=lambda item: item[0])
    if len(ordered) < 2:
        return 0, 0
    detections = [align.Detection(box=box, score=1.0) for _, box in ordered]
    if vertical is None:
        xs = [detection.centre[0] for detection in detections]
        ys = [detection.centre[1] for detection in detections]
        columns = ainu.columns_of([detection.box for detection in detections])
        vertical = not (max(xs) - min(xs) > max(ys) - min(ys) and len(columns) == len(detections))
    if vertical:
        reading = align.reading_order(detections)
    else:
        reading = sorted(detections, key=lambda detection: detection.centre)
    rank = {id(detection): index for index, detection in enumerate(reading)}
    ranks = [rank[id(detection)] for detection in detections]
    return sum(1 for a, b in pairwise(ranks) if b < a), len(ranks) - 1


def stale(placed: Sequence[tuple[int, Box]], share: float = STALE_SHARE, vertical: bool | None = None) -> bool:
    """Whether a line's boxed units, as (seq, box), were aligned in the old detection order."""
    back, steps = descents(placed, vertical)
    return steps + 1 >= MIN_UNITS and back > share * steps


def shared_lines(placed: Iterable[tuple[str | None, str, Box]]) -> set[str]:
    """The lines that hold a box another line of their page holds, from each placed unit's
    (page id, line id, box).

    A detection belongs to one line (`align.assign_page`); two lines holding it carry two labels for
    one crop. A box counts as the other's when `SHARED_SHARE` of the smaller lies inside the larger,
    so a box formed as the union of two detections is caught as well as an identical one.
    """
    by_page: dict[str | None, list[tuple[str, Box]]] = defaultdict(list)
    for page, line, box in placed:
        by_page[page].append((line, box))
    found: set[str] = set()
    for held in by_page.values():
        held.sort(key=lambda item: item[1].x)
        for index, (line, box) in enumerate(held):
            for other, there in held[index + 1:]:
                if there.x >= box.x + box.w:
                    break
                if other == line:
                    continue
                width = min(box.x + box.w, there.x + there.w) - max(box.x, there.x)
                height = min(box.y + box.h, there.y + there.h) - max(box.y, there.y)
                if width > 0 and height > 0 and width * height >= SHARED_SHARE * min(box.w * box.h,
                                                                                    there.w * there.h):
                    found.update((line, other))
    return found


def placed_of(units: Iterable[Unit]) -> list[tuple[int, Box]]:
    """The (seq, box) of every boxed unit with a place in its line."""
    return [(unit.seq, unit.box) for unit in units if unit.box is not None and unit.seq is not None]


def stale_lines(units: Iterable[Unit], share: float = STALE_SHARE,
                vertical: Mapping[str, bool] | None = None) -> set[str]:
    """The ids of the lines whose detect-align units were aligned in the old order, or that hold a box
    another line of their page holds; `vertical` gives each line's orientation where it is known."""
    by_line: dict[str, list[Unit]] = defaultdict(list)
    for unit in units:
        if unit.line_id and unit.method == "detect-align":
            by_line[unit.line_id].append(unit)
    known = vertical or {}
    crossed = shared_lines((unit.page_id, line, unit.box) for line, found in by_line.items() for unit in found
                           if unit.box is not None and unit.seq is not None)
    return crossed | {line for line, found in by_line.items() if stale(placed_of(found), share, known.get(line))}


def label_of(unit: Unit) -> str | None:
    """The character a unit is labelled with: its transcription, in NFC."""
    return unicodedata.normalize("NFC", unit.text_source) if unit.text_source else None


def one_character(unit: Unit) -> bool:
    """Whether a unit names one written character: a letter, a repeat mark, a ligature, a mark."""
    return str(unit.kind) not in NO_TEXT and len((label_of(unit) or "").strip()) == 1


def placements(new: Sequence[Unit]) -> dict[tuple[int, int, int, int], Unit]:
    """The new units that name one character on a box of their own, by box.

    A box shared by two units (a merge), a box that holds part of a character (a member of a split)
    and a unit of more than one character name no single character for the box.
    """
    holders = Counter(box_key(unit.box) for unit in new if unit.box is not None)
    return {box_key(unit.box): unit for unit in new
            if unit.box is not None and holders[box_key(unit.box)] == 1 and one_character(unit)
            and unit.granularity == "char" and unit.group_id is None}


def reading_ranks(boxes: Iterable[tuple[int, int, int, int]]) -> dict[tuple[int, int, int, int], int]:
    """Each box's position in the reading order of a vertical line."""
    detections = [align.Detection(box=Box(x=x, y=y, w=w, h=h), score=1.0) for x, y, w, h in set(boxes)]
    return {box_key(detection.box): rank for rank, detection in enumerate(align.reading_order(detections))}


def gap_fills(old: Sequence[Unit], new: Sequence[Unit], placed: dict[tuple[int, int, int, int], Unit]
              ) -> dict[tuple[int, int, int, int], Unit]:
    """Characters the new alignment skipped, put on the old boxes it left between their neighbours.

    Between two placed characters, or before the first or after the last, the alignment may skip k
    written characters while k old boxes lie there unplaced, in reading order. The line then says
    what those boxes hold, one character each in order, and they are filled. A run whose count of
    boxes and characters differ, or that holds a box the alignment used otherwise, is left unplaced,
    and so is every box of a line the alignment placed nothing on: there the count alone would decide.
    """
    if not placed:
        return {}
    ranks = reading_ranks([box_key(unit.box) for unit in old if unit.box is not None] + list(placed))
    free = sorted({(ranks[box_key(unit.box)], box_key(unit.box)) for unit in old
                   if unit.box is not None and box_key(unit.box) not in placed})
    chosen = {id(unit) for unit in placed.values()}
    ordered = sorted((unit for unit in new if unit.seq is not None and not (unit.upstream or {}).get("role")),
                     key=lambda unit: unit.seq)
    fills: dict[tuple[int, int, int, int], Unit] = {}
    run: list[Unit] = []
    low, blocked = -1, False
    for unit in [*ordered, None]:
        if unit is not None and id(unit) not in chosen:
            if unit.box is None and one_character(unit):
                run.append(unit)
            elif unit.box is not None or str(unit.kind) in NO_TEXT or (label_of(unit) or "").strip():
                # A box the alignment used for something other than one character (a split, a merge),
                # a gap of unknown length or a token of several characters leaves the count in doubt;
                # a space holds no ink and changes nothing.
                blocked = True
            continue
        high = ranks[box_key(unit.box)] if unit is not None else len(ranks)
        between = [box for rank, box in free if low < rank < high]
        if run and not blocked and len(between) == len(run):
            fills.update(zip(between, run, strict=True))
        run, low, blocked = [], high, False
    return fills


def fillable(old: Sequence[Unit], owned: Collection[tuple[int, int, int, int]] | None) -> list[Unit]:
    """The old units whose boxes a gap fill may use: all of them, or those on a box in `owned`."""
    return list(old) if owned is None else [unit for unit in old if box_key(unit.box) in owned]


def unplaced_reason(box: tuple[int, int, int, int], new: Sequence[Unit],
                    elsewhere: Collection[tuple[int, int, int, int]] = (), duplicate: bool = False) -> str:
    """Why the new alignment names no single character for `box`: `elsewhere` holds the boxes the
    page's other lines took, and `duplicate` says the line repeats another record of its page."""
    holders = [unit for unit in new if box_key(unit.box) == box]
    if not holders:
        if duplicate:
            return "duplicate-line"
        return "other-line" if box in elsewhere else "no-unit"
    if len(holders) > 1:
        return "shared"
    unit = holders[0]
    if unit.group_id is not None or unit.granularity != "char":
        return "split"
    if str(unit.kind) in NO_TEXT or not (label_of(unit) or "").strip():
        return "not-a-character"
    return "several-characters"


def relabel(old: Sequence[Unit], new: Sequence[Unit], verdicts: Mapping[str, str] | None = None,
            forms: Mapping[str, str] | None = None, *,
            elsewhere: Collection[tuple[int, int, int, int]] = (), duplicate: bool = False,
            owned: Collection[tuple[int, int, int, int]] | None = None) -> list[dict[str, Any]]:
    """One record per boxed old unit of a line: the label the new alignment gives its box.

    `status` is `unchanged` when the label stands, `relabelled` when the box holds another character
    of the line, `unplaced` when the new alignment names no single character for the box
    (`placements`, `gap_fills`), and `protected` when a person's review keeps the label (`holds`,
    over `verdicts`). A label that comes from a gap fill is marked `fill: gap`. `elsewhere` and
    `duplicate` say why a box left the line (`unplaced_reason`). `owned` holds the boxes the page's
    assignment gave the line; only those are gap-filled, since a box another line holds, or that no
    line took, is not one of the line's characters.

    `forms` names, by unit id, the form a person decided for the unit's cluster, as a character or a
    family's code point. Such a unit takes a new label of that form's family; one the realignment
    gives another character, or none, is `review`: it keeps its label and goes to a reviewer, who
    sees the realigned label beside it.
    """
    verdicts, forms = verdicts or {}, forms or {}
    placed = placements(new)
    fills = gap_fills(fillable(old, owned), new, placed)
    placed = {**placed, **fills}
    records = []
    for unit in old:
        if unit.box is None:
            continue
        found = placed.get(box_key(unit.box))
        record = {"unit_id": unit.id, "line_id": unit.line_id, "box": box_key(unit.box), "before": label_of(unit),
                  "after": label_of(found) if found else None, "seq": found.seq if found else None,
                  "review": str(found.review) if found else None}
        if unit.id in verdicts:
            record["verdict"] = verdicts[unit.id]
        if holds(unit, verdicts):
            record["status"] = "protected"
        elif found is None:
            record["status"] = "unplaced"
            record["reason"] = unplaced_reason(record["box"], new, elsewhere, duplicate)
        elif record["after"] == record["before"]:
            record["status"] = "unchanged"
        else:
            record["status"] = "relabelled"
        decided = forms.get(unit.id)
        # A box that left the line (`unplaced_reason`) is another record's crop: keeping this unit's
        # label there would put two labels on it.
        if (decided and record["status"] in ("relabelled", "unplaced")
                and record.get("reason") not in LEFT_LINE
                and (record["after"] is None or family(record["after"]) != family(decided))):
            record["status"] = "review"
            record["form"] = decided
        if found is not None and record["box"] in fills:
            record["fill"] = "gap"
        records.append(record)
    return records


def realign(lines: Sequence[Line], detections: dict[str, list[Box]], *, run: align.Run, classifier: Any,
            crop_of: Any) -> dict[str, list[Unit]]:
    """The current aligner's units of each line, its page aligned as a whole over the detections cached
    for it, so that each detection goes to one line."""
    by_page: dict[str | None, list[Line]] = defaultdict(list)
    for line in lines:
        by_page[line.page_id].append(line)
    out: dict[str, list[Unit]] = {}
    for page, found in by_page.items():
        boxes = [align.Detection(box=box, score=1.0) for box in detections.get(page, [])]
        for line_id, (units, _) in align.align_page(found, boxes, run=run, classifier=classifier,
                                                    crop_of=crop_of).items():
            out[line_id] = units
    return out


def applied(unit: Unit, record: dict[str, Any]) -> Unit:
    """`unit` as the record leaves it: the new label and place in the line, or no label when unplaced.

    An unchanged or protected unit takes only its place in the reading order, so the line no longer
    reads as stale; an unplaced one has no place in it and leaves any group it was cut into.
    A unit sent to review keeps its label and its place, and carries the realigned label for the
    reviewer. The id and the box stay. The evidence goes into `meta["box_relabel"]`, with the label
    before and, for a unit a person reviewed, the review state and verdict it had.
    """
    status = record["status"]
    if status in ("unchanged", "protected"):
        # A protected unit keeps its label, and takes its place in the reading order like the rest, so
        # its line is not read as stale; a box the new alignment leaves empty has no place in it.
        return unit.model_copy(update={"seq": record["seq"]})
    if status not in ("relabelled", "unplaced", "review"):
        return unit
    note = {"method": METHOD, "status": status, "before": record["before"], "after": record["after"]}
    if record.get("verdict"):
        note["review"] = {"state": str(unit.review), "verdict": record["verdict"]}
    if status == "review":
        # The unit keeps its label, and its place in the line goes with the box: none when the
        # realignment leaves the box empty.
        note["form"] = record["form"]
        return unit.model_copy(update={"seq": record["seq"], "meta": {**(unit.meta or {}), "box_relabel": note}})
    meta = {**(unit.meta or {}), "box_relabel": note}
    if status == "unplaced":
        return unit.model_copy(update={"seq": None, "text_source": None, "unicode": None,
                                       "candidates": [], "group_id": None, "granularity": "char",
                                       "review": ReviewState.REJECTED, "meta": meta})
    return unit.model_copy(update={"meta": meta, **record["fields"]})


def fields_of(unit: Unit) -> dict[str, Any]:
    """What a relabelled unit takes from the new unit on its box."""
    return {key: getattr(unit, key) for key in ("seq", "kind", "granularity", "group_id", "text_source",
                                                "unicode", "classification", "script", "candidates", "confidence",
                                                "review")}


def aligned_lines(units: Iterable[Unit]) -> set[str]:
    """The ids of the lines that hold boxed detect-align units."""
    return {unit.line_id for unit in units
            if unit.line_id and unit.method == "detect-align" and unit.box is not None}


def repair(old_units: Sequence[Unit], lines: Sequence[Line], detections: dict[str, list[Box]], *, run: align.Run,
           classifier: Any, crop_of: Any, verdicts: Mapping[str, str] | None = None,
           forms: Mapping[str, str] | None = None, every: bool = False) -> tuple[list[Unit], list[dict[str, Any]]]:
    """Relabel the units of every page in `lines` that holds a stale line, or with `every` of every
    page holding an aligned line, and return all of `old_units` with the records.

    A page is realigned as a whole, every line of it in `lines` competing for its detections, and every
    vertical line of it holding detect-align units is relabelled: a line that is not stale itself can
    still hold a box the realignment gives a neighbour. A horizontal line keeps its labels and loses
    only the boxes another line took. `lines` should therefore hold every line of
    the pages, those without units included, since their ink is theirs.

    `every` is for a dataset known to be aligned in the old order throughout: a line whose boxes
    happen to run down the column in sequence passes the stale test, and its labels may still sit
    one box off where the old order swapped two neighbours.
    """
    by_line: dict[str, list[Unit]] = defaultdict(list)
    for unit in old_units:
        # Only the aligner's own units are its to relabel: a unit another method placed on the line
        # (an import cut from a record's own text) keeps what it says.
        if unit.line_id and unit.method == "detect-align":
            by_line[unit.line_id].append(unit)
    wanted = aligned_lines(old_units) if every else stale_lines(old_units, vertical={line.id: line.vertical
                                                                                    for line in lines})
    pages = sorted({line.page_id for line in lines if line.id in wanted and line.vertical}, key=str)
    records: list[dict[str, Any]] = []
    changed: dict[str, Unit] = {}
    by_id = {unit.id: unit for units in by_line.values() for unit in units}
    for page in pages:
        page_records: list[dict[str, Any]] = []
        page_lines = [line for line in lines if line.page_id == page]
        new_by_line = realign(page_lines, detections, run=run, classifier=classifier, crop_of=crop_of)
        duplicates = line_assignment.duplicates_of(page_lines)
        page_boxes = [align.Detection(box=box, score=1.0) for box in detections.get(page, [])]
        owned = {line_id: {box_key(found.box) for found in held}
                 for line_id, held in align.assign_page(page_lines, page_boxes).items()}
        for line in page_lines:
            if line.id not in by_line:
                continue
            elsewhere = set().union(*(boxes for line_id, boxes in owned.items() if line_id != line.id))
            if not line.vertical:
                # A horizontal line was read left to right all along; it only gives up the boxes the
                # page's assignment gave to another line.
                for unit in by_line[line.id]:
                    if box_key(unit.box) in elsewhere:
                        record = {"unit_id": unit.id, "line_id": line.id, "box": box_key(unit.box),
                                  "before": label_of(unit), "after": None, "seq": None, "review": None,
                                  "status": "unplaced", "reason": "other-line"}
                        page_records.append(record)
                continue
            new = new_by_line[line.id]
            placed = placements(new)
            placed.update(gap_fills(fillable(by_line[line.id], owned[line.id]), new, placed))
            own = {unit.id: unit for unit in by_line[line.id]}
            for record in relabel(by_line[line.id], new, verdicts, forms, elsewhere=elsewhere,
                                  duplicate=line.id in duplicates, owned=owned[line.id]):
                unit = own[record["unit_id"]]
                if record["status"] == "relabelled":
                    record["fields"] = fields_of(placed[record["box"]])
                page_records.append(record)
        # A unit whose label a person's review holds keeps it even on a box the page gave another
        # line; that line's unit on the box then yields it, so one crop carries one label. A unit
        # sent to review yields the same way: the crop's label belongs to the record the box went to.
        held = {record["box"]: record["line_id"] for record in page_records
                if record["status"] == "protected" and record["box"] not in owned.get(record["line_id"], set())}
        for record in page_records:
            if (record["box"] in held and held[record["box"]] != record["line_id"]
                    and record["status"] in ("relabelled", "unchanged", "review")):
                record.update(status="unplaced", reason="held", after=None, seq=None)
                record.pop("fields", None)
                record.pop("form", None)
            changed[record["unit_id"]] = applied(by_id[record["unit_id"]], record)
            record.pop("fields", None)
            records.append(record)
        align.clear_crop_cache()
    return [changed.get(unit.id, unit) for unit in old_units], records


def counts(records: Iterable[dict[str, Any]]) -> dict[str, int]:
    return dict(Counter(record["status"] for record in records))


def write_records(path: Path, records: Iterable[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            written += 1
    return written


def relabel_directory(directory: Path, out: Path, *, run: align.Run, classifier: Any, detections: Path,
                      reviews: Iterable[Mapping[str, Any]] = (), forms: Mapping[str, str] | None = None,
                      every: bool = False) -> dict[str, int]:
    """Relabel the pages of `directory` that hold a stale line, or with `every` all its aligned lines,
    and write the result to `out`, never to `directory`.

    `out` gets the source's other tables, `units.parquet`, every unit of the source with the stale
    lines relabelled, and `relabels.jsonl`, one record per boxed unit of a relabelled line.
    `detections` is the cache of the boxes the units were cut from, keyed by page. `reviews` are review
    events made elsewhere, such as the site's, read after the journal of the review store beside the
    source; together they decide which units keep their labels (`verdicts_of`). `forms` names the
    forms people decided (`relabel`). A horizontal line is not relabelled: the old order read one left
    to right already.
    """
    import shutil

    from . import ainu, images, tables
    from . import repair as alignment_repair

    directory, out = Path(directory), Path(out)
    if out.resolve() == directory.resolve():
        raise ValueError("the relabel writes a derived dataset, never its source")
    dataset = tables.Dataset(directory)
    units = tables.read(directory / "units.parquet", Unit)
    human = alignment_repair.human_state(directory)
    candidates = aligned_lines(units)
    # Every line of a page competes for its detections, a line no unit was cut from included.
    found = [line for batch in dataset.scan("lines", keep=tables.In("page_id", {unit.page_id for unit in units
                                                                               if unit.line_id in candidates}))
             for line in batch]
    orientation = {line.id: line.vertical for line in found if line.id in candidates}
    wanted = candidates if every else stale_lines(units, vertical=orientation)
    horizontal = {line for line in wanted if orientation.get(line) is False}
    pages = {line.page_id for line in found if line.id in wanted and line.vertical and line.box is not None}
    page_records = {page.id: page for batch in dataset.scan("pages", keep=tables.In("id", pages)) for page in batch}
    # Without its page image the classifier scores every crop at the floor and the alignment places by
    # position alone, which is the guess this repair exists to replace: such a line is left as it is.
    unread = {page for page, record in page_records.items() if images.path_for(record.image) is None}
    found_boxes = ainu.read_detections(Path(detections))
    # A page the cache holds no detections for would be aligned against nothing.
    undetected = {page for page in page_records if not found_boxes.get(page)}
    pages -= unread | undetected
    lines = sorted((line for line in found if line.page_id in pages and line.box is not None),
                   key=lambda line: (line.page_id, line.seq if line.seq is not None else -1, line.id))
    verdicts = verdicts_of([*human.rows, *reviews])
    repaired, records = repair(units, lines, found_boxes, run=run, classifier=classifier,
                               crop_of=align._crop_reader(dataset, page_records),
                               verdicts=verdicts, forms=forms, every=every)
    out.mkdir(parents=True, exist_ok=True)
    for name in alignment_repair.COPIED_TABLES:
        path = dataset.tables.get(name)
        if path is None:
            continue
        target = out / Path(path).name
        if Path(path).is_dir():
            shutil.rmtree(target, ignore_errors=True)
            shutil.copytree(path, target)
        else:
            shutil.copy2(path, target)
    tables.write(out / "units.parquet", repaired, Unit)
    write_records(out / "relabels.jsonl", records)
    relabelled_lines = {line.id for line in lines if line.vertical and line.id in candidates}
    return {"units": len(units), "chosen_lines": len(wanted), "pages": len(pages), "lines": len(relabelled_lines),
            "horizontal_lines": len(horizontal),
            "pages_without_image": len(unread),
            "pages_without_detections": len(undetected), "reviewed": len(verdicts),
            **{f"verdict_{name}": count for name, count in sorted(Counter(verdicts.values()).items())},
            **counts(records)}
