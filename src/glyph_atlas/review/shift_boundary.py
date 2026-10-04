"""Settle the edges of shifted runs, where two crops of a block claim one place of its text.

`shift_repair` moves a run of crops one or two places along their block's text. Where the run starts
or ends, the crop beside it keeps its old label, which is now the label the run's first or last crop
took: two crops claim one character of the text. A shift starts because one box of the block is an
extra cut (a stroke of a neighbour, a dot, a sliver of blank paper) or because a character got no box,
so one of the two crops, or one a little further on, is a box the text has no character for, or a
crop that belongs one place further along the run.

Each such claim is settled inside a window. The window runs out from the two crops to the nearest crop
on each side that shows the character it claims with at least `ANCHOR`, or that a person settled, or
to the block's end; those crops keep their places. The crops inside are aligned anew to the text
between the two anchors, in order: each crop takes the next character, or is an extra box, and a
character may be left without a box. A crop pays −log of the probability that it shows the character
it takes (at least `LEAST`, and `NEUTRAL` for a character the classifier has no class for), an extra
box pays −log of its prior (`extra_prior`: higher the smaller the box is against the block's median),
and a character left without a box pays `SKIP`. The cheapest alignment is taken when the next one costs
at least `MARGIN` nats more or, where no other alignment is possible, when every box it withholds
has a prior of at least `EXTRA_FORCED`; otherwise the window is left as it is. A crop the classifier
did not read anchors its window.

A crop that takes another character is relabelled, as `shift_repair` does, with its evidence. A crop
the alignment leaves without a character is kept and withheld: its `alignment_repair` note says it is
withheld from Quick review, with the reason, which the site already shows on a withheld crop. A crop a
person reviewed, one named in `protect`, one whose review state is anything but `machine`, and one
whose label two readings of the ink confirmed is never changed, and anchors its window. A box another
pass withheld keeps that pass's note; a box this pass withheld keeps its place in the text on a rerun.

Measured on 2026-10-04 in a dry run (docs/reports/block-shift-boundaries.md): the pass settles 2,413
of 3,348 windows, relabelling 2,131 crops and withholding 1,272, and neighbouring `ar:` crops with
one label fall from 3,383 pairs to 960. On reviewed crops it relabels 33 to the reviewer's correction
and none to another character; of 40 random decisions on unreviewed crops none was wrong by eye.
"""
from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from collections.abc import Iterable
from itertools import pairwise
from pathlib import Path
from typing import Any

import numpy as np

from .shift_repair import METHOD as SHIFT_METHOD
from .shift_repair import _shown, place

METHOD = "block-boundary-v1"
#: The offsets read for each crop: a run's −2…+2 and one more place on either side.
OFFSETS = (-3, -2, -1, 0, 1, 2, 3)
#: A crop that shows the character it claims with this much anchors its window.
ANCHOR = 0.5
#: The least probability a crop is taken to show any character with, so that a classifier sure of
#: the wrong class costs a crop's place no more than about 4.6 nats.
LEAST = 0.01
#: The probability given to a character the classifier has no class for: it neither shows nor denies it.
NEUTRAL = 0.05
#: What a character left without a box costs, in nats.
SKIP = -math.log(0.02)
#: How much cheaper the chosen alignment has to be than the next one.
MARGIN = 1.0
#: Where the only possible alignment withholds a box, its prior has to be at least this.
EXTRA_FORCED = 0.08
#: A window with more crops than this is left alone.
MAX_WINDOW = 8
#: The reason a withheld extra box shows on the site.
REASON = "the block's text has no character for this box: it holds a stroke, a dot or part of a neighbouring character"


def extra_prior(area: float) -> float:
    """The prior that a box is an extra cut, by its area over the block's median area."""
    return 0.5 if area < 0.2 else 0.25 if area < 0.35 else 0.08 if area < 0.6 else 0.02


