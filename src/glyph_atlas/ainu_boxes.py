"""Bring ainu-records' crop repairs to the atlas units cut from the same occurrences.

ainu-records repairs a crop by giving its occurrence a new rectangle and records each repair in a
ledger (`data/editorial/character-crop-repairs/*.json`) with the rectangle before and after. An
`ar:` unit still holding the rectangle before is moved to the one after; any other box is someone's
later decision and stays. A unit a person reviewed is never moved, nor one whose box would leave its
page. The label is untouched: ainu-records' repairs change only where the crop is cut.

Only ledgers marked `"status": "applied"` count: a ledger the repair script has just proposed is
not yet part of ainu-records. Later ledgers can repair a box an earlier one produced, so a unit
follows the chain from whatever box it holds. Reviews made on the site reach this store only when
they are imported, so import them before applying; a crop recut first would refuse them.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

METHOD = "ainu-records-crop-repair-v1"


def repairs(ledgers: Path) -> dict[tuple[str, str], list[tuple[list[int], list[int], str]]]:
    """(copy key, occurrence id) → its (box before, box after, policy) repairs, oldest first."""
    found: dict[tuple[str, str], list] = {}
    for path in sorted(ledgers.glob("*.json")) if ledgers.is_dir() else [ledgers]:
        ledger = json.loads(path.read_text(encoding="utf-8"))
        if ledger.get("status") != "applied":
            continue
        for batch in ledger.get("batches", []):
            for change in batch.get("changes", []):
                found.setdefault((batch["unit"], change["after"]["id"]), []).append(
                    (list(change["before"]["box"]), list(change["after"]["box"]), ledger.get("policy", "")))
    return found


def resolve(box: list[int], chain: list[tuple[list[int], list[int], str]]) -> tuple[list[int], str] | None:
    """The box the repairs lead to from `box`, and the last policy applied; None if none applies."""
    current, policy = box, None
    for _ in chain:
        step = next(((after, rule) for before, after, rule in chain if before == current), None)
        if step is None or step[0] == current:
            break
        current, policy = step
    return (current, policy) if policy is not None else None


def run(dataset: Path, ledgers: Path, *, apply: bool = False) -> dict:
    """Propose, and with `apply` record, the box of every unit a ledger repaired."""
    from .review.refine import _changes
    from .review.store import SEEN, Conflict, Store

    found = repairs(ledgers)
    store = Store(dataset)
    reviewed = {event.target_id for event in store.events() if event.role != "model" and event.field != SEEN}
    pages = store.pages()
    counts, items = Counter(), []
    for unit, revision in store.unit_snapshot():
        if not unit.id.startswith("ar:"):
            continue
        place = (unit.meta or {}).get("ainu_records") or {}
        chain = found.get((place.get("key"), place.get("id")))
        if chain is None or not unit.active or unit.box is None:
            continue
        box = [unit.box.x, unit.box.y, unit.box.w, unit.box.h]
        target = resolve(box, chain)
        if target is None:
            counts["already repaired" if box == chain[-1][1] else "box changed since"] += 1
            continue
        (after, policy), before = target, box
        if unit.id in reviewed or str(unit.review) != "machine":
            counts["reviewed"] += 1
            continue
        page = pages.get(unit.page_id)
        x, y, w, h = after
        if page is None or w < 2 or h < 2 or x < 0 or y < 0 or x + w > page.width or y + h > page.height:
            counts["off the page"] += 1
            continue
        item = {"unit_id": unit.id, "before": before, "after": after, "policy": policy, "status": "proposed"}
        if apply:
            evidence = {"kind": "upstream-crop-repair", "method": METHOD, "automated": True, "source": "ainu-records",
                        "occurrence": f"{place['key']}#{place['id']}", "policy": policy, "before": before, "after": after,
                        "base_revision": revision}
            try:
                # The revision is in the evidence, so a crop moved back since an earlier run is recut again.
                _changes(store, unit, {"box": {"x": x, "y": y, "w": w, "h": h}}, evidence, base_revision=revision)
                item["status"] = "recut"
            except Conflict:
                # Reviewed or changed since it was read; the next run judges it as it stands.
                item["status"] = "stale"
        counts[item["status"]] += 1
        items.append(item)
    return {"method": METHOD, "repairs": len(found), "counts": dict(counts), "items": items}
