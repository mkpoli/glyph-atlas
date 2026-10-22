"""The old site's review export records, rebuilt exactly from its frozen rows for the review archive."""
from __future__ import annotations

import json
import re
import unicodedata


def literal(value):
    """The Worker's input.literal: compose kana, preserving compatibility ideographs."""
    value = value.strip()
    if re.fullmatch(r"U\+[0-9a-f]{4,6}(?:[\s-]+U\+[0-9a-f]{4,6})*", value, re.IGNORECASE):
        value = "".join(chr(int(part[2:], 16)) for part in re.split(r"[\s-]+", value))  # noqa: FURB166 -- U+ is not a Python numeric prefix
    return "".join(part if len(part) == 1 and (0xF900 <= ord(part) <= 0xFAFF or 0x2F800 <= ord(part) <= 0x2FA1F)
                   else unicodedata.normalize("NFC", part)
                   for part in re.split(r"([\uF900-\uFAFF\U0002F800-\U0002FA1F])", value))


def parsed(value):
    # The legacy endpoint's parse helper uses an empty object for absent values.
    return json.loads(value) if value else {}


def publication(unit):
    """The crop's publication record as the old export gave it (`parse(u.snapshot)`, an empty object
    when the old site held none); a unit read without it stops the archive."""
    if "snapshot" not in unit:
        raise ValueError(f"unit {unit.get('id', 'unknown')} was read without its publication record")
    return parsed(unit["snapshot"])


def legacy(row, unit):
    """The immutable fields of the old reviews endpoint, including its evidence string.

    Missing JS object properties are omitted; explicit nulls remain null. In particular,
    rebuilding the nested evidence JSON would invalidate existing feedback receipts.
    """
    row = dict(row)
    event, snapshot = parsed(row["event"]), parsed(row["snapshot"])
    if unit is None or row.get("kind") == "undo":
        return {"legacy_hidden": True, "event": event, "reviewed": snapshot,
                "expected_revision": row["expected_revision"]}
    if unit["origin"] != "corpus":
        return {"event": event, "reviewed": snapshot, "expected_revision": row["expected_revision"],
                "publication_snapshot": publication(unit)}
    evidence = parsed(event["evidence"])
    named = literal(evidence["suggested_character"]) if evidence.get("suggested_character") else None
    correction = evidence.get("suggested_text")
    if correction is None:
        correction = evidence.get("suggested_reading")
    decision = {k: evidence[k] for k in ("verdict", "issue", "note") if k in evidence}
    decision.update(character=named, correction=correction)
    update = {"proposed_character": named, "applied_upstream": False}
    if "target_id" in event:
        update["identity"] = event["target_id"]
    update.update({k: snapshot[k] for k in ("source_revision", "source", "box") if k in snapshot})
    if snapshot.get("source_code_point"):
        update["original_character"] = literal(snapshot["source_code_point"])
    elif "source_label" in snapshot:
        update["original_character"] = snapshot["source_label"]
    if "issue" in evidence:
        update["issue"] = evidence["issue"]
    return {"origin": "corpus", "event": {**event, "new": decision}, "reviewed": snapshot, "source_update": update}