def claims(crops: list[tuple[str, int, str, str]], offsets: dict[str, int],
           text: dict[int, str] | None = None) -> dict[str, int | None]:
    """The place of the block's text each crop's label is taken from.

    `crops` are (id, position, text at the position, label); `offsets` the offset a relabel recorded.
    A recorded offset is taken when the text there is the label; otherwise the nearest place within
    three whose text is the label, nearer before further and before after. A label the text has
    nowhere near (a person's correction) claims no place. `text` is the block's text by position,
    when it holds places no crop of `crops` has.
    """
    text = text if text is not None else {position: written for _, position, written, _ in crops}
    found: dict[str, int | None] = {}
    for identity, position, _, label in crops:
        offset = offsets.get(identity)
        if offset is not None and text.get(position + offset) == label:
            found[identity] = position + offset
            continue
        found[identity] = next((position + o for o in sorted(OFFSETS, key=lambda o: (abs(o), o))
                                if text.get(position + o) == label), None)
    return found


def align(costs: list[list[float]], extra: list[float], places: int) -> list[tuple[float, tuple]]:
    """The two cheapest monotone alignments of crops to `places` characters.

    `costs[j][i]` is what crop j pays to take character i, `extra[j]` what it pays to be an extra box,
    and every character no crop takes costs `SKIP`. Each alignment is (cost, the character each crop
    takes or None).
    """
    k = len(costs)
    best: dict[tuple[int, int], list[tuple[float, tuple]]] = {(0, 0): [(0.0, ())]}

    def push(state, cost, chosen):
        found = best.setdefault(state, [])
        # Skips are not part of an alignment, so two orders of an extra box and a skip are one alignment.
        if any(chosen == other for _, other in found):
            return
        found.append((cost, chosen))
        found.sort(key=lambda item: item[0])
        del found[2:]

    for j in range(k + 1):
        for i in range(places + 1):
            for cost, chosen in best.get((j, i), ()):
                if i < places:
                    push((j, i + 1), cost + SKIP, chosen)
                if j < k:
                    push((j + 1, i), cost + extra[j], (*chosen, None))
                    if i < places:
                        push((j + 1, i + 1), cost + costs[j][i], (*chosen, i))
    return best.get((k, places), [])


def resolve(blocks: dict[Any, list[tuple[str, int, str, str]]], shown: dict[str, np.ndarray],
            sizes: dict[str, tuple[float, float]], protected: set[str],
            offsets: dict[str, int] | None = None, passed: Iterable[str] = ()) -> list[dict]:
    """Every window where two neighbouring crops claim one place of their block's text, and what to do.

    `blocks` lists each block's crops as (id, position, the text written at that position, the
    crop's label now). `shown[id]` holds, for each offset of `OFFSETS`, the probability that the crop
    shows the text at that offset (`nan` where the classifier has no class for it). `sizes` gives
    each box's width and height, `offsets` the offset an earlier relabel recorded for a crop. A crop in
    `passed` (an extra box withheld before) keeps its place's text in the block and is otherwise left out.
    A crop the classifier did not read anchors its window, since nothing says where it belongs.
    """
    offsets, passed = offsets or {}, set(passed)
    return [window for key, crops in blocks.items()
            for window in _block(key, crops, shown, sizes, protected, offsets, passed)]


