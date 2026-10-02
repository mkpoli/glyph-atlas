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

`descents` is the test for a stale line. In a line aligned in reading order the boxes advance down
the column with the units' sequence; in one aligned in the old order about half of the steps go back
up. A unit a person reviewed, or one named in `protect`, is never relabelled.
"""

from __future__ import annotations

import json
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Collection, Iterable, Sequence
from itertools import pairwise
from pathlib import Path
from typing import Any

from . import align
from .schema import Box, Line, ReviewState, Unit

METHOD = "box-relabel-v1"
#: A line whose boxes step back up the column on more than this share of its steps was aligned in
#: the old order. Over the 14,000 lines of the Honkoku-Lines pilot, lines aligned in reading order
#: step back on at most 15 percent of their steps in 99 percent of cases, and lines aligned in the
#: old order on more than 15 percent in 90 percent of cases.
STALE_SHARE = 0.25
#: A line needs this many boxed units before its order says anything.
MIN_UNITS = 3
#: Review states a person set; a unit in one of them keeps its label.
HUMAN = frozenset({ReviewState.REVIEWED, ReviewState.DOUBLE_REVIEWED, ReviewState.ADJUDICATED,
                   ReviewState.DISPUTED})


def box_key(box: Box | None) -> tuple[int, int, int, int] | None:
    return None if box is None else (box.x, box.y, box.w, box.h)


def descents(placed: Sequence[tuple[int, Box]]) -> tuple[int, int]:
    """How many steps of a line's boxes, taken in sequence order, go back against reading order, out
    of how many steps there are. `placed` holds each boxed unit's (seq, box).

    Boxes that spread wider than they run tall are a horizontal line, read left to right, which is
    the order the aligner has always walked one in.
    """
    ordered = sorted(placed, key=lambda item: item[0])
    if len(ordered) < 2:
        return 0, 0
    detections = [align.Detection(box=box, score=1.0) for _, box in ordered]
    xs = [detection.centre[0] for detection in detections]
    ys = [detection.centre[1] for detection in detections]
    if max(xs) - min(xs) > max(ys) - min(ys):
        reading = sorted(detections, key=lambda detection: detection.centre)
    else:
        reading = align.reading_order(detections)
    rank = {id(detection): index for index, detection in enumerate(reading)}
    ranks = [rank[id(detection)] for detection in detections]
    return sum(1 for a, b in pairwise(ranks) if b < a), len(ranks) - 1


def stale(placed: Sequence[tuple[int, Box]], share: float = STALE_SHARE) -> bool:
    """Whether a vertical line's boxed units, as (seq, box), were aligned in the old detection order."""
    back, steps = descents(placed)
    return steps + 1 >= MIN_UNITS and back > share * steps


def placed_of(units: Iterable[Unit]) -> list[tuple[int, Box]]:
    """The (seq, box) of every boxed character unit."""
    return [(unit.seq, unit.box) for unit in units
            if unit.box is not None and unit.seq is not None and str(unit.kind) == "char"]


def stale_lines(units: Iterable[Unit], share: float = STALE_SHARE) -> set[str]:
    """The ids of the lines whose detect-align units were aligned in the old order."""
    by_line: dict[str, list[Unit]] = defaultdict(list)
    for unit in units:
        if unit.line_id and unit.method == "detect-align":
            by_line[unit.line_id].append(unit)
    return {line for line, found in by_line.items() if stale(placed_of(found), share)}


def label_of(unit: Unit) -> str | None:
    """The character a unit is labelled with: its transcription, in NFC."""
    return unicodedata.normalize("NFC", unit.text_source) if unit.text_source else None


