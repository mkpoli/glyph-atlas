"""Relabel Ainu record crops whose OCR reading the page's transcription corrects.

ainu-records labels a block it could not find in the transcription by OCR: the block's text is what
the OCR read, cut into as many ink boxes as it has characters. Most of these pages have a みんなで翻刻
transcription all the same, and where the OCR misread a character the transcription has it right: the
block 同千七百両程緋運上 is 同千七百匁程鯡運上 on its page.

Each OCR block is aligned inside its page's transcription (`koji.plain` of it, letters only) by a
local alignment. A block is used when at least half its characters, and at least `MIN_MATCHES`, match
the transcription exactly, and no other place on the page aligns as well (a page that repeats the
phrase leaves the block alone). A character the OCR read differently is corrected when it is aligned
to one character of the transcription, its run of differing characters is at most `MAX_RUN` long, and
the run has an exact match on both sides: the box count says the OCR saw one character there, and both
neighbours place it. Insertions and deletions are left alone, since a box the OCR added or missed has
no one character of the transcription to take.

A crop takes the correction of the character its label came from: its own position, or the position a
shift repair (`block-shift-v1`, `block-boundary-v1`) moved it to. A crop whose label no longer is the
OCR's reading there (reviewed, relabelled by another rule) is left alone, as is a crop a person
reviewed, one named in `protect`, and one whose review state is anything but `machine`. The crop's
`text_source` becomes the transcription's character at its own position, so a later shift repair
works from the transcription. The relabel is a model event in the store's journal, with the evidence
that chose it.
"""
from __future__ import annotations

import json
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterable
from pathlib import Path

from .. import koji

METHOD = "ocr-transcription-v1"
SHIFT_METHODS = {"block-shift-v1", "block-boundary-v1"}
MATCH, MISMATCH, GAP = 2, -1, -2
MIN_MATCHES = 3
MAX_RUN = 3


def letters(text: str) -> str:
    """The letters of a transcription in reading order, its notation, spaces and punctuation dropped."""
    return "".join(c for c in unicodedata.normalize("NFC", koji.plain(text)) if unicodedata.category(c)[0] == "L")


def align(block: str, page: str) -> tuple[list[tuple[int | None, int | None]], int, int]:
    """The best local alignment of `block` inside `page` as (block index, page index) pairs, with its
    score and the best score of any alignment ending away from it."""
    n, m = len(block), len(page)
    score = [[0] * (m + 1) for _ in range(n + 1)]
    best = (0, 0, 0)
    for i in range(1, n + 1):
        row, above, character = score[i], score[i - 1], block[i - 1]
        for j in range(1, m + 1):
            value = max(0, above[j - 1] + (MATCH if character == page[j - 1] else MISMATCH),
                        above[j] + GAP, row[j - 1] + GAP)
            row[j] = value
            if value > best[0]:
                best = (value, i, j)
    top, i, j = best
    pairs: list[tuple[int | None, int | None]] = []
    while i and j and score[i][j]:
        value = score[i][j]
        if value == score[i - 1][j - 1] + (MATCH if block[i - 1] == page[j - 1] else MISMATCH):
            pairs.append((i - 1, j - 1))
            i, j = i - 1, j - 1
        elif value == score[i - 1][j] + GAP:
            pairs.append((i - 1, None))
            i -= 1
        else:
            pairs.append((None, j - 1))
            j -= 1
    pairs.reverse()
    start, end = j, best[2]
    rival = max((score[r][c] for c in range(1, m + 1) if c < start - n or c > end + n for r in range(1, n + 1)),
                default=0)
    return pairs, top, rival


def corrections(block: str, page: str) -> dict[int, str]:
    """The block's positions the transcription corrects, with the character it has there."""
    if len(block) < MIN_MATCHES:
        return {}
    pairs, top, rival = align(block, page)
    same = [i is not None and j is not None and block[i] == page[j] for i, j in pairs]
    if sum(same) < max(MIN_MATCHES, len(block) / 2) or rival >= top:
        return {}
    found = {}
    k = 0
    while k < len(pairs):
        if same[k] or None in pairs[k]:
            k += 1
            continue
        end = k
        while end < len(pairs) and not same[end] and None not in pairs[end]:
            end += 1
        if k > 0 and same[k - 1] and end < len(pairs) and same[end] and end - k <= MAX_RUN:
            found.update({i: page[j] for i, j in pairs[k:end]})
        k = end
    return found


