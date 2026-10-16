"""Release withheld crops whose own box shows the character they are filed under.

The alignment repair withholds a unit when it cannot settle where the unit's character is: the ink at
the position the corrected reading order gives it scores worse than another crop of the line, two
characters would share a box, or the line is placed as one sequence and another of its characters is
unsettled. A withheld unit keeps the box it had. The repair judged the *corrected* position and never
the box the unit kept, and on the Ainu manuscripts that box is often the right one.

This pass reads the kept box. The classifier gives, for the unit's own label and for the labels written
one and two places either side of it in the line, the probability that the crop shows each. A unit is
released when its crop shows its own label with at least `MIN_P` and with no less than any neighbour's
label, and both sides of its box are at least `MIN_SIDE` pixels. Released, its repair note says the pass
stands behind the box (`reliable`, `quiz`) and keeps what it replaced under `before`, so `undo` puts
it back; nothing else on the unit changes. A crop that shows a neighbour's label stays withheld: relabelling single crops that way
was measured and is not reliable enough.

Only the repair's own doubt is lifted. A unit a person reviewed, one named in `protect`, one whose note
records a report or a crop issue, and one a source contests is never released.

Measured on 2026-10-16 (docs/reports/withheld-release.md): of 38.5k withheld crops on the site, the pass
releases 21,942 `hk:` crops. Of the 44 releases whose crop has a site verdict, 43 matched it; the other
was a merged crop, which the report rule now leaves to a person. Of 48 random releases among unreviewed
crops, 46 were plainly right by eye, 1 uncertain and none wrong.
"""
from __future__ import annotations

import json
import math
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterable
from pathlib import Path

from .shift_repair import MIN_SIDE, OFFSETS, place

METHOD = "withheld-release-v1"
#: The least probability with which a released crop shows its own label.
MIN_P = 0.9
#: Notes that name a person's report or a crop defect: the unit waits for a person whatever the
#: classifier reads.
REPORTED = ("crop issue", "reviewer", "merged box", "blank", "rejected this ink")


def releasable(note: dict) -> bool:
    """Whether a withheld note records only the repair's own doubt about where the character is."""
    reason = note.get("reason") or ""
    return (note.get("quiz") is False and note.get("status") == "uncertain"
            and not any(marker in reason for marker in REPORTED))


def decide(shown: list[float], min_p: float = MIN_P) -> bool:
    """Whether a crop read as `shown` (one probability per offset of `OFFSETS`) shows its own label."""
    own = shown[OFFSETS.index(0)]
    return own >= min_p and all(own >= p for p in shown if not math.isnan(p))


def release_note(note: dict, p: float) -> dict:
    """The repair note of a released unit: what the release changed, with what it replaced under `before`."""
    changed = {"status": "released", "withheld": False, "quiz": True, "reliable": True, "machine": True,
               "verified": False, "method": METHOD,
               "reason": f"the crop shows its own character with probability {p:.2f}, more than the characters "
                         f"written beside it"}
    return {**note, **changed, "before": {key: note[key] for key in changed if key in note}}


def restored_note(note: dict) -> dict:
    """The repair note a released unit had before its release."""
    before = note.get("before") or {}
    kept = {key: value for key, value in note.items() if key != "before" and key not in RELEASED_KEYS}
    return {**kept, **before}


RELEASED_KEYS = frozenset(release_note({}, 1.0)) - {"before"}


