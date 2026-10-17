"""Withhold the worse of two crops that cut one written character twice.

The Ainu manuscripts are cut twice: `ar:` boxes come from the ainu-records transcription and `hk:`
boxes from detect-align over the same pages. Where both cut one written character and file it under
one label, the character gallery and the book listing show the same ink twice. This pass withholds one
copy and names the crop it repeats.

A pair is the same ink when the two boxes share at least `MIN_OVERLAP` of the smaller box's area. Only
pairs whose crops are both shown count: a crop that is withheld or rejected leaves the other as the only
copy. A pair in which a person reviewed either crop, or one is named in `protect`, is left as it is.

Either source cuts the worse box often enough that neither is kept by rule: an `ar:` box can take in a
neighbour's ink, a `hk:` box can hold part of the character. The classifier reads each crop of a pair
against the label, and the crop that shows it less surely is withheld.

The withhold is written to the unit's repair note, which the quiz, the galleries and the export read.
The note it replaces, or its absence, is kept under `before`, so `undo` puts it back.

Measured on 2026-10-17 (docs/reports/source-duplicates.md).
"""
from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from collections.abc import Iterable
from pathlib import Path

from ..schema import Box

METHOD = "source-duplicates-v1"
#: The least share of the smaller box's area the two boxes share for one ink.
MIN_OVERLAP = 0.5
#: Marks a note the pass wrote over no note at all.
NO_NOTE = None


def overlap(a: Box, b: Box) -> float:
    """The share of the smaller box's area that the two boxes share."""
    width = min(a.x + a.w, b.x + b.w) - max(a.x, b.x)
    height = min(a.y + a.h, b.y + b.h) - max(a.y, b.y)
    if width <= 0 or height <= 0:
        return 0.0
    return width * height / max(1, min(a.w * a.h, b.w * b.h))


def withhold_note(note: dict | None, kept: str) -> dict:
    """The repair note of a withheld copy, with the note it replaced under `before`."""
    return {"status": "duplicate", "withheld": True, "quiz": False, "reliable": False, "machine": True,
            "verified": False, "method": METHOD, "duplicate_of": kept,
            "reason": "another crop of the same ink, filed under the same character, shows it more clearly",
            "before": dict(note) if note is not None else NO_NOTE}


def restored_meta(meta: dict) -> dict:
    """The unit's meta as it was before the pass withheld it, keeping what later passes added to the note."""
    note = meta["alignment_repair"]
    before = note.get("before")
    added = {key: value for key, value in note.items() if key not in WITHHELD_KEYS}
    rest = {key: value for key, value in meta.items() if key != "alignment_repair"}
    restored = {**(before or {}), **added}
    return {**rest, "alignment_repair": restored} if restored or before is not NO_NOTE else rest


def worse(read: dict[str, float], copy: str, kept: str) -> str:
    """Which of two crops of one ink to withhold: the one the classifier reads its label in less surely.

    A crop the classifier cannot read, or a tie, withholds the `hk:` copy."""
    p_copy, p_kept = read.get(copy, math.nan), read.get(kept, math.nan)
    if math.isnan(p_copy):
        return copy
    return kept if math.isnan(p_kept) or p_copy > p_kept else copy


WITHHELD_KEYS = frozenset(withhold_note(None, ""))


