"""Character catalogue, bounded image crops and durable visual review rounds."""

from __future__ import annotations

import io
import json
import logging
import random
import threading
import unicodedata
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from .. import images
from ..schema import Box, ReviewState, Unit
from . import status
from .store import BadRequest, ReviewRequest, Store

_IMAGE_SLOTS = threading.BoundedSemaphore(2)
CONFIRMED = {ReviewState.REVIEWED, ReviewState.DOUBLE_REVIEWED, ReviewState.ADJUDICATED}


def label(unit: Unit) -> str:
    return unicodedata.normalize("NFC", unit.reading or unit.text_source or "").strip()


def review_state(decision: str | None) -> str:
    if decision in CONFIRMED:
        return "checked"
    if decision == ReviewState.DISPUTED:
        return "flagged"
    return "pending"


def single_character(text: str) -> bool:
    bases = [c for c in text if not unicodedata.combining(c)
             and not 0xFE00 <= ord(c) <= 0xFE0F and not 0xE0100 <= ord(c) <= 0xE01EF]
    return len(bases) == 1


def character_group(unit: Unit) -> str:
    name = unicodedata.name(label(unit)[0], "") if label(unit) else ""
    if name.startswith(("HIRAGANA", "KATAKANA", "HENTAIGANA")):
        return "kana"
    return "kanji" if name.startswith("CJK") else "other"


@lru_cache(maxsize=3)
def decoded_image(path: str, stamp: int) -> Image.Image:
    """Reuse nearby crops without repeatedly decoding the same manuscript image."""
    with Image.open(path) as image:
        return image.convert("RGB")


@lru_cache(maxsize=256)
def thumbnail(path: str, stamp: int, box: tuple[float, ...] | None, context: bool) -> bytes:
    """Two workers share a cache of three source images and 256 small JPEG crops."""
    with _IMAGE_SLOTS:
        image = decoded_image(path, stamp)
        if box:
            x, y, w, h = box
            margin = max(w, h) * (0.65 if context else 0.08)
            bounds = (max(0, int(x - margin)), max(0, int(y - margin)),
                      min(image.width, int(x + w + margin)), min(image.height, int(y + h + margin)))
            if bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
                raise ValueError("The crop falls outside the image.")
            image = image.crop(bounds)
        else:
            image = image.copy()
        image.thumbnail((640, 640) if context else (240, 280), Image.Resampling.LANCZOS)
        output = io.BytesIO()
        image.save(output, format="JPEG", quality=88)
        return output.getvalue()


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    revision: int = Field(ge=0)
    image_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    verdict: Literal["match", "wrong", "unsure"]
    issue: Literal["reading", "crop", "merged", "blank", "unclear", "other"] | None = None
    correction: str | None = Field(default=None, max_length=32)