def run(dataset: Path, *, checkpoint: Path, apply: bool = False, protect: Iterable[str] = (),
        min_p: float = MIN_P) -> dict:
    """Propose, and with `apply` record, the release of every withheld unit of `dataset` its box supports."""
    from .atlas import written_identity
    from .refine import _changes
    from .shift_repair import _shown
    from .store import SEEN, Conflict, Store

    store = Store(dataset)
    reviewed = {event.target_id for event in store.events() if event.role != "model" and event.field != SEEN}
    protected = reviewed | set(protect)
    units, blocks, candidates, skipped = {}, defaultdict(list), set(), Counter()
    for unit, revision in store.unit_snapshot():
        found = place(unit)
        if not unit.active or str(unit.kind) != "char" or found is None or unit.box is None:
            continue
        label = written_identity(unit)
        text = unit.text_source or label
        if not label or not text:
            continue
        units[unit.id] = (unit, revision)
        blocks[found[0]].append((unit.id, found[1], text, label))
        note = (unit.meta or {}).get("alignment_repair") or {}
        if note.get("quiz") is not False:
            continue
        if unit.id in protected or str(unit.review) not in ("machine", "rejected"):
            skipped["protected"] += 1
        elif not releasable(note):
            skipped["reported"] += 1
        elif unicodedata.normalize("NFC", label) != unicodedata.normalize("NFC", text):
            # The classifier is read against the text at each place, and another pass changed this
            # unit's label away from its text, so its read says nothing about the label it has now.
            skipped["relabelled"] += 1
        elif min(unit.box.w, unit.box.h) < MIN_SIDE:
            skipped["small"] += 1
        else:
            candidates.add(unit.id)
    # Each candidate's whole line is passed so that its neighbours' labels are there to compare with.
    lines = {key: crops for key, crops in blocks.items() if any(crop[0] in candidates for crop in crops)}
    shown = _shown(store, lines, checkpoint, unknown=float("nan"))
    if candidates and not candidates & shown.keys():
        raise RuntimeError(f"none of the {len(candidates)} withheld crops could be read; the page images are not "
                           "reachable from this checkout")
    items, counts = [], Counter()
    for identity in sorted(candidates):
        read = shown.get(identity)
        if read is None:
            counts["unread"] += 1
            continue
        if not decide(read.tolist(), min_p):
            counts["kept"] += 1
            continue
        unit, revision = units[identity]
        note = unit.meta["alignment_repair"]
        p = round(float(read[OFFSETS.index(0)]), 4)
        item = {"unit_id": identity, "label": written_identity(unit), "p": p, "withheld_reason": note.get("reason"),
                "status": "proposed"}
        items.append(item)
        if not apply:
            continue
        # The revision makes each release its own request: a release made again after an undo is recorded.
        evidence = {"kind": "withheld-release", "method": METHOD, "automated": True, "revision": revision,
                    "p": p, "min_p": min_p,
                    "shown": [None if math.isnan(x) else round(x, 4) for x in read.tolist()], "offsets": list(OFFSETS)}
        released = release_note(note, p)
        try:
            _changes(store, unit, {"meta": {**unit.meta, "alignment_repair": released}}, evidence,
                     base_revision=revision)
        except Conflict:
            # Reviewed or changed since it was read; the next run judges it as it stands.
            item["status"] = "stale"
            continue
        item["status"] = "released"
    counts.update(item["status"] for item in items)
    return {"method": METHOD, "min_p": min_p, "candidates": len(candidates), "read": sum(i in shown for i in candidates),
            "skipped": dict(skipped), "counts": dict(counts), "items": items}


def undo(dataset: Path, *, apply: bool = False) -> dict:
    """Put back, with `apply`, the repair note of every unit this pass released."""
    from .refine import _changes
    from .store import Conflict, Store

    store = Store(dataset)
    counts = Counter()
    for unit, revision in store.unit_snapshot():
        note = (unit.meta or {}).get("alignment_repair") or {}
        if note.get("method") != METHOD or note.get("status") != "released":
            continue
        if not apply:
            counts["to restore"] += 1
            continue
        evidence = {"kind": "withheld-release-undo", "method": METHOD, "automated": True, "revision": revision}
        try:
            _changes(store, unit, {"meta": {**unit.meta, "alignment_repair": restored_note(note)}}, evidence,
                     base_revision=revision)
        except Conflict:
            counts["stale"] += 1
            continue
        counts["restored"] += 1
    return {"method": METHOD, "counts": dict(counts)}


def write(result: dict, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n")
