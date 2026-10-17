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

Punctuation the OCR read (`。`, `・`) is left out of the alignment: the page's punctuation is dropped,
and its ruby readings stand beside their base characters, so a stop would line up with a kanji.

A crop takes the correction of the character its label came from: its own position, or the position a
shift repair (`block-shift-v1`, `block-boundary-v1`) moved it to; the relabel keeps that offset for
later passes. A crop whose label no longer is the OCR's reading there (reviewed, relabelled by another
rule) is left alone entirely, as is a crop a person reviewed, one named in `protect`, one whose review
state is anything but `machine`, one the alignment repair withheld, and one whose label it confirmed
reliably. Otherwise the crop's `text_source` becomes the transcription's character at its own
position, so a later shift repair works from the transcription. The relabel is a model event in the
store's journal, with the evidence that chose it.
"""
from __future__ import annotations

import json
import math
import tempfile
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterable
from pathlib import Path

from .. import koji

METHOD = "ocr-transcription-v1"
#: The relabels that record where a crop's label came from as an `offset` from its own position.
SHIFT_METHODS = {"block-shift-v1", "block-boundary-v1", METHOD}
UNREAD = "□▢"
#: How many characters on each side of a kana the script of its neighbours is counted over.
KANA_WINDOW = 10
#: The least probability a form is given, so that one the classifier never names still counts.
FLOOR = 1e-4
MATCH, MISMATCH, GAP = 2, -1, -2
MIN_MATCHES = 3
MAX_RUN = 3


def readable(character: str) -> bool:
    """Whether a character names ink of its own: a letter, or the mark for an unread one."""
    return unicodedata.category(character)[0] == "L" or character in UNREAD


def letters(text: str) -> str:
    """The letters of a transcription in reading order, its notation, spaces and punctuation dropped."""
    return "".join(c for c in unicodedata.normalize("NFC", koji.plain(text)) if readable(c))


def _best(block: str, page: str) -> tuple[list[list[int]], tuple[int, int, int]]:
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
    return score, best


def align(block: str, page: str) -> tuple[list[tuple[int | None, int | None]], int, int]:
    """The best local alignment of `block` inside `page` as (block index, page index) pairs, with its
    score and the best score of an alignment anywhere else on the page."""
    score, (top, i, j) = _best(block, page)
    end = j
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
    # The page with the aligned span blanked out: what scores as well there is another place.
    _, (rival, _, _) = _best(block, page[:j] + "\0" * (end - j) + page[end:])
    return pairs, top, rival


def corrections(block: str, page: str) -> dict[int, str]:
    """The block's positions the transcription corrects, with the character it has there.

    Punctuation the OCR read has no counterpart among the page's letters and takes no part.
    """
    return {i: page[j] for i, j in placed(block, page).items()}


def placed(block: str, page: str) -> dict[int, int]:
    """The block's positions the transcription corrects, with the page index of its character there."""
    kept = [i for i, c in enumerate(block) if readable(c)]
    text = "".join(block[i] for i in kept)
    if len(text) < MIN_MATCHES:
        return {}
    pairs, top, rival = align(text, page)
    same = [i is not None and j is not None and text[i] == page[j] for i, j in pairs]
    if sum(same) < max(MIN_MATCHES, len(text) / 2) or rival >= top:
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
            found.update({kept[i]: j for i, j in pairs[k:end]})
        k = end
    return found


def form_pair(written: str, transcribed: str) -> str | None:
    """How a transcription that writes `transcribed` where the ink was read as `written` may only have
    normalised it: `kana` for the other kana of one reading (は and ハ), `variant` for a 異体字 (嶋 and 島)."""
    from .. import refs

    if not written or not transcribed or written == transcribed:
        return None
    if refs.to_hiragana(written) == refs.to_hiragana(transcribed):
        return "kana"
    try:
        if transcribed in {other for other, _ in refs.variants(written)}:
            return "variant"
    except (KeyError, ValueError):
        pass
    return None


def normalises(old: str, new: str) -> bool:
    """Whether `new` is the form a transcription normalises `old` to: its 新字体 (國 to 国, 廣 to 広), or
    the common form the MJ table reduces it to where nothing reduces the other way (嶋 to 島, 髙 to 高)."""
    from .. import refs

    edges = [edge for edge in refs.variant_edges_of(old) if edge["written"] and {edge["a"], edge["b"]} == {old, new}]
    toward = {edge["relation"] for edge in edges if edge["a"] == old}
    back = {edge["relation"] for edge in edges if edge["a"] == new}
    return "shinjitai" in toward or ("reduction" in toward and "reduction" not in back)


def kana_script(character: str) -> str | None:
    code = ord(character)
    return "katakana" if 0x30A1 <= code <= 0x30FA else "hiragana" if 0x3041 <= code <= 0x3096 else None


def prior(written: str, transcribed: str, around: str) -> float:
    """The log odds the transcription's kana has over the written one from the script of the kana
    around it: katakana is rare in hiragana text and the other way round. Nothing for a 異体字."""
    if form_pair(written, transcribed) != "kana":
        return 0.0
    count = Counter(kana_script(c) for c in around)
    return math.log((count[kana_script(transcribed)] + 1) / (count[kana_script(written)] + 1))


