"""Import hosted reviews only when their published pixels and revision lineage still match."""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from copy import deepcopy

from .. import refs
from ..schema import Review
from .atlas import identity_text, label, single_character
from .characters import _source_digest, reading_is_allowed, written_identity
from .receipts import fingerprint
from .store import _change

REMOTE_ID = re.compile(r"cf:[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$", re.IGNORECASE)
POLICY = "cloudflare-import-v1"


class Rejected(ValueError):
    pass


class _PreviewRollback(Exception):
    pass


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _snapshot(record):
    evidence = json.loads(record["event"]["evidence"])
    snapshot = evidence["snapshot"]
    if snapshot != record.get("reviewed"):
        raise Rejected("event and exported snapshots disagree")
    return snapshot["character"], evidence


def _same_view(a, b):
    return all(a.get(key) == b.get(key) for key in
               ("id", "label", "reading", "box", "image_sha256", "page_id"))


def _answer(evidence, identity):
    request = evidence["request"]
    if evidence.get("kind") == "visual-quiz":
        answers = [item for item in request.get("answers", []) if item.get("id") == identity]
        if len(answers) != 1:
            raise Rejected("round does not identify exactly one answer")
        return answers[0]
    return request


def _step(record, before):
    event = record["event"]
    shown, evidence = _snapshot(record)
    revision = record.get("expected_revision")
    if (type(revision) is not int or revision != before["revision"]
            or shown.get("revision") != revision or not _same_view(shown, before)):
        raise Rejected("remote revision chain or reviewed pixels changed")
    if (event.get("target_type") != "unit" or event.get("field") != "review"
            or event.get("role") != "reviewer" or not event.get("actor")
            or event.get("target_id") != before["id"]):
        raise Rejected("unsupported remote event")
    request = evidence["request"]
    answer = _answer(evidence, before["id"])
    if (answer.get("revision") != revision or answer.get("image_sha256") != before["image_sha256"]
            or request.get("client_id") != event["actor"]):
        raise Rejected("request provenance disagrees with the reviewed occurrence")
    verdict, issue = answer.get("verdict"), answer.get("issue")
    if verdict != evidence.get("verdict") or issue != evidence.get("issue"):
        raise Rejected("request and saved decision disagree")
    if verdict not in ("match", "wrong") or (evidence.get("kind") == "visual-quiz" and verdict != "wrong"):
        raise Rejected("skipped or implicit round confirmations are not imported")
    if issue not in (None, "character", "reading", "merged", "crop", "blank", "other"):
        raise Rejected("unsupported issue")
    if verdict == "wrong" and not issue:
        raise Rejected("missing issue")
    written = identity_text(answer["character"]) if answer.get("character") else None
    if written and (not single_character(written) or verdict != "wrong"):
        raise Rejected("written identity does not match the decision")
    reading = answer.get("reading")
    if not reading and issue == "reading" and answer.get("correction") and single_character(answer["correction"]):
        reading = answer["correction"]
    after = {**before, "revision": revision + 1}
    if written:
        after["label"] = written
    if reading:
        if not reading_is_allowed(reading, " ".join(refs.to_code_points(after["label"]))):
            raise Rejected("reading is not allowed for this identity")
        after["reading"] = reading
    if answer.get("box"):
        raise Rejected("hosted geometry edits require a separate importer")
    correction = evidence.get("correction", {})
    if (correction.get("unicode") != " ".join(refs.to_code_points(after["label"]))
            or correction.get("reading") != after["reading"] or correction.get("box") != after["box"]):
        raise Rejected("saved effective state disagrees with the explicit correction")
    resolved = verdict == "match" or bool(issue == "character" and written) or bool(issue == "reading" and reading)
    if event.get("new") != ("reviewed" if resolved else "disputed"):
        raise Rejected("review certainty exceeds the saved decision")
    return after