def placements(new: Sequence[Unit]) -> dict[tuple[int, int, int, int], Unit]:
    """The new units that name one character on a box of their own, by box.

    A box shared by two units (a merge), a box that holds part of a character (a member of a split)
    and a unit of more than one character name no single character for the box.
    """
    holders = Counter(box_key(unit.box) for unit in new if unit.box is not None)
    return {box_key(unit.box): unit for unit in new
            if unit.box is not None and holders[box_key(unit.box)] == 1 and str(unit.kind) == "char"
            and unit.granularity == "char" and unit.group_id is None and len(label_of(unit) or "") == 1}


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
    boxes and characters differ, or that holds a box the alignment used otherwise, is left unplaced.
    """
    ranks = reading_ranks([box_key(unit.box) for unit in old if unit.box is not None] + list(placed))
    free = sorted((ranks[box_key(unit.box)], box_key(unit.box)) for unit in old
                  if unit.box is not None and box_key(unit.box) not in placed)
    chosen = {id(unit) for unit in placed.values()}
    ordered = sorted((unit for unit in new if unit.seq is not None and not (unit.upstream or {}).get("role")),
                     key=lambda unit: unit.seq)
    fills: dict[tuple[int, int, int, int], Unit] = {}
    run: list[Unit] = []
    low, blocked = -1, False
    for unit in [*ordered, None]:
        if unit is not None and id(unit) not in chosen:
            if unit.box is not None:
                # A box the alignment used for something other than one character (a split, a merge)
                # leaves the gap's count of boxes in doubt.
                blocked = True
            elif str(unit.kind) == "char" and len((label_of(unit) or "").strip()) == 1:
                run.append(unit)
            continue
        high = ranks[box_key(unit.box)] if unit is not None else len(ranks)
        between = [box for rank, box in free if low < rank < high]
        if run and not blocked and len(between) == len(run):
            fills.update(zip(between, run, strict=True))
        run, low, blocked = [], high, False
    return fills


def unplaced_reason(box: tuple[int, int, int, int], new: Sequence[Unit]) -> str:
    """Why the new alignment names no single character for `box`."""
    holders = [unit for unit in new if box_key(unit.box) == box]
    if not holders:
        return "no-unit"
    if len(holders) > 1:
        return "shared"
    unit = holders[0]
    if unit.group_id is not None or unit.granularity != "char":
        return "split"
    if str(unit.kind) != "char":
        return "not-a-character"
    return "several-characters"


def relabel(old: Sequence[Unit], new: Sequence[Unit], protected: Collection[str] = ()) -> list[dict[str, Any]]:
    """One record per boxed old unit of a line: the label the new alignment gives its box.

    `status` is `unchanged` when the label stands, `relabelled` when the box holds another character
    of the line, `unplaced` when the new alignment names no single character for the box
    (`placements`, `gap_fills`), and `protected` when a person reviewed the unit or it is in
    `protected`. A label that comes from a gap fill is marked `fill: gap`.
    """
    placed = placements(new)
    fills = gap_fills(old, new, placed)
    placed = {**placed, **fills}
    records = []
    for unit in old:
        if unit.box is None:
            continue
        found = placed.get(box_key(unit.box))
        record = {"unit_id": unit.id, "line_id": unit.line_id, "box": box_key(unit.box), "before": label_of(unit),
                  "after": label_of(found) if found else None, "seq": found.seq if found else None,
                  "review": str(found.review) if found else None}
        if unit.id in protected or unit.review in HUMAN:
            record["status"] = "protected"
        elif found is None:
            record["status"] = "unplaced"
            record["reason"] = unplaced_reason(record["box"], new)
        elif record["after"] == record["before"]:
            record["status"] = "unchanged"
        else:
            record["status"] = "relabelled"
        if found is not None and record["box"] in fills:
            record["fill"] = "gap"
        records.append(record)
    return records


def realign(lines: Sequence[Line], detections: dict[str, list[Box]], *, run: align.Run, classifier: Any,
            crop_of: Any) -> dict[str, list[Unit]]:
    """The current aligner's units of each line, over the detections cached for its page."""
    out: dict[str, list[Unit]] = {}
    for line in lines:
        found = [align.Detection(box=box, score=1.0) for box in detections.get(line.page_id, [])]
        units, _ = align.align_line(line, found, run=run, classifier=classifier, crop_of=crop_of)
        out[line.id] = units
    return out


def applied(unit: Unit, record: dict[str, Any]) -> Unit:
    """`unit` as the record leaves it: the new label and place in the line, or no label when unplaced.

    An unchanged unit takes only its place in the reading order, so the line no longer reads as stale;
    an unplaced one has no place in it.
    The id and the box stay. The evidence goes into `meta["box_relabel"]`, with the label before.
    """
    status = record["status"]
    if status == "unchanged" and record["seq"] is not None:
        return unit.model_copy(update={"seq": record["seq"]})
    if status not in ("relabelled", "unplaced"):
        return unit
    note = {"method": METHOD, "status": status, "before": record["before"], "after": record["after"]}
    meta = {**(unit.meta or {}), "box_relabel": note}
    if status == "unplaced":
        return unit.model_copy(update={"seq": None, "text_source": None, "reading": None, "unicode": None,
                                       "candidates": [], "review": ReviewState.REJECTED, "meta": meta})
    return unit.model_copy(update={"meta": meta, **record["fields"]})


