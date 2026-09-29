"""Relabel crops whose transcription block is one or two places out of step with its boxes.

The Ainu records label a block's crops by cutting its transcription into as many ink boxes as it has
characters and giving box i character i. One bad cut moves every later label of the block: the crop
of 金 is filed under 表, the character written before it. Such a crop is not a new reading; the
character it shows is in the block's own text, one or two places away.

Every crop is read by the classifier, and each crop gets the probability that it shows the label at
each offset −2…+2 of its own position in the block. A Viterbi pass over the block chooses one offset
per crop, paying `SWITCH` nats for each change of offset, so an offset has to be carried by a run of
crops: a single crop the classifier misreads cannot move its label on its own. A crop is relabelled
with the character at its chosen offset when that offset is not zero, the crop shows that character
with at least `MIN_P`, at least `MIN_SUPPORT` crops of its run do the same, and both sides of its box
are at least `MIN_SIDE` pixels. The character written in the block is taken as it stands, so a crop
filed under 表 whose text runs 前金 becomes 金 even where the scan shows a form of it.

Measured on 2026-09-29 against the review site (docs/reports/block-shift-repair.md): among the crops
a person reviewed, the rule relabels 43; 40 take the reviewer's correction (or its 新字・旧字 or
katakana form), 1 another character and 2 were confirmed as they stood. It finds 40 of the 57
corrections reviewers made on these blocks. Of 40 random relabels among unreviewed crops, 28 were
plainly right by eye, about 9 uncertain on small or damaged crops and about 3 wrong; the label
before was wrong on nearly all of them.

A crop a person reviewed is never relabelled, and neither is one named in `protect` (the ids the
site holds reviews for). The relabel is a model event in the store's journal, with the evidence that
chose it, so it can be listed and undone like any other.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import numpy as np

METHOD = "block-shift-v1"
OFFSETS = (-2, -1, 0, 1, 2)
#: The least probability a crop gives any label, so that one crop cannot make a path impossible.
FLOOR = 1e-4
#: What a change of offset between neighbouring crops costs, in nats.
SWITCH = 4.0
#: A crop is relabelled only when it shows the character at its offset with at least this much,
MIN_P = 0.8
#: and at least this many crops of its run show theirs with half that or more.
MIN_SUPPORT = 2
#: Smaller boxes are mostly cut from a stroke or a dot, and reviewers report them as bad crops.
MIN_SIDE = 10


def sequence_key(unit) -> tuple | None:
    """The block a unit's label was cut from: its line, or its record's page and block."""
    if unit.line_id:
        return ("line", unit.line_id)
    record = (unit.meta or {}).get("ainu_records")
    if record and record.get("block") is not None:
        return ("block", record.get("key"), record.get("page"), record["block"])
    return None


def path(cost: np.ndarray, switch: float = SWITCH) -> list[int]:
    """The column of `cost` (crops by offsets) chosen for each crop, paying `switch` per change."""
    n, k = cost.shape
    total = cost[0].copy()
    back = np.zeros((n, k), dtype=int)
    change = switch * (1 - np.eye(k))
    for i in range(1, n):
        options = total[None, :] + change
        back[i] = options.argmin(1)
        total = options.min(1) + cost[i]
    chosen = [int(total.argmin())]
    for i in range(n - 1, 0, -1):
        chosen.append(int(back[i][chosen[-1]]))
    return chosen[::-1]


def propose(blocks: dict[Any, list[tuple[str, int, str]]], shown: dict[str, np.ndarray],
            sizes: dict[str, tuple[float, float]], protected: set[str]) -> list[dict]:
    """The relabels of every block.

    `blocks` lists each block's crops as (id, position, label); `shown[id]` holds, for each offset of
    `OFFSETS`, the probability that the crop shows the label at that offset (`FLOOR` where there is
    none). `sizes` gives each box's width and height.
    """
    zero = OFFSETS.index(0)
    proposals = []
    for key, crops in blocks.items():
        crops = sorted(crops, key=lambda crop: crop[1])
        by_position = {position: label for _, position, label in crops}
        scored = [crop for crop in crops if crop[0] in shown]
        if not scored:
            continue
        p = np.stack([shown[crop[0]] for crop in scored])
        chosen = path(-np.log(np.maximum(p, FLOOR)))
        for i, (identity, position, label) in enumerate(scored):
            column = chosen[i]
            offset = OFFSETS[column]
            target = by_position.get(position + offset)
            if not offset or not target or target == label or identity in protected:
                continue
            low = i
            while low > 0 and chosen[low - 1] == column:
                low -= 1
            high = i
            while high < len(scored) - 1 and chosen[high + 1] == column:
                high += 1
            support = int((p[low:high + 1, column] >= MIN_P / 2).sum())
            if p[i, column] < MIN_P or support < MIN_SUPPORT or min(sizes.get(identity, (0, 0))) < MIN_SIDE:
                continue
            proposals.append({"unit_id": identity, "before": label, "character": target, "offset": offset,
                              "p": round(float(p[i, column]), 4), "p_label": round(float(p[i, zero]), 4),
                              "run": high - low + 1, "support": support, "block": list(key)})
    return proposals


def _shown(store, blocks, checkpoint: Path) -> dict[str, np.ndarray]:
    """Classify every crop by the display image the dataset publishes, and keep, per offset, the
    probability that it shows the label at that offset."""
    import re

    from PIL import Image

    from ..classify import preprocess
    from ..form_clusters import Encoder
    from .atlas import router
    from .media import MediaCache
    from .quiz_suspects import Labels
    from .request_cache import lookup_scope

    encoder = Encoder(checkpoint)
    labels = Labels(encoder.classes)
    media = MediaCache()
    listing = next(r.endpoint for r in router(store, media=media).routes if r.path == "/atlas" and "GET" in r.methods)
    # Called as a function, the endpoint takes every parameter given here or its plain default.
    with lookup_scope():
        images = {item["id"]: found[1] for item in listing(reviewer=None, limit=10**7)["items"]
                  if (found := re.fullmatch(r"/atlas/media/([0-9a-f]{64})\.webp", item["image"] or ""))}
    wanted = []
    for crops in blocks.values():
        by_position = {position: label for _, position, label in crops}
        for identity, position, _ in crops:
            if identity in images:
                wanted.append((identity, [by_position.get(position + o) for o in OFFSETS]))
    shown = {}
    for start in range(0, len(wanted), 512):
        batch, pixels = [], []
        for identity, around in wanted[start:start + 512]:
            try:
                with Image.open(media.materialize(images[identity])) as picture:
                    pixels.append(np.asarray(preprocess(picture.convert("L"), size=encoder.size), dtype=np.uint8))
            except (OSError, ValueError):
                continue
            batch.append((identity, around))
        if not batch:
            continue
        _features, probabilities = encoder.classify(np.stack(pixels))
        for (identity, around), row in zip(batch, probabilities, strict=True):
            shown[identity] = np.array([float(row[labels.mask(label)].sum()) if label else FLOOR for label in around])
    return shown


def run(dataset: Path, *, checkpoint: Path, apply: bool = False, protect: Iterable[str] = ()) -> dict:
    """Propose, and with `apply` record, the relabels of every block of `dataset`."""
    from .atlas import script_of_identity, written_identity
    from .refine import _changes, encoded
    from .store import SEEN, Store

    store = Store(dataset)
    reviewed = {event.target_id for event in store.events() if event.role != "model" and event.field != SEEN}
    protected = reviewed | set(protect)
    units, blocks, sizes = {}, defaultdict(list), {}
    for unit, revision in store.unit_snapshot():
        key = sequence_key(unit)
        if not unit.active or str(unit.kind) != "char" or key is None or unit.seq is None or unit.box is None:
            continue
        label = written_identity(unit)
        if not label:
            continue
        units[unit.id] = (unit, revision)
        blocks[key].append((unit.id, unit.seq, label))
        sizes[unit.id] = (unit.box.w, unit.box.h)
    shown = _shown(store, blocks, checkpoint)
    proposals = propose(blocks, shown, sizes, protected)
    counts = Counter()
    for item in proposals:
        unit, revision = units[item["unit_id"]]
        item["status"] = "proposed"
        if not apply:
            continue
        character = item["character"]
        evidence = {"kind": "block-shift-repair", "method": METHOD, "automated": True,
                    **{k: v for k, v in item.items() if k not in ("unit_id", "status")}}
        values = {"unicode": encoded(character), "reading": character, "script": script_of_identity(character),
                  "review": "machine", "meta": {**(unit.meta or {}), "feedback_identity": evidence}}
        _changes(store, unit, values, evidence, base_revision=revision)
        item["status"] = "relabelled"
    counts.update(item["status"] for item in proposals)
    return {"method": METHOD, "blocks": len(blocks), "crops": len(units), "read": len(shown), "protected": len(protected & set(units)),
            "counts": dict(counts), "items": proposals}


def write(result: dict, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n")