class Round(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    client_id: str = Field(min_length=1, max_length=128)
    label: str = Field(min_length=1, max_length=32)
    answers: list[Answer] = Field(min_length=1, max_length=24)


class Undo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_id: str = Field(min_length=1, max_length=128)


class CharacterEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    client_id: str = Field(min_length=1, max_length=128)
    revision: int = Field(ge=0)
    image_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    reading: str | None = Field(default=None, min_length=1, max_length=32)
    verdict: Literal["match", "wrong", "unsure"]
    issue: Literal["reading", "crop", "merged", "blank", "unclear", "other"] = "reading"
    correction: str | None = Field(default=None, max_length=32)
    note: str = Field(default="", max_length=2000)
    box: Box | None = None


def router(store: Store) -> APIRouter:
    from .server import cached_image

    api = APIRouter()
    image_root = images.images_root()
    inference_failures: set[str] = set()

    @lru_cache(maxsize=2)
    def url_index(stamp: int) -> dict:
        return {record.url: record for record in images.index(image_root) if not record.superseded_by}

    def index_stamp() -> int:
        path = image_root / "index.parquet"
        return path.stat().st_mtime_ns if path.exists() else 0

    @lru_cache(maxsize=512)
    def page_image(page_id: str, stamp: int) -> tuple[Path, float, float] | None:
        page = store.page(page_id)
        if page is None:
            return None
        if page.sha256:
            path = cached_image(page.sha256)
            if path:
                return path, 1, 1
        record = url_index(stamp).get(page.image)
        if record:
            path = cached_image(record.sha256)
            if path:
                return path, record.width / page.width, record.height / page.height
        return None

    def image_source(unit: Unit) -> tuple[Path, tuple[float, ...] | None] | None:
        if unit.crop_sha256:
            path = cached_image(unit.crop_sha256)
            if path:
                return path, None
        source = page_image(unit.page_id, index_stamp()) if unit.page_id else None
        if source and unit.box:
            path, sx, sy = source
            b = unit.box
            return path, (b.x * sx, b.y * sy, b.w * sx, b.h * sy)
        return None

    def eligible(unit: Unit) -> bool:
        return unit.active and single_character(label(unit)) and bool(
            unit.crop_sha256 or (unit.page_id and unit.box and unit.box.w > 0 and unit.box.h > 0)
        ) and image_source(unit) is not None

    def item(unit: Unit, revision: int, state: str | None = None) -> dict:
        if state is None:
            standing = status.unit_reviews([unit], store.events())[unit.id]
            state = review_state(standing.human_review)
        source = image_source(unit)
        # Page-backed crops share a source checksum; revision and box pin the crop itself.
        digest = source[0].stem if source else None
        return {"id": unit.id, "label": label(unit), "reading": unit.reading,
                "script": unit.script, "jibo": unit.jibo, "revision": revision,
                "state": state, "page_id": unit.page_id, "line_id": unit.line_id,
                "box": unit.box.model_dump() if unit.box else None,
                "image_sha256": digest,
                "image": f"/atlas/characters/{quote(unit.id, safe='')}/image?revision={revision}"
                         + (f"&image_sha256={digest}" if digest else "")}

    def one(unit_id: str) -> tuple[Unit, int]:
        records = store.unit_snapshot(unit_id)
        if not records or not records[0][0].active:
            raise HTTPException(404, "This character is no longer available.")
        return records[0]

    def snapshot(unit: Unit, revision: int) -> dict:
        page = store.page(unit.page_id) if unit.page_id else None
        document_id = unit.document_id or (page.document_id if page else None)
        document = store.document(document_id) if document_id else None
        source = image_source(unit)
        return {"character": item(unit, revision), "source_refs": document.source_refs if document else {},
                "canvas": page.canvas if page else None, "page_index": page.seq if page else None,
                "image_sha256": source[0].stem if source else None}

    @api.get("/atlas")
    def catalogue(
        reading: str | None = None,
        group: Literal["all", "kana", "kanji"] = "all",
        state: Literal["all", "pending", "checked", "flagged"] = "all",
        seed: int = 0,
        limit: Annotated[int, Query(ge=1, le=96)] = 60,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> dict:
        records = [(u, rev) for u, rev in store.unit_snapshot() if eligible(u)]
        standing = status.unit_reviews([u for u, _ in records], store.events())
        states = {key: review_state(value.human_review) for key, value in standing.items()}
        categories: dict[str, Counter] = {}
        for unit, _ in records:
            counts = categories.setdefault(label(unit), Counter())
            counts["total"] += 1
            counts[states[unit.id]] += 1
        counts = Counter(states[u.id] for u, _ in records)
        selected = [(u, rev) for u, rev in records if (reading is None or label(u) == reading)
                    and (group == "all" or character_group(u) == group)
                    and (state == "all" or states[u.id] == state)]
        random.Random(seed).shuffle(selected)
        return {"total": len(selected), "available": len(records), "counts": dict(counts),
                "categories": [{"label": name, **{key: c[key] for key in
                                  ("total", "pending", "checked", "flagged")}}
                               for name, c in sorted(categories.items(), key=lambda x: (-x[1]["total"], x[0]))],
                "items": [item(u, rev, states[u.id]) for u, rev in selected[offset:offset + limit]]}

    @api.get("/atlas/characters/{unit_id}")
    def character(unit_id: str) -> dict:
        unit, revision = one(unit_id)
        result = item(unit, revision)
        line = store.line(unit.line_id) if unit.line_id else None
        page = store.page(unit.page_id) if unit.page_id else None
        document = store.document(unit.document_id or page.document_id) if (unit.document_id or page) else None
        result.update({"context_image": result["image"] + "&context=true",
                       "text": line.text if line else "", "source": document.title if document else "",
                       "page_number": page.seq + 1 if page else None,
                       "line": line.model_dump(mode="json") if line else None})
        source = image_source(unit)
        result["context_box"] = None
        if source and source[1] and page:
            x, y, w, h = (unit.box.x, unit.box.y, unit.box.w, unit.box.h)
            margin = max(w, h) * 0.65
            left, top = max(0, int(x - margin)), max(0, int(y - margin))
            result["context_box"] = {"x": left, "y": top,
                                     "w": min(page.width, int(x + w + margin)) - left,
                                     "h": min(page.height, int(y + h + margin)) - top}
        return result

    @api.get("/atlas/characters/{unit_id}/image")
    def crop(unit_id: str, revision: int, context: bool = False,
             image_sha256: str | None = None) -> Response:
        unit, current = one(unit_id)
        if revision != current:
            raise HTTPException(409, "This crop changed. Reload the character.")
        source = image_source(unit)
        if source is None:
            raise HTTPException(404, "The character image is not cached.")
        path, box = source
        if image_sha256 is not None and image_sha256 != path.stem:
            raise HTTPException(409, "The source image changed. Reload this character.")
        try:
            content = thumbnail(str(path), path.stat().st_mtime_ns, box, context)
        except (OSError, ValueError, Image.DecompressionBombError):
            raise HTTPException(422, "The character image could not be opened.") from None
        return Response(content, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})

    @api.get("/lines/{line_id}")
    def line_record(line_id: str) -> dict:
        line = store.line(line_id)
        if line is None:
            raise HTTPException(404, "This line is unavailable.")
        return {**line.model_dump(mode="json"), "revision": store.revision(line_id)}

    @api.get("/atlas/characters/{unit_id}/suggestions")
    def suggestions(unit_id: str, revision: int, image_sha256: str) -> dict:
        unit, current = one(unit_id)
        source = image_source(unit)
        if current != revision or not source or source[0].stem != image_sha256:
            raise HTTPException(409, "This crop changed. Reload the character.")
        try:
            from .suggestions import infer
            path, box = source
            result = infer(str(path), path.stat().st_mtime_ns, box)
        except Exception as error:  # noqa: BLE001 — optional inference must not block review
            kind = type(error).__name__
            if kind not in inference_failures:
                inference_failures.add(kind)
                # Log a diagnostic once without exception text, which can contain private paths.
                logging.getLogger(__name__).warning("OCR suggestions unavailable (%s)", kind)
            return {"status": "unavailable", "candidates": [], "engines": []}
        return {**result, "revision": revision, "image_sha256": image_sha256}

    def correction_text(value: str | None, issue: str | None) -> str | None:
        text = unicodedata.normalize("NFC", value.strip()) if value else None
        if text and (not single_character(text) and issue != "merged"):
            raise BadRequest("Choose one character, or report joined characters.")
        if text and issue not in ("reading", "merged"):
            raise BadRequest("A reading suggestion belongs to a reading or joined-character issue.")
        return text

    def repeat(previous: list[dict]) -> dict:
        return {"results": [{**r, "duplicate": True} for r in previous]}

    @api.post("/atlas/rounds")
    def submit(round: Round) -> dict:
        if len({answer.id for answer in round.answers}) != len(round.answers):
            raise BadRequest("A character can appear only once in a round.")
        prefix = f"quiz:{round.id}:"
        previous = store.submission_results(round.client_id, prefix)
        if previous:
            reviews = [r for r in previous if r["field"] == "review"]
            if {r["target_id"] for r in reviews} != {a.id for a in round.answers}:
                raise BadRequest("This round was already submitted with different characters.")
            for answer in round.answers:
                old = json.loads(next(r for r in reviews if r["target_id"] == answer.id)["review"]["evidence"])
                if (old["label"] != round.label or old["verdict"] != answer.verdict
                        or old.get("issue") != answer.issue
                        or old.get("suggested_reading") != correction_text(answer.correction, answer.issue)
                        or old["snapshot"]["image_sha256"] != answer.image_sha256):
                    raise BadRequest("This round was already saved with different answers.")
            return {"id": str(round.id), **repeat(previous)}
        requests = []
        for answer in round.answers:
            unit, revision = one(answer.id)
            if not eligible(unit):
                raise BadRequest("This character has no available crop to review.")
            if answer.image_sha256 != image_source(unit)[0].stem:
                raise HTTPException(409, "The source image changed. Reload this round.")
            if label(unit) != round.label:
                raise HTTPException(409, "A character's reading changed. Reload this round.")
            correction = correction_text(answer.correction, answer.issue)
            if answer.verdict == "match" and (answer.issue or correction):
                raise BadRequest("A matching character cannot also have an unresolved issue.")
            resolved = bool(answer.issue == "reading" and correction and single_character(correction))
            if resolved and correction == round.label:
                raise BadRequest("Choose a different reading or mark the character as matching.")
            base = answer.revision
            evidence = json.dumps({"kind": "visual-quiz", "round": str(round.id),
                                   "label": round.label, "verdict": answer.verdict, "issue": answer.issue,
                                   "suggested_reading": correction,
                                   "snapshot": snapshot(unit, revision),
                                   "correction": {"reading": correction if resolved else label(unit),
                                                  "box": unit.box.model_dump() if unit.box else None}},
                                  ensure_ascii=False)
            if resolved:
                requests.append(ReviewRequest(
                    target_type="unit", target_id=answer.id, field="reading", new=correction,
                    base_revision=base, client_id=round.client_id,
                    idempotency_key=prefix + answer.id + ":reading", evidence=evidence,
                ))
                base += 1
            requests.append(ReviewRequest(
                target_type="unit", target_id=answer.id, field="review",
                new="reviewed" if answer.verdict == "match" or resolved else "disputed",
                base_revision=base, client_id=round.client_id,
                idempotency_key=prefix + answer.id, evidence=evidence,
            ))
        return {"id": str(round.id), "results": store.record_batch(requests)}

    @api.post("/atlas/rounds/{round_id}/undo")
    def undo(round_id: UUID, request: Undo) -> dict:
        previous = store.submission_results(request.client_id, f"quiz:{round_id}:")
        if not previous:
            raise HTTPException(404, "No saved round belongs to this reviewer.")
        revisions = {r["target_id"]: r["revision"] for r in previous}
        requests = []
        for r in reversed(previous):
            target = r["target_id"]
            requests.append(ReviewRequest(
                target_type="unit", target_id=target, field=r["field"], new=r["review"]["old"],
                base_revision=revisions[target], client_id=request.client_id,
                idempotency_key=f"quiz-undo:{round_id}:{r['id']}", evidence=f"undo of {r['id']}",
            ))
            revisions[target] += 1
        return {"id": str(round_id), "results": store.record_batch(requests)}

    @api.post("/atlas/characters/{unit_id}")
    def edit(unit_id: str, edit: CharacterEdit) -> dict:
        unit, current_revision = one(unit_id)
        correction = correction_text(edit.correction, edit.issue)
        supplied = unicodedata.normalize("NFC", edit.reading.strip()) if edit.reading else None
        if supplied is not None and not single_character(supplied):
            raise BadRequest("Use one character for the reading, or report joined characters.")
        previous = store.submission_results(edit.client_id, f"edit:{edit.id}:")
        if previous:
            old = json.loads(next(r for r in previous if r["field"] == "review")["review"]["evidence"])
            if old.get("request") != edit.model_dump(mode="json"):
                raise BadRequest("This edit was already saved with different values.")
            return repeat(previous)
        if not eligible(unit):
            raise BadRequest("This character has no available crop to review.")
        if edit.image_sha256 != image_source(unit)[0].stem:
            raise HTTPException(409, "The source image changed. Reload this character.")
        resolved = bool(edit.issue == "reading" and correction and single_character(correction))
        reading = correction if resolved else supplied or label(unit)
        requests = []
        revision = edit.revision
        if edit.box is not None:
            page = store.page(unit.page_id) if unit.page_id else None
            if (not page or edit.box.w <= 0 or edit.box.h <= 0 or edit.box.x < 0 or edit.box.y < 0
                    or edit.box.x + edit.box.w > page.width or edit.box.y + edit.box.h > page.height):
                raise BadRequest("The crop must stay inside the source image.")
            requests.append(ReviewRequest(
                target_type="unit", target_id=unit_id, field="box", new=edit.box.model_dump(),
                base_revision=revision, client_id=edit.client_id, idempotency_key=f"edit:{edit.id}:box",
                evidence=edit.note or "Crop adjusted in character review",
            ))
            revision += 1
        if reading != label(unit):
            requests.append(ReviewRequest(
                target_type="unit", target_id=unit_id, field="reading", new=reading,
                base_revision=revision, client_id=edit.client_id, idempotency_key=f"edit:{edit.id}:reading",
                evidence=edit.note or "Character review",
            ))
            revision += 1
        evidence = json.dumps({"kind": "character-review", "verdict": edit.verdict,
                               "issue": edit.issue, "note": edit.note, "suggested_reading": correction,
                               "request": edit.model_dump(mode="json"),
                               "snapshot": snapshot(unit, current_revision),
                               "correction": {"reading": reading, "box": edit.box.model_dump()
                                              if edit.box else unit.box.model_dump() if unit.box else None}},
                              ensure_ascii=False)
        requests.append(ReviewRequest(
            target_type="unit", target_id=unit_id, field="review",
            new="reviewed" if edit.verdict == "match" or resolved else "disputed",
            base_revision=revision, client_id=edit.client_id, idempotency_key=f"edit:{edit.id}:review",
            evidence=evidence,
        ))
        return {"results": store.record_batch(requests)}

    @api.get("/atlas/reviews")
    def export_reviews() -> dict:
        all_events = store.events()
        latest = {e.target_id: e.id for e in all_events if e.field == "review"}
        events = [e for e in all_events if e.field == "review" and e.evidence and
                  ('"kind": "visual-quiz"' in e.evidence or '"kind": "character-review"' in e.evidence)]
        records = {u.id: (u, revision) for u, revision in store.unit_snapshot()}
        output = []
        for event in events:
            record = records.get(event.target_id)
            if not record:
                continue
            unit, revision = record
            evidence = json.loads(event.evidence)
            observed = evidence.get("snapshot")
            correction = evidence.get("correction", {})
            expected_label = correction.get("reading", evidence.get("label"))
            expected_box = correction.get("box", observed["character"]["box"] if observed else None)
            image = image_source(unit)
            current = bool(observed and latest.get(unit.id) == event.id and label(unit) == expected_label
                           and image and image[0].stem == observed["image_sha256"]
                           and (unit.box.model_dump() if unit.box else None) == expected_box)
            output.append({"event": event.model_dump(mode="json"), "reviewed": observed,
                           "current": current, "current_revision": revision})
        return {"version": 1, "kind": "atlas-character-reviews", "reviews": output}

    return api
