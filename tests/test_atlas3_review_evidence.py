import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "atlas3"))
import review_evidence as evidence


def test_legacy_local_keeps_the_exact_event_and_publication_baseline():
    event = {"id": "old-17", "target_id": "ex:crop", "at": "2026-10-01T00:00:00+00:00",
             "evidence": '{ "suggested_reading": "か", "snapshot": {"character": {"revision": 3}} }'}
    snapshot = {"character": {"revision": 3}}
    baseline = {"character": {"id": "ex:crop", "revision": 0, "page_id": "book:p1"}}
    record = evidence.legacy({"event": json.dumps(event), "snapshot": json.dumps(snapshot), "expected_revision": 3},
                             {"origin": "local", "snapshot": json.dumps(baseline)})
    assert record == {"event": event, "reviewed": snapshot, "expected_revision": 3, "publication_snapshot": baseline}
    assert record["event"]["evidence"] == event["evidence"]
    # The old export parsed an absent record as an empty object; a unit read without it stops.
    assert evidence.legacy({"event": json.dumps(event), "snapshot": "{}", "expected_revision": 3},
                           {"origin": "retired", "snapshot": None})["publication_snapshot"] == {}
    with pytest.raises(ValueError, match="without its publication record"):
        evidence.legacy({"event": json.dumps(event), "snapshot": "{}", "expected_revision": 3}, {"origin": "local", "id": "ex:crop"})


def test_legacy_corpus_keeps_missing_fields_distinct_from_null_and_typed_reading():
    saved = {"verdict": "wrong", "issue": "character", "suggested_character": "U+304B-U+3099",
             "suggested_reading": "が", "note": None}
    event = {"id": "old-18", "target_id": "codh:g", "evidence": json.dumps(saved), "new": "disputed"}
    snapshot = {"source_revision": "r1", "source_code_point": "U+FA10", "source": {"book": "b"}, "box": None}
    record = evidence.legacy({"event": json.dumps(event), "snapshot": json.dumps(snapshot)}, {"origin": "corpus"})
    assert record == {"origin": "corpus", "event": {**event, "new": {
        "verdict": "wrong", "issue": "character", "character": "が", "correction": "が", "note": None}},
        "reviewed": snapshot, "source_update": {"identity": "codh:g", "source_revision": "r1", "source": {"book": "b"},
        "box": None, "original_character": "塚", "proposed_character": "が", "issue": "character", "applied_upstream": False}}
    event["evidence"] = '{}'
    record = evidence.legacy({"event": json.dumps(event), "snapshot": '{}'}, {"origin": "corpus"})
    assert record["event"]["new"] == {"character": None, "correction": None}
    assert record["source_update"] == {"identity": "codh:g", "proposed_character": None, "applied_upstream": False}