def keeps_written(p_written: float | None, p_transcribed: float | None, odds: float) -> bool:
    """Whether a normalising correction keeps the written form: unless the ink looks more like the
    transcription's form, with the kana around it counted in, the form the OCR read stays."""
    if p_written is not None and p_transcribed is not None:
        odds += math.log((p_transcribed + FLOOR) / (p_written + FLOOR))
    return odds <= 0


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
        if not page:
            counts["no transcription"] += 1
            continue
        context = unicodedata.normalize("NFC", crops[0]["context"] or "")
        where = placed(context, page)
        fixed = {i: page[j] for i, j in where.items()}
        counts["corrected" if fixed else "unchanged"] += 1
        for crop in crops:
            source = crop["position"] + crop["offset"]
            if crop["id"] in protected or not 0 <= source < len(context) or crop["label"] != context[source]:
                continue
            target, text = fixed.get(source, crop["label"]), fixed.get(crop["position"], crop["text"])
            if target == crop["label"] and text == crop["text"]:
                continue
            item = {"unit_id": crop["id"], "before": crop["label"], "character": target, "text_before": crop["text"],
                    "text": text, "offset": crop["offset"], "block": list(key), "context": context}
            if form := form_pair(crop["label"], target):
                j = where[source]
                item |= {"form": form, "prior": round(prior(crop["label"], target, page[max(0, j - KANA_WINDOW):j]
                                                            + page[j + 1:j + 1 + KANA_WINDOW]), 4)}
            if crop["text"] and form_pair(crop["text"], text):
                item["text_form"] = True
            proposals.append(item)
    return proposals, counts


def run(dataset: Path, *, pages: Path, checkpoint: Path | None = None, apply: bool = False,
        protect: Iterable[str] = ()) -> dict:
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
        note = meta.get("alignment_repair") or {}
        # A settled label, a label two readings agree on, and a box held back as uncertain stay as they are.
        if str(unit.review) != "machine" or note.get("withheld") or (note.get("status") == "confirmed" and note.get("reliable")):
            protected.add(unit.id)
        relabel = meta.get("feedback_identity") or {}
        offset = int(relabel["offset"]) if relabel.get("method") in SHIFT_METHODS and relabel.get("offset") is not None else 0
        units[unit.id] = (unit, revision)
        blocks[(record.get("key"), record.get("page"), record.get("block"))].append(
            {"id": unit.id, "page_id": unit.page_id, "context": record.get("context"), "position": int(record["position"]),
             "offset": offset, "label": written_identity(unit), "text": unit.text_source})
    proposals, counts = propose(blocks, texts, protected)
    settle(store, proposals, checkpoint)
    # A correction that keeps the written form is reported, with what decided it, and changes nothing.
    kept = [item | {"status": "kept written form"} for item in proposals if item.get("written")]
    proposals = [item for item in proposals if item["character"] != item["before"] or item["text"] != item["text_before"]]
    counts["kept written form"] = len(kept)
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
    return {"method": METHOD, "blocks": len(blocks), "crops": len(units), "counts": dict(counts), "items": proposals,
            "kept": kept}


def ink(page, box, services: dict, *, client=None):
    """The crop at `box` of `page`, cut by the page's Image API service at full resolution: these pages
    are not held locally, and a region request is a few kilobytes."""
    import io

    from PIL import Image

    from .. import images, net

    service = images.service_of(page.image or "") if page is not None else None
    if service is None or box is None or not page.width:
        return None
    if service not in services:
        services[service] = images.info(service, client=client)
    described = services[service]
    scale = described["width"] / page.width
    region = ",".join(str(round(v * scale)) for v in (box.x, box.y, box.w, box.h))
    size = images._size_keyword(described["version"])
    with tempfile.TemporaryDirectory(prefix="ocr-repair-") as scratch:
        path = net.download(f"{service}/{region}/{size}/0/default.jpg", Path(scratch) / "crop", expected="image",
                            client=client)
        with Image.open(io.BytesIO(Path(path).read_bytes())) as picture:
            return picture.convert("L")


def settle(store, proposals: list[dict], checkpoint: Path | None) -> None:
    """Decide each normalising correction in place, a 異体字 by the way it normalises and a kana by the
    ink and the kana around it: one that keeps the written form keeps its label, and its text where the
    same reading gave both."""
    # The classifier learnt 異体字 from transcriptions that normalise them, and reads 嶋 ink as 島, so a
    # 異体字 is settled by which way the table normalises it: a transcription that writes the normalised
    # form of what the OCR read has normalised it, and one that writes the older form has copied the ink.
    for item in proposals:
        if item.get("form") == "variant" and not normalises(item["character"], item["before"]):
            item |= {"character": item["before"], "written": True}
    pending = [item for item in proposals if item.get("form") == "kana"]
    shown: dict[str, tuple[float | None, float | None]] = {}
    if pending and checkpoint is not None:
        import numpy as np

        from .. import refs
        from ..classify import preprocess
        from ..form_clusters import Encoder

        encoder = Encoder(checkpoint)
        index = {name: i for i, name in enumerate(encoder.classes)}
        services: dict = {}
        for item in pending:
            unit = store.unit(item["unit_id"])
            try:
                picture = ink(store.page(unit.page_id), unit.box, services)
            except Exception:  # noqa: BLE001 - a crop the service cannot cut is read as unknown
                picture = None
            if picture is None:
                continue
            row = encoder.classify(np.asarray(preprocess(picture, size=encoder.size), dtype=np.uint8)[None])[1][0]
            found = [index.get(refs.to_code_point(c)) for c in (item["before"], item["character"])]
            shown[item["unit_id"]] = tuple(None if i is None else float(row[i]) for i in found)
    for item in proposals:
        if item.get("form") == "kana":
            p_written, p_transcribed = shown.get(item["unit_id"], (None, None))
            item |= {"p_written": p_written, "p_transcribed": p_transcribed}
            if keeps_written(p_written, p_transcribed, item["prior"]):
                item |= {"character": item["before"], "written": True}
        # The text at the crop's own place follows its label when one reading gave both, and otherwise
        # keeps the written form, since only its own place's ink was read.
        if item.get("text_form") and (item["offset"] or item.get("written") or not item.get("form")):
            item["text"] = item["text_before"]


def write(result: dict, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n")