def run(dataset: Path, *, checkpoint: Path, apply: bool = False, protect: Iterable[str] = (),
        min_overlap: float = MIN_OVERLAP) -> dict:
    """Propose, and with `apply` record, the withhold of the worse crop of every shown `hk:`/`ar:` pair."""
    if not 0.0 < min_overlap <= 1.0:
        raise ValueError(f"min_overlap is a share of the smaller box, above 0 and at most 1; got {min_overlap}")
    from .atlas import repair_withheld, written_identity
    from .refine import _changes
    from .shift_repair import _shown
    from .store import SEEN, Conflict, Store

    store = Store(dataset)
    reviewed = {event.target_id for event in store.events() if event.role != "model" and event.field != SEEN}
    protected = reviewed | set(protect)
    pages, units = defaultdict(lambda: ([], [])), {}
    for unit, revision in store.unit_snapshot():
        if not unit.active or str(unit.kind) != "char" or unit.box is None or repair_withheld(unit):
            continue
        if str(unit.review) == "rejected":
            continue
        label = written_identity(unit)
        if not label:
            continue
        side = {"ar:": 0, "hk:": 1}.get(unit.id[:3])
        if side is not None:
            pages[unit.page_id][side].append((unit, label))
            units[unit.id] = (unit, revision, label)
    pairs, skipped = [], Counter()
    for shown, copies in pages.values():
        for unit, label in copies:
            share, kept = max(((overlap(unit.box, other.box), other) for other, other_label in shown
                               if other_label == label), default=(0.0, None), key=lambda pair: pair[0])
            if share < min_overlap:
                continue
            if {unit.id, kept.id} & protected or str(unit.review) != "machine" or str(kept.review) != "machine":
                skipped["protected"] += 1
                continue
            pairs.append((unit.id, kept.id, label, share))
    # Each crop is read alone against its own label.
    blocks = {identity: [(identity, 0, label, label)] for pair in pairs for identity in pair[:2]
              for label in (units[identity][2],)}
    read = {identity: float(row[0]) for identity, row in
            (_shown(store, blocks, checkpoint, offsets=(0,), unknown=math.nan) if blocks else {}).items()}
    if blocks and not read:
        raise RuntimeError(f"none of the {len(blocks)} crops could be read; the page images are not reachable "
                           "from this checkout")
    items, withheld = [], set()
    for copy, kept, label, share in pairs:
        if {copy, kept} & withheld:
            # One crop of the pair is already withheld as the worse copy of another pair.
            skipped["paired twice"] += 1
            continue
        if not {copy, kept} <= read.keys():
            # An image that could not be loaded says nothing about which crop is the better cut.
            skipped["unread"] += 1
            continue
        loser = worse(read, copy, kept)
        winner = kept if loser == copy else copy
        withheld.add(loser)
        item = {"unit_id": loser, "duplicate_of": winner, "label": label, "overlap": round(share, 3),
                "p": _rounded(read.get(loser)), "p_kept": _rounded(read.get(winner)), "status": "proposed"}
        items.append(item)
        if not apply:
            continue
        unit, revision, _ = units[loser]
        if store.unit_snapshot(winner)[0][1] != units[winner][1]:
            # The crop that would stay was reviewed or changed while the crops were read.
            item["status"] = "stale"
            continue
        note = (unit.meta or {}).get("alignment_repair")
        evidence = {"kind": "source-duplicate", "method": METHOD, "automated": True, "revision": revision,
                    "duplicate_of": winner, "overlap": item["overlap"], "min_overlap": min_overlap,
                    "p": item["p"], "p_kept": item["p_kept"]}
        try:
            _changes(store, unit, {"meta": {**unit.meta, "alignment_repair": withhold_note(note, winner)}},
                     evidence, base_revision=revision)
        except Conflict:
            # Reviewed or changed since it was read; the next run judges it as it stands.
            item["status"] = "stale"
            continue
        item["status"] = "withheld"
    counts = Counter(item["status"] for item in items)
    sides = Counter(item["unit_id"][:3] for item in items)
    return {"method": METHOD, "min_overlap": min_overlap, "skipped": dict(skipped), "counts": dict(counts),
            "withheld_by_source": dict(sides), "items": sorted(items, key=lambda item: item["unit_id"])}


def _rounded(p: float | None) -> float | None:
    return None if p is None or math.isnan(p) else round(p, 4)


def undo(dataset: Path, *, apply: bool = False) -> dict:
    """Put back, with `apply`, the repair note of every unit this pass withheld."""
    from .refine import _changes
    from .store import Conflict, Store

    store = Store(dataset)
    counts = Counter()
    for unit, revision in store.unit_snapshot():
        note = (unit.meta or {}).get("alignment_repair") or {}
        if note.get("method") != METHOD or note.get("status") != "duplicate":
            continue
        if not apply:
            counts["to restore"] += 1
            continue
        evidence = {"kind": "source-duplicate-undo", "method": METHOD, "automated": True, "revision": revision}
        try:
            _changes(store, unit, {"meta": restored_meta(unit.meta)}, evidence, base_revision=revision)
        except Conflict:
            counts["stale"] += 1
            continue
        counts["restored"] += 1
    return {"method": METHOD, "counts": dict(counts)}


def write(result: dict, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n")