def _append(store, conn, remote, field, value, evidence, event_id):
    event = Review(id=event_id, target_type="unit", target_id=remote["target_id"], field=field,
                   new=value, role="reviewer", actor=remote["actor"], evidence=evidence, at=remote["at"])
    state = store._state_for(conn, event)
    change = _change(state, event, guard=False)
    event = change.event.model_copy(update={"id": event_id})
    change.event = event
    conn.execute("""INSERT INTO events(id,target_type,target_id,field,old,new,role,actor,evidence,at,
                    client_id,idempotency_key) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                 (event.id, event.target_type, event.target_id, event.field, _json(event.old), _json(event.new),
                  event.role, event.actor, event.evidence, event.at.isoformat(), event.actor, event_id))
    store._persist(conn, state, change)
    store._bump(conn, event.target_id)
    result = store._build_result(conn, event, change, state)
    conn.execute("UPDATE events SET result=? WHERE id=?", (_json(result), event.id))
    store._set_meta(conn, "state_seq", str(store._last_seq(conn)))
    return event.model_dump(mode="json")


def ingest_cloudflare(store, payload: dict, *, apply=False) -> tuple[dict, dict]:
    """Return locally bound feedback plus an import report; previews write nothing.

    A current review needs an unbroken path from the immutable publication snapshot
    or a previous import. Superseded records supply lineage only. Missing undo steps
    and unrelated local changes are conflicts, even when the pixels still match.
    """
    output, groups, report = [], defaultdict(list), []
    for record in payload.get("reviews", []):
        event = record.get("event", {})
        if record.get("origin", "local") != "local" or not str(event.get("id", "")).startswith("cf:"):
            output.append(record)
        else:
            groups[event.get("target_id")].append(record)
    if not groups:
        return payload, {"counts": {}, "items": []}
    try:
        with store._lock, store._connection() as conn, store._transaction(conn):
            conn.execute("""CREATE TABLE IF NOT EXISTS cloudflare_imports(
                remote_id TEXT PRIMARY KEY,target_id TEXT NOT NULL,fingerprint TEXT NOT NULL,
                publication TEXT NOT NULL,remote_revision INTEGER NOT NULL,local_revision INTEGER NOT NULL,
                remote_state TEXT NOT NULL,local_record TEXT NOT NULL)""")
            for target, records in groups.items():
                current = [record for record in records if record.get("current") is True]
                if not current:
                    report.append({"unit_id": target, "status": "not-current"})
                    continue
                item = {"unit_id": target, "event_id": current[-1]["event"]["id"], "status": "rejected"}
                report.append(item)
                conn.execute("SAVEPOINT remote_review")
                try:
                    if len(current) != 1 or len({r["event"]["id"] for r in records}) != len(records):
                        raise Rejected("ambiguous remote history")
                    record = current[0]
                    remote = record["event"]
                    if not all(REMOTE_ID.fullmatch(r["event"]["id"]) for r in records):
                        raise Rejected("invalid remote event identity")
                    source_fingerprint = fingerprint(record)
                    item["event_fingerprint"] = source_fingerprint
                    publication = record.get("publication_snapshot")
                    if not isinstance(publication, dict) or not isinstance(publication.get("character"), dict):
                        raise Rejected("immutable publication snapshot is missing")
                    publication_key = _json(publication)
                    prior = conn.execute("SELECT * FROM cloudflare_imports WHERE remote_id=?", (remote["id"],)).fetchone()
                    if prior:
                        if prior["fingerprint"] != source_fingerprint or prior["publication"] != publication_key:
                            raise Rejected("remote event ID was reused with different content")
                        bound = json.loads(prior["local_record"])
                        output.append(bound)
                        item.update(status="duplicate", local_event_fingerprint=fingerprint(bound))
                        continue
                    if conn.execute("SELECT 1 FROM events WHERE id=?", (remote["id"],)).fetchone():
                        raise Rejected("remote event ID already exists outside this importer")
                    unit = store._unit_row(conn, target)
                    if unit is None or not unit.active:
                        raise Rejected("local occurrence is absent or retired")
                    previous = conn.execute("SELECT * FROM cloudflare_imports WHERE target_id=? ORDER BY local_revision DESC LIMIT 1",
                                            (target,)).fetchone()
                    if previous:
                        if previous["publication"] != publication_key:
                            raise Rejected("publication changed after the last import")
                        before = json.loads(previous["remote_state"])
                        if not conn.execute("SELECT 1 FROM events WHERE id=?", (previous["remote_id"],)).fetchone():
                            raise Rejected("the imported journal was reset")
                        events = conn.execute("SELECT * FROM events WHERE target_id=? AND seq>(SELECT seq FROM events WHERE id=?)",
                                              (target, previous["remote_id"])).fetchall()
                        if store._revision(conn, target) != previous["local_revision"] + len(events):
                            raise Rejected("local revision history changed after import")
                        for event in events:
                            evidence = json.loads(event["evidence"] or "{}")
                            if event["role"] != "model" or evidence.get("source_event_id") != previous["remote_id"]:
                                raise Rejected("a later local decision must be preserved")
                    else:
                        before = deepcopy(publication["character"])
                        if type(before.get("revision")) is not int or store._revision(conn, target) != before["revision"]:
                            raise Rejected("local revision differs from the published baseline")
                    actual = {"id": unit.id, "label": written_identity(unit), "reading": label(unit),
                              "box": unit.box.model_dump() if unit.box else None, "page_id": unit.page_id,
                              "image_sha256": _source_digest(store, unit)}
                    if not actual["image_sha256"] or not _same_view(actual, before):
                        raise Rejected("local pixels, geometry, or identity changed")
                    chain = sorted((r for r in records if type(r.get("expected_revision")) is int
                                    and r["expected_revision"] >= before["revision"]), key=lambda r: r["expected_revision"])
                    if not chain or chain[-1]["event"]["id"] != remote["id"]:
                        raise Rejected("current review has no complete remote lineage")
                    for link in chain:
                        if _json(link.get("publication_snapshot")) != publication_key:
                            raise Rejected("remote history mixes publication baselines")
                        before = _step(link, before)
                    if record.get("current_revision") != before["revision"]:
                        raise Rejected("the exported current revision is not the reviewed revision")
                    evidence = json.loads(remote["evidence"])
                    evidence["cloudflare_import"] = {"policy": POLICY, "publication": payload.get("publication"),
                        "remote_fingerprint": source_fingerprint, "remote_event": deepcopy(remote),
                        "remote_chain": [deepcopy(r["event"]) for r in chain], "publication_snapshot": publication}
                    # Hosted reading edits already distinguish pronunciation from
                    # identity. Do not run the normalizer's legacy identity inference.
                    evidence["layer"] = "review"
                    answer = _answer(evidence, target)
                    if evidence.get("kind") != "visual-quiz" and answer.get("character"):
                        answer["character"] = identity_text(answer["character"])
                    # A joined-text choice and an identity edit can coexist. Keep
                    # the explicit sequence available to the split assessor even
                    # though the identity has higher proposal precedence.
                    selected = answer.get("correction")
                    if evidence.get("issue") == "merged" and isinstance(selected, str) and len(selected.strip()) >= 2:
                        evidence["suggestions"] = [{"text": selected.strip(), "selected": True}]
                    encoded = _json(evidence)
                    values = {}
                    if before["label"] != actual["label"]:
                        values.update(unicode=" ".join(refs.to_code_points(before["label"])),
                                      script=str(refs.script_of(before["label"])))
                    if before["reading"] != actual["reading"]:
                        values["reading"] = before["reading"]
                    for field, value in values.items():
                        if getattr(unit, field) != value:
                            _append(store, conn, remote, field, value, encoded, remote["id"] + ":" + field)
                    local_event = _append(store, conn, remote, "review", remote["new"], encoded, remote["id"])
                    revision = store._revision(conn, target)
                    bound = {**deepcopy(record), "event": local_event, "current_revision": revision}
                    conn.execute("INSERT INTO cloudflare_imports VALUES(?,?,?,?,?,?,?,?)",
                                 (remote["id"], target, source_fingerprint, publication_key, before["revision"], revision,
                                  _json(before), _json(bound)))
                    if apply:
                        output.append(bound)
                    item.update(status="imported" if apply else "ready", local_event_fingerprint=fingerprint(bound))
                except (Rejected, KeyError, TypeError, ValueError) as error:
                    conn.execute("ROLLBACK TO remote_review")
                    item["reason"] = str(error) if isinstance(error, Rejected) else "invalid remote review structure"
                finally:
                    conn.execute("RELEASE remote_review")
            if not apply:
                raise _PreviewRollback()
    except _PreviewRollback:
        pass
    return {**payload, "reviews": output}, {"counts": dict(Counter(item["status"] for item in report)), "items": report}


def bind_remote_outcomes(result: dict, imports: dict) -> None:
    """Bind completed local refinement to original remote receipts without changing the journal."""
    originals = {item["event_id"]: item for item in imports["items"]
                 if item["status"] in ("imported", "duplicate")}
    for item in result.get("items", []):
        source = originals.get(item.get("event_id"))
        if source and item.get("event_fingerprint") == source.get("local_event_fingerprint"):
            item["local_event_fingerprint"] = item["event_fingerprint"]
            item["event_fingerprint"] = source["event_fingerprint"]