def _block(key, crops, shown, sizes, protected, offsets, passed) -> list[dict]:
    """The windows of one block, as `resolve` describes them."""
    zero = OFFSETS.index(0)
    windows: list[dict] = []
    crops = sorted(crops, key=lambda crop: crop[1])
    text = {position: written for _, position, written, _ in crops}
    crops = [crop for crop in crops if crop[0] not in passed]
    if not crops:
        return windows
    ids = [crop[0] for crop in crops]
    position = {crop[0]: crop[1] for crop in crops}
    label = {crop[0]: crop[3] for crop in crops}
    claim = claims(crops, offsets, text)
    areas = [sizes.get(i, (0, 0))[0] * sizes.get(i, (0, 0))[1] for i in ids]
    median = float(np.median(areas)) or 1.0

    def evidence(identity: str, place: int) -> float | None:
        if place not in text:
            return None
        row = shown.get(identity)
        offset = place - position[identity]
        if row is None:
            return NEUTRAL
        if not -zero <= offset <= zero:
            return LEAST
        value = float(row[zero + offset])
        return NEUTRAL if math.isnan(value) else max(value, LEAST)

    def anchored(identity: str) -> bool:
        return (identity in protected or claim[identity] is None or identity not in shown
                or (evidence(identity, claim[identity]) or 0) >= ANCHOR)

    area = {i: a / median for i, a in zip(ids, areas, strict=True)}
    visited: set[str] = set()
    for n in range(len(ids) - 1):
        x, y = ids[n], ids[n + 1]
        if claim[x] is None or claim[x] != claim[y] or x in visited:
            continue
        window: dict[str, Any] = {"block": list(key), "pair": [x, y], "label": label[x], "place": claim[x],
                                  "relabel": [], "extra": []}
        windows.append(window)
        if anchored(x) and anchored(y):
            window["status"] = "protected" if {x, y} & protected else "both-shown"
            continue
        low = n
        while low >= 0 and not anchored(ids[low]):
            low -= 1
        high = n + 1
        while high < len(ids) and not anchored(ids[high]):
            high += 1
        left = claim[ids[low]] if low >= 0 else min(text) - 1
        right = claim[ids[high]] if high < len(ids) else max(text) + 1
        inside = ids[low + 1:high]
        window["crops"] = inside
        visited.update(inside)
        places = list(range(left + 1, right)) if left is not None and right is not None else []
        if (left is None or right is None or left >= right or len(inside) > MAX_WINDOW
                or len(places) > MAX_WINDOW + 2):
            window["status"] = "unfit"
            continue
        costs = [[-math.log(p) if (p := evidence(i, place)) is not None else math.inf for place in places]
                 for i in inside]
        boxes = [area[i] for i in inside]
        top = align(costs, [-math.log(extra_prior(area)) for area in boxes], len(places))
        if not top or math.isinf(top[0][0]):
            window["status"] = "unfit"
            continue
        cost, chosen = top[0]
        margin = top[1][0] - cost if len(top) > 1 else math.inf
        window["cost"] = round(cost, 3)
        window["margin"] = None if math.isinf(margin) else round(margin, 3)
        window["skipped"] = len(places) - sum(c is not None for c in chosen)
        for identity, taken, size in zip(inside, chosen, boxes, strict=True):
            before = evidence(identity, claim[identity])
            if taken is None:
                window["extra"].append({"unit_id": identity, "label": label[identity], "area": round(size, 3),
                                        "p_label": round(before or 0, 4), "position": position[identity]})
            elif text[places[taken]] != label[identity]:
                place = places[taken]
                window["relabel"].append({"unit_id": identity, "before": label[identity], "character": text[place],
                                          "offset": place - position[identity], "p": round(evidence(identity, place), 4),
                                          "p_label": round(before or 0, 4)})
        # With no other alignment possible the window's crops are all extra boxes; a full-size box is
        # more likely a character the anchors misplace than ink without one.
        forced_full = math.isinf(margin) and any(extra_prior(size) < EXTRA_FORCED for size in boxes)
        window["status"] = "settled" if margin >= MARGIN and not forced_full else "unsure"
    return windows


def doubled(blocks: dict[Any, list[tuple[str, int, str, str]]], withheld: Iterable[str] = (),
            relabel: dict[str, str] | None = None) -> tuple[int, int]:
    """Crops of a block at neighbouring positions, and how many such pairs hold the same label.

    Crops in `withheld` are passed over, so the crops on either side of one are neighbours, and
    `relabel` gives the labels to count instead.
    """
    withheld, relabel = set(withheld), relabel or {}
    pairs = same = 0
    for crops in blocks.values():
        crops = sorted(crops, key=lambda crop: crop[1])
        passed = {position for identity, position, _, _ in crops if identity in withheld}
        kept = [(position, relabel.get(identity, label)) for identity, position, _, label in crops
                if identity not in withheld]
        for (a, first), (b, second) in pairwise(kept):
            if all(between in passed for between in range(a + 1, b)) and b > a:
                pairs += 1
                same += first == second
    return pairs, same