def propose(blocks: dict[tuple, list[dict]], pages: dict[str, str], protected: set[str]) -> tuple[list[dict], Counter]:
    """The relabels of every block.

    `blocks` lists each OCR block's crops as dicts with `id`, `page_id`, `context` (the block's OCR
    text), `position`, `offset` (where a shift repair moved its label from, else 0), `label` and
    `text` (its `text_source`).
    """
    counts: Counter = Counter()
    proposals = []
    for key, crops in blocks.items():
        page = pages.get(crops[0]["page_id"])
        if page is None:
            counts["no transcription"] += 1
            continue
        context = unicodedata.normalize("NFC", crops[0]["context"] or "")
        fixed = corrections(context, page)
        counts["aligned" if fixed or len(context) >= MIN_MATCHES else "short"] += 1
        for crop in crops:
            if crop["id"] in protected:
                continue
            source = crop["position"] + crop["offset"]
            text = fixed.get(crop["position"])
            target = fixed.get(source)
            if not 0 <= source < len(context) or crop["label"] != context[source]:
                target = None
            if target is None and text is None:
                continue
            proposals.append({"unit_id": crop["id"], "before": crop["label"], "character": target or crop["label"],
                              "text_before": crop["text"], "text": text or crop["text"], "offset": crop["offset"],
                              "block": list(key), "context": context})
    return [p for p in proposals if p["character"] != p["before"] or p["text"] != p["text_before"]], counts


def run(dataset: Path, *, pages: Path, apply: bool = False, protect: Iterable[str] = ()) -> dict:
    """Propose, and with `apply` record, the relabels of every OCR block of `dataset`."""
    import pyarrow.parquet as pq

    from .atlas import script_of_identity, written_identity
    from .refine import _changes, encoded
    from .store import SEEN, Conflict, Store

    texts = {row["page_id"]: letters(row["text_raw"] or "")
             for row in pq.read_table(pages, columns=["page_id", "text_raw"]).to_pylist()}
    store = Store(dataset)
    protected = {event.target_id for event in store.events() if event.role != "model" and event.field != SEEN}
    protected |= set(protect)
    units, blocks = {}, defaultdict(list)
    for unit, revision in store.unit_snapshot():
        meta = unit.meta or {}
        record = meta.get("ainu_records") or {}
        if not unit.active or record.get("origin") != "ocr" or record.get("position") is None:
            continue
        if str(unit.review) != "machine":
            protected.add(unit.id)
        relabel = meta.get("feedback_identity") or {}
        offset = int(relabel["offset"]) if relabel.get("method") in SHIFT_METHODS and relabel.get("offset") is not None else 0
        units[unit.id] = (unit, revision)
        blocks[(record.get("key"), record.get("page"), record.get("block"))].append(
            {"id": unit.id, "page_id": unit.page_id, "context": record.get("context"), "position": int(record["position"]),
             "offset": offset, "label": written_identity(unit), "text": unit.text_source})
    proposals, counts = propose(blocks, texts, protected)
    for item in proposals:
        unit, revision = units[item["unit_id"]]
        item["status"] = "proposed"
        if not apply:
            continue
        character = item["character"]
        evidence = {"kind": "ocr-transcription-repair", "method": METHOD, "automated": True,
                    **{k: v for k, v in item.items() if k not in ("unit_id", "status")}}
        values = {"text_source": item["text"]}
        if character != item["before"]:
            values |= {"unicode": encoded(character), "script": script_of_identity(character), "review": "machine",
                       "meta": {**(unit.meta or {}), "feedback_identity": evidence}}
        try:
            _changes(store, unit, values, evidence, base_revision=revision)
        except Conflict:
            item["status"] = "stale"
            continue
        item["status"] = "relabelled"
    counts.update(item["status"] for item in proposals)
    counts["relabels"] = sum(1 for item in proposals if item["character"] != item["before"])
    return {"method": METHOD, "blocks": len(blocks), "crops": len(units), "counts": dict(counts), "items": proposals}


def write(result: dict, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n")
