"""Bring ainu-records' crop repairs to the atlas units cut from the same occurrences.

ainu-records repairs a crop by giving its occurrence a new rectangle and records each repair in a
ledger (`data/editorial/character-crop-repairs/*.json`) with the rectangle before and after. An
`ar:` unit still holding the rectangle before is moved to the one after; any other box is someone's
later decision and stays. A unit a person reviewed is never moved, nor one whose box would leave its
page. The label is untouched: ainu-records' repairs change only where the crop is cut.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

METHOD = "ainu-records-crop-repair-v1"


def repairs(ledgers: Path) -> dict[tuple[str, str], tuple[list[int], list[int], str]]:
    """(copy key, occurrence id) → (box before, box after, policy) for every repair in `ledgers`."""
    found = {}
    for path in sorted(ledgers.glob("*.json")) if ledgers.is_dir() else [ledgers]:
        ledger = json.loads(path.read_text(encoding="utf-8"))
        for batch in ledger.get("batches", []):
            for change in batch.get("changes", []):
                # Later ledgers repair what earlier ones left; the last word on an occurrence stands.
                found[(batch["unit"], change["after"]["id"])] = (
                    list(change["before"]["box"]), list(change["after"]["box"]), ledger.get("policy", ""))
    return found


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
        place = (unit.meta or {}).get("ainu_records") or {}
        repair = found.get((place.get("key"), place.get("id")))
        if repair is None or not unit.active or unit.box is None:
            continue
        before, after, policy = repair
        box = [unit.box.x, unit.box.y, unit.box.w, unit.box.h]
        if box == after:
            counts["already repaired"] += 1
            continue
        if box != before:
            counts["box changed since"] += 1
            continue
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
                        "occurrence": f"{place['key']}#{place['id']}", "policy": policy, "before": before, "after": after}
            try:
                _changes(store, unit, {"box": {"x": x, "y": y, "w": w, "h": h}}, evidence, base_revision=revision)
                item["status"] = "recut"
            except Conflict:
                # Reviewed or changed since it was read; the next run judges it as it stands.
                item["status"] = "stale"
        counts[item["status"]] += 1
        items.append(item)
    return {"method": METHOD, "repairs": len(found), "counts": dict(counts), "items": items}
