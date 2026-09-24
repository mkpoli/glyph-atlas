"""Apply saved character decisions and conservatively split joined detections.

The journal is append-only. Human-selected text remains attached to its original
review; inferred boundaries and labels retain model provenance.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter

from PIL import Image

from .. import refs
from ..feedback import QUARANTINE_IMPLICIT_QUIZ_MATCH, normalize_export
from ..schema import Box
from ..split_proposals import Limits, propose_split
from .atlas import identity_text, script_of_identity, single_character
from .characters import _source_digest, written_identity
from .receipts import fingerprint
from .store import BadRequest, Conflict, ReviewRequest, Store

POLICY = "feedback-extraction-v1"


def encoded(text: str) -> str:
    return " ".join(refs.to_code_point(c) for c in text)


def crop_for(store: Store, unit) -> tuple[Image.Image, str]:
    """Use the same immutable page/crop and coordinate scale as the viewer."""
    from .server import cached_image

    digest = _source_digest(store, unit)
    path = cached_image(digest) if digest else None
    if path is None:
        raise ValueError("source image unavailable")
    with Image.open(path) as image:
        source = image.convert("RGB")
    if unit.crop_sha256:
        raise ValueError("a standalone crop cannot be split into page coordinates")
    page = store.page(unit.page_id) if unit.page_id else None
    if page is None or unit.box is None or not page.width or not page.height:
        raise ValueError("page geometry unavailable")
    # Until subpixel geometry is supported, refuse a reduced cached image instead
    # of silently applying cuts in the wrong coordinate system.
    if source.size != (page.width, page.height):
        raise ValueError("cached image and page use different coordinate scales")
    b = unit.box
    if b.x < 0 or b.y < 0 or b.x + b.w > source.width or b.y + b.h > source.height:
        raise ValueError("crop extends beyond the source image")
    return source.crop((b.x, b.y, b.x + b.w, b.y + b.h)), digest


class SplitEngine:
    """A bounded sequence check followed by independently recognized child crops."""

    def __init__(self, model=None):
        if model is None:
            from .suggestions import recognizer
            model = recognizer()
        self.model = model

    def score(self, crops):
        # PARSeq scores candidate boundaries within one model. A classifier's .99 and
        # PARSeq's .98 are not comparable probabilities and must not compete by max().
        results = []
        for crop in crops:
            result = self.model.read(crop)
            votes = result.get("votes", result["candidates"])
            best = next((c for c in votes if c["engine"] == "NDLkotenOCR"), None)
            results.append({encoded(best["text"]): best["score"]}
                           if best and best.get("text") and single_character(best["text"]) else {})
        return results

    def assess_unit(self, unit, crop: Image.Image, expected: str | None = None) -> dict:
        return self.assess(crop, expected, identity=unit.unicode)

    def _blank_gap_fallback(self, crop, expected, rejected):
        """A saved sequence may be clearer as children than as a joined image.

        Keep the existing strict blank-gap and independent-child thresholds. A
        weak whole-crop guess alone never supplies the expected text here.
        """
        if not expected:
            return rejected
        from ..partial_split import propose_partial

        proposal = propose_partial(crop, expected, self.model.read)
        if not proposal["accepted"]:
            return rejected
        return {**rejected, **proposal, "whole_crop_assessment": rejected,
                "boundary_model": "independently-recognized-blank-gap"}

    def assess(self, crop: Image.Image, expected: str | None = None, *, identity: str | None = None) -> dict:
        from ..split_proposals import LigatureCheck, default_ligature_guard
        from .suggestions import LOCK
        if identity and refs.ligature(identity):
            return {"accepted": False, "reason": "the parent is an encoded ligature", "identity": identity}
        # With no encoded identity the ordinary splitter must consult its reading
        # ligature guard; the fallback cannot bypass that check.
        fallback_text = expected if identity else None
        with LOCK:
            result = self.model.read(crop)
            partition = result.get("symbol_partition")
            if partition:
                text = "".join(partition.get("text", []))
                if partition["accepted"] and (not expected or expected == text):
                    return {**partition, "engines": result["engines"]}
                # Do not let a whole-crop OCR guess silently discard a positively
                # identified mark, including when the saved suggestion omitted it.
                return {**partition, "accepted": False, "engines": result["engines"],
                        "reason": "saved text omits or disagrees with the separated symbol"
                        if partition["accepted"] else partition["reason"]}
            sequence = next((c for c in result["candidates"] if c["engine"] == "NDLkotenOCR"), None)
            evidence = {"engines": result["engines"], "sequence": sequence}
            if not sequence or not 2 <= len(sequence["text"]) <= 4:
                return self._blank_gap_fallback(crop, fallback_text,
                    {"accepted": False, "reason": "no joined sequence recognized", **evidence})
            if expected and sequence["text"] != expected:
                # A contradictory whole-crop reading stays unresolved even if a
                # few small pieces happen to resemble the requested characters.
                return {"accepted": False, "reason": "sequence OCR disagrees with the saved decision", **evidence}
            if sequence["score"] < (.90 if expected else .98):
                return self._blank_gap_fallback(crop, fallback_text,
                    {"accepted": False, "reason": "sequence OCR is uncertain", **evidence})
            proposal = propose_split(
                crop, expected or sequence["text"], scorer=self.score,
                ligature=(lambda _: LigatureCheck()) if identity else default_ligature_guard,
                candidates_of=lambda c: [encoded(c)],
                limits=Limits(min_character_mass=.85, min_margin=.7),
            )
            # Keep the classifier's alternatives as separate evidence for review.
            child_models = [self.model.read(child).get("votes", []) for child in proposal.crops(crop)]
            assessed = {**proposal.model_dump(mode="json"), **evidence,
                        "boundary_model": "NDLkotenOCR", "child_models": child_models}
            return assessed if assessed["accepted"] else self._blank_gap_fallback(crop, fallback_text, assessed)


def _changes(store, unit, values, evidence, *, base_revision, role="model"):
    revision = base_revision
    body = json.dumps(evidence, ensure_ascii=False, sort_keys=True)
    key = hashlib.sha256(json.dumps({"unit_id": unit.id, "evidence": evidence, "changes": values},
                                    ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:24]
    requests = []
    for field, value in values.items():
        old = getattr(unit, field, None)
        if hasattr(old, "model_dump"):
            old = old.model_dump(mode="json")
        if old == value:
            continue
        requests.append(ReviewRequest(
            target_id=unit.id, field=field, new=value, base_revision=revision,
            client_id=POLICY, idempotency_key=f"{key}:{field}", evidence=body,
        ))
        revision += 1
    return store.record_batch(requests, role=role) if requests else []


def _collision(boxes, others):
    for box in boxes:
        for other in others:
            if not other.active or not other.box:
                continue
            b = other.box
            area = max(0, min(box.x + box.w, b.x + b.w) - max(box.x, b.x)) * max(
                0, min(box.y + box.h, b.y + b.h) - max(box.y, b.y))
            if area / max(1, min(box.w * box.h, b.w * b.h)) > .65:
                return other.id
    return None


def _reuse_existing_child(store, unit, assessment, boxes, others, *, base_revision, source_event_id,
                         source_event_fingerprint=None):
    """Trim a joined parent when its other child already exists at the same ink.

    This only handles a two-character, human-selected sequence whose remaining
    child is the parent's existing identity. Existing occurrences are untouched.
    Every overlap must be an enclosing, identically labelled child; conflicting
    labels, crossing boxes, and multiple existing occurrences are refused.
    """
    if not source_event_id or len(boxes) != 2 or len(assessment.get("text", [])) != 2:
        return None

    def overlaps(a, b):
        return a.x < b.x + b.w and b.x < a.x + a.w and a.y < b.y + b.h and b.y < a.y + a.h

    reused, uncovered = [], []
    for index, (box, text) in enumerate(zip(boxes, assessment["text"], strict=True)):
        hits = [other for other in others if other.active and other.box and overlaps(box, other.box)]
        if not hits:
            uncovered.append(index)
            continue
        if len(hits) != 1:
            return None
        existing = hits[0]
        b = existing.box
        if (written_identity(existing) != text or b.x < box.x or b.y < box.y
                or b.x + b.w > box.x + box.w or b.y + b.h > box.y + box.h
                or b.w * b.h < .45 * box.w * box.h):
            return None
        reused.append({"id": existing.id, "revision": store.revision(existing.id),
                       "box": b.model_dump(), "character": text})
    if len(reused) != 1 or len(uncovered) != 1:
        return None
    index = uncovered[0]
    if assessment["text"][index] != written_identity(unit):
        return None
    evidence = {"kind": "feedback-reconciliation", "policy": POLICY,
                "result": "recropped", "source_event_id": source_event_id,
                "source_event_fingerprint": source_event_fingerprint,
                "source_image_sha256": _source_digest(store, unit), "parent_box": unit.box.model_dump(),
                "reused_children": reused, "assessment": assessment, "automated": True,
                "applied_upstream": False}
    meta = {key: value for key, value in unit.meta.items()
            if key not in ("alignment_repair", "segmentation_scan", "feedback_repair")}
    meta["feedback_repair"] = evidence
    meta["alignment_repair"] = {"status": "applied", "machine": True, "verified": False,
        "reliable": True, "withheld": False, "quiz": True,
        "reason": "measured blank gap separated a neighbouring character already represented by its own crop"}
    results = _changes(store, unit, {"box": boxes[index].model_dump(), "review": "machine", "meta": meta},
                       evidence, base_revision=base_revision)
    return {"status": "recropped", "box": boxes[index].model_dump(), "reused_children": reused,
            "events": len(results)}


def split_unit(store, unit, assessment, *, base_revision, source_event_id=None, source_event_fingerprint=None):
    from ..split_proposals import to_page

    boxes = to_page([Box(**b) for b in assessment["boxes"]], unit.box)
    others = [u for u in store.iter_units() if u.page_id == unit.page_id and u.id != unit.id]
    collision = _collision(boxes, others)
    if collision:
        reused = _reuse_existing_child(store, unit, assessment, boxes, others,
                                       base_revision=base_revision, source_event_id=source_event_id,
                                       source_event_fingerprint=source_event_fingerprint)
        if reused:
            return reused
        return {"status": "withheld", "reason": "child would duplicate another occurrence", "overlap": collision}
    from ..unit_scope import character_count

    entries = [{"box": b.model_dump(), "unicode": encoded(char), "reading": char,
                "text_source": char, "script": script_of_identity(char),
                "kind": "char" if character_count(char) == 1 else "sequence",
                "granularity": "char" if character_count(char) == 1 else "sequence"} for b, char in zip(boxes, assessment["text"], strict=True)]
    evidence = {"kind": "feedback-split", "policy": POLICY, "source_event_id": source_event_id,
                "source_event_fingerprint": source_event_fingerprint,
                "source_image_sha256": _source_digest(store, unit), "parent_box": unit.box.model_dump(),
                "human_selected_text": bool(source_event_id), "assessment": assessment}
    try:
        results = _changes(store, unit, {"segmentation": {"split": entries}}, evidence,
                           base_revision=base_revision, role="model")
    except BadRequest:
        return {"status": "withheld", "reason": "split conflicts with existing page geometry"}
    return {"status": "split", "children": store.unit(unit.id).split_into, "events": len(results)}


def refine_feedback(store: Store, payload: dict, *, apply=False, engine=None, mark_unresolved=True) -> dict:
    """Validate feedback against current pixels before recording a derived change."""
    feedback = normalize_export(payload)
    source_rows = {r["event"]["id"]: r for r in payload.get("reviews", [])}
    events = {e.id: e for e in store.events()}
    latest = {e.target_id: e.id for e in events.values() if e.field == "review"}
    output = []
    for f in feedback:
        if f.door != "local" or not f.current or not f.trusted_human:
            continue
        event_fingerprint = fingerprint(source_rows[f.event_id])
        item = {"event_id": f.event_id, "unit_id": f.source_unit_id, "decision": f.decision,
                "event_fingerprint": event_fingerprint}
        output.append(item)
        unit = store.unit(f.source_unit_id)
        if not unit or not unit.active:
            item["status"] = "retired"
            continue
        previous = unit.meta.get("feedback_repair", {})
        if (previous.get("source_event_id") == f.event_id and previous.get("policy") == POLICY
                and previous.get("source_event_fingerprint") == event_fingerprint
                and previous.get("result") in ("resolved", "recropped", "unconfirmed")):
            item["status"] = "already-processed"
            continue
        original = events.get(f.event_id)
        expected_revision = source_rows[f.event_id].get("current_revision")
        latest_event = events.get(latest.get(unit.id))
        try:
            latest_evidence = json.loads(latest_event.evidence or "{}") if latest_event else {}
        except (ValueError, TypeError):
            latest_evidence = {}
        continuation = bool(isinstance(latest_evidence, dict)
                            and latest_evidence.get("kind") == "feedback-reconciliation"
                            and latest_evidence.get("source_event_id") == f.event_id)
        if (not isinstance(expected_revision, int) or not original
                or (latest.get(unit.id) != f.event_id and not continuation)
                or (expected_revision is not None and store.revision(unit.id) != expected_revision)
                or original.model_dump(mode="json") != source_rows[f.event_id]["event"]
                or not f.page_hash or _source_digest(store, unit) != f.page_hash
                or (unit.box.model_dump() if unit.box else None) != f.source_box):
            item.update(status="stale", reason="the reviewed occurrence has changed")
            continue
        evidence = {"kind": "feedback-reconciliation", "policy": POLICY,
                    "source_event_id": f.event_id, "source_actor": f.actor,
                    "source_event_fingerprint": event_fingerprint,
                    "source_image_sha256": f.page_hash, "decision": f.decision,
                    "automated": True, "applied_upstream": False, "issue": f.issue}
        values = {}
        if QUARANTINE_IMPLICIT_QUIZ_MATCH in f.quarantine_reasons:
            # Preserve the original batch event but retract its inferred positive.
            # Snapshot/revision checks above protect later deliberate decisions.
            values["review"] = "machine"
            item["status"] = "unconfirmed"
            evidence["reason"] = "unselected quick-review crops were never explicitly confirmed"
        elif f.decision == "accepted-identity":
            proposed = f.proposed_text or f.effective_text
            # A reviewer may type the identity as code points; U+30C4 U+309A is ツ゚.
            proposed = identity_text(proposed) if proposed else proposed
            # One character, counted as the character layer counts it: a base and
            # its mark is one identity, however many code points it is written with.
            if not proposed or not single_character(proposed) or written_identity(unit) not in (f.original_identity, proposed):
                item["status"] = "stale"
                continue
            values.update(unicode=encoded(proposed), review="reviewed")
            script = script_of_identity(proposed)
            if script != "unknown":
                values["script"] = script
            evidence["character"] = proposed
            item["status"] = "resolved"
        elif f.decision == "accepted-match":
            item["status"] = "confirmed"
            continue
        elif f.issue in ("merged", "crop"):
            item["status"] = "withheld"
            values["review"] = "disputed"
            repair = {**unit.meta.get("alignment_repair", {}), "status": "uncertain",
                      "quiz": False, "withheld": True, "reliable": False,
                      "reason": "saved crop issue awaiting extraction repair"}
            values["meta"] = {**unit.meta, "alignment_repair": repair}
            if f.issue == "merged":
                try:
                    crop, _ = crop_for(store, unit)
                    engine = engine or SplitEngine()
                    expected = f.proposed_text if f.decision == "joined" else None
                    assessment = (engine.assess_unit(unit, crop, expected) if hasattr(engine, "assess_unit")
                                  else engine.assess(crop, expected))
                    item["assessment"] = assessment
                    if assessment["accepted"]:
                        item["status"] = "split-proposed"
                        if apply:
                            # CAS must still name the snapshot inspected before inference.
                            if store.revision(unit.id) != expected_revision:
                                raise Conflict("review changed during inference")
                            item.update(split_unit(store, unit, assessment, base_revision=expected_revision,
                                                   source_event_id=f.event_id if f.decision == "joined" else None,
                                                   source_event_fingerprint=event_fingerprint))
                            if item["status"] in ("split", "recropped"):
                                continue
                except (ValueError, OSError) as error:
                    item["reason"] = f"crop could not be assessed ({type(error).__name__})"
        else:
            item["status"] = "unchanged"
            continue
        if item["status"] == "withheld" and not mark_unresolved:
            continue
        meta = values.get("meta", unit.meta)
        evidence["result"] = item["status"]
        values["meta"] = {**meta, "feedback_repair": {**evidence, "assessment": item.get("assessment")}}
        if apply:
            if store.revision(unit.id) != expected_revision:
                raise Conflict("review changed during inference")
            item["events"] = len(_changes(store, unit, values, evidence, base_revision=expected_revision))
    return {"policy": POLICY, "applied": apply, "counts": dict(Counter(i["status"] for i in output)),
            "items": output}


def scan_joined(store: Store, *, limit=64, apply=False, engine=None) -> dict:
    """Measure current crops and withhold confident joins before a review round."""
    from .preflight import apply_records, scan

    report = scan(store, limit=max(0, min(limit, 256)), engine=engine)
    result = report.model_dump(mode="json")
    if apply:
        result["application"] = apply_records(store, report.records, engine=engine,
                                               split_limit=max(0, min(limit, 256)))
    return result


def background_refine(store: Store, payload: dict) -> None:
    """A saved review remains durable even when optional OCR is unavailable."""
    import logging

    try:
        refine_feedback(store, payload, apply=True, mark_unresolved=False)
    except Exception as error:  # noqa: BLE001 — optional background inference
        logging.getLogger(__name__).warning("Feedback extraction deferred (%s)", type(error).__name__)


def repair_adjacent_labels(store: Store, *, limit=128, apply=False, engine=None) -> dict:
    """Use nearby transcription and strong OCR, rejecting classifier contradictions."""
    import random

    from .suggestions import LOCK

    human = {e.target_id for e in store.events() if e.role != "model"}
    rows = [(u, rev) for u, rev in store.unit_snapshot()
            if u.active and u.id not in human and u.box and u.line_id
            and str(u.review) in ("machine", "rejected") and str(u.kind) == "char"
            and not refs.ligature(u.unicode or "")
            and written_identity(u) == u.reading]
    random.Random(20260920).shuffle(rows)
    output = []
    for unit, revision in rows[:max(0, min(limit, 256))]:
        item = {"unit_id": unit.id, "status": "unchanged"}
        output.append(item)
        try:
            crop, digest = crop_for(store, unit)
            engine = engine or SplitEngine()
            with LOCK:
                result = engine.model.read(crop)
            tops = [next((c for c in result.get("votes", []) if c["engine"] == name), None)
                    for name in ("NDLkotenOCR", "Atlas classifier")]
            seq, char = tops
            if (not seq or not char or not seq["text"] or len(seq["text"]) != 1
                    or seq["text"] == unit.reading):
                continue
            agreed = (char.get("identity_scope", "character") == "character"
                      and seq["text"] == char["text"] and seq["score"] >= .98 and char["score"] >= .90)
            abstained = (char["text"] is None and char.get("identity_scope", "other") == "other"
                         and seq["score"] >= .99)
            if not (agreed or abstained):
                continue
            prediction = seq["text"]
            siblings = sorted(store.units_of_line(unit.line_id), key=lambda u: (u.seq or 0, u.id))
            index = next(i for i, u in enumerate(siblings) if u.id == unit.id)
            neighbours = [u for u in siblings[max(0, index - 2):index + 3] if u.id != unit.id
                          and written_identity(u) == prediction]
            if not neighbours:
                continue
            evidence = {"kind": "adjacent-label-repair", "policy": POLICY, "automated": True,
                        "source_image_sha256": digest, "box": unit.box.model_dump(),
                        "before": unit.reading, "character": prediction,
                        "neighbours": [u.id for u in neighbours], "recognition": result,
                        "basis": "model-agreement" if agreed else "sequence-and-neighbour-classifier-abstained"}
            item.update(status="identity-proposed", evidence=evidence)
            if apply:
                if _source_digest(store, unit) != digest:
                    item["status"] = "stale"
                    continue
                values = {"unicode": encoded(prediction), "reading": prediction,
                          "script": script_of_identity(prediction), "review": "machine",
                          "meta": {**unit.meta, "feedback_identity": evidence}}
                _changes(store, unit, values, evidence, base_revision=revision)
                item["status"] = "identity-repaired"
        except (ValueError, OSError):
            item["status"] = "unavailable"
    return {"policy": POLICY, "examined": len(output), "candidates": len(rows),
            "counts": dict(Counter(i["status"] for i in output)), "items": output}