def fields_of(unit: Unit) -> dict[str, Any]:
    """What a relabelled unit takes from the new unit on its box."""
    return {key: getattr(unit, key) for key in ("seq", "kind", "text_source", "reading", "unicode", "classification",
                                                "script", "candidates", "confidence", "review")}


def aligned_lines(units: Iterable[Unit]) -> set[str]:
    """The ids of the lines that hold boxed detect-align units."""
    return {unit.line_id for unit in units
            if unit.line_id and unit.method == "detect-align" and unit.box is not None}


def repair(old_units: Sequence[Unit], lines: Sequence[Line], detections: dict[str, list[Box]], *, run: align.Run,
           classifier: Any, crop_of: Any, protected: Collection[str] = (),
           every: bool = False) -> tuple[list[Unit], list[dict[str, Any]]]:
    """Relabel the units of every stale line in `lines`, or with `every` of every aligned line in it, and
    return all of `old_units` with the records.

    `every` is for a dataset known to be aligned in the old order throughout: a line whose boxes
    happen to run down the column in sequence passes the stale test, and its labels may still sit
    one box off where the old order swapped two neighbours.
    """
    by_line: dict[str, list[Unit]] = defaultdict(list)
    for unit in old_units:
        if unit.line_id:
            by_line[unit.line_id].append(unit)
    wanted = aligned_lines(old_units) if every else stale_lines(old_units)
    chosen = [line for line in lines if line.id in wanted and line.vertical]
    records: list[dict[str, Any]] = []
    changed: dict[str, Unit] = {}
    for line in chosen:
        new = realign([line], detections, run=run, classifier=classifier, crop_of=crop_of)[line.id]
        placed = placements(new)
        placed.update(gap_fills(by_line[line.id], new, placed))
        own = {unit.id: unit for unit in by_line[line.id]}
        for record in relabel(by_line[line.id], new, protected):
            unit = own[record["unit_id"]]
            if record["status"] == "relabelled":
                record["fields"] = fields_of(placed[record["box"]])
            changed[unit.id] = applied(unit, record)
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
                      protect: Collection[str] = (), every: bool = False) -> dict[str, int]:
    """Relabel the stale lines of `directory`, or with `every` all its aligned lines, and write the
    result to `out`, never to `directory`.

    `out` gets `units.parquet`, every unit of the source with the stale lines relabelled, and
    `relabels.jsonl`, one record per boxed unit of a stale line. `detections` is the cache of the
    boxes the units were cut from, keyed by page.
    """
    from . import ainu, images, tables

    directory, out = Path(directory), Path(out)
    if out.resolve() == directory.resolve():
        raise ValueError("the relabel writes a derived dataset, never its source")
    dataset = tables.Dataset(directory)
    units = tables.read(directory / "units.parquet", Unit)
    wanted = aligned_lines(units) if every else stale_lines(units)
    pages = {unit.page_id for unit in units if unit.line_id in wanted and unit.page_id}
    lines = sorted((line for batch in dataset.scan("lines", keep=tables.In("page_id", pages)) for line in batch
                    if line.id in wanted and line.box is not None), key=lambda line: (line.page_id, line.seq))
    page_records = {page.id: page for batch in dataset.scan("pages", keep=tables.In("id", pages)) for page in batch}
    # Without its page image the classifier scores every crop at the floor and the alignment places by
    # position alone, which is the guess this repair exists to replace: such a line is left as it is.
    unread = {page for page, record in page_records.items() if images.path_for(record.image) is None}
    lines = [line for line in lines if line.page_id not in unread]
    repaired, records = repair(units, lines, ainu.read_detections(Path(detections)), run=run, classifier=classifier,
                               crop_of=align._crop_reader(dataset, page_records), protected=set(protect),
                               every=every)
    out.mkdir(parents=True, exist_ok=True)
    tables.write(out / "units.parquet", repaired, Unit)
    write_records(out / "relabels.jsonl", records)
    return {"units": len(units), "chosen_lines": len(wanted), "lines": len(lines), "pages_without_image": len(unread),
            **counts(records)}