def run(dataset: Path, *, checkpoint: Path, apply: bool = False, protect: Iterable[str] = ()) -> dict:
    """Settle, and with `apply` record, every window of `dataset` where two crops claim one place."""
    from .atlas import script_of_identity, written_identity
    from .refine import _changes, encoded
    from .store import SEEN, Conflict, Store

    store = Store(dataset)
    reviewed = {event.target_id for event in store.events() if event.role != "model" and event.field != SEEN}
    protected = reviewed | set(protect)
    units, blocks, sizes, offsets, passed, held = {}, defaultdict(list), {}, {}, set(), set()
    for unit, revision in store.unit_snapshot():
        found = place(unit)
        if not unit.active or str(unit.kind) != "char" or found is None or unit.box is None:
            continue
        meta = unit.meta or {}
        label = written_identity(unit)
        text = unit.text_source or label
        if not label or not text:
            continue
        note = meta.get("alignment_repair") or {}
        # A label two readings of the ink agree on is settled like a person's.
        if str(unit.review) != "machine" or (note.get("status") == "confirmed" and note.get("reliable")):
            protected.add(unit.id)
        if note.get("withheld"):
            # A box this pass withheld before keeps its place's text and is otherwise passed over,
            # until a person reviews it; one another pass withheld keeps that pass's note.
            if note.get("method") == METHOD and unit.id not in protected:
                passed.add(unit.id)
            else:
                held.add(unit.id)
        relabel = meta.get("feedback_identity") or {}
        if relabel.get("method") in (SHIFT_METHOD, METHOD) and relabel.get("offset") is not None:
            offsets[unit.id] = int(relabel["offset"])
        units[unit.id] = (unit, revision)
        blocks[found[0]].append((unit.id, found[1], text, label))
        sizes[unit.id] = (unit.box.w, unit.box.h)
    shown = _shown(store, blocks, checkpoint, offsets=OFFSETS, unknown=float("nan"))
    windows = resolve(blocks, shown, sizes, protected, offsets, passed)
    settled = [w for w in windows if w["status"] == "settled"]
    relabels = {item["unit_id"]: item["character"] for w in settled for item in w["relabel"]}
    extras = [item["unit_id"] for w in settled for item in w["extra"]]
    counts: Counter = Counter()
    for window in settled:
        for item in window["relabel"] + window["extra"]:
            item["status"] = "already withheld" if item["unit_id"] in held else "proposed"
        if not apply:
            continue
        for item in window["relabel"]:
            unit, revision = units[item["unit_id"]]
            evidence = {"kind": "block-boundary-repair", "method": METHOD, "automated": True, "block": window["block"],
                        "margin": window["margin"], **{k: v for k, v in item.items() if k not in ("unit_id", "status")}}
            values = {"unicode": encoded(item["character"]), "script": script_of_identity(item["character"]),
                      "review": "machine", "meta": {**(unit.meta or {}), "feedback_identity": evidence}}
            item["status"] = _record(store, unit, values, evidence, revision, _changes, Conflict, "relabelled")
        for item in window["extra"]:
            if item["unit_id"] in held:
                item["status"] = "already withheld"
                continue
            unit, revision = units[item["unit_id"]]
            evidence = {"kind": "block-boundary-repair", "method": METHOD, "automated": True, "block": window["block"],
                        "margin": window["margin"], "verdict": "extra",
                        **{k: v for k, v in item.items() if k not in ("unit_id", "status")}}
            note = {**((unit.meta or {}).get("alignment_repair") or {}), "status": "withheld", "machine": True,
                    "verified": False, "reliable": False, "withheld": True, "quiz": False, "method": METHOD,
                    "reason": REASON}
            values = {"meta": {**(unit.meta or {}), "alignment_repair": note}}
            item["status"] = _record(store, unit, values, evidence, revision, _changes, Conflict, "withheld")
    for window in settled:
        counts.update(item["status"] for item in window["relabel"] + window["extra"])
    before = doubled(_by_prefix(blocks, "ar:"), passed)
    after = doubled(_by_prefix(blocks, "ar:"), passed | set(extras), relabels)
    return {"method": METHOD, "blocks": len(blocks), "crops": len(units), "read": len(shown),
            "protected": len(protected & set(units)), "windows": dict(Counter(w["status"] for w in windows)),
            "relabels": len(relabels), "extras": len(extras), "counts": dict(counts),
            "doubled_ar": {"pairs": before[0], "before": before[1], "after": after[1]}, "items": windows}


def _record(store, unit, values, evidence, revision, changes, conflict, done: str) -> str:
    try:
        changes(store, unit, values, evidence, base_revision=revision)
    except conflict:
        # Reviewed or changed since it was read; the next run judges it as it stands.
        return "stale"
    return done


def _by_prefix(blocks, prefix: str) -> dict:
    return {key: [crop for crop in crops if crop[0].startswith(prefix)] for key, crops in blocks.items()}


def write(result: dict, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n")
