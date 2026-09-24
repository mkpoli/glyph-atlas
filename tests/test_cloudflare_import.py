"""Hosted feedback is anchored to local pixels and never overwrites newer local decisions."""
import hashlib
import json
from copy import deepcopy
from uuid import uuid4

import pytest
from PIL import Image

from glyph_atlas import tables
from glyph_atlas.review import cloudflare_import as bridge
from glyph_atlas.review.receipts import FeedbackReceipts, complete_batch, fingerprint
from glyph_atlas.review.refine import refine_feedback
from glyph_atlas.review.store import ReviewRequest, Store
from glyph_atlas.schema import Box, Document, Line, Page, Unit


@pytest.fixture
def store(tmp_path, monkeypatch):
    root = tmp_path / "dataset"
    root.mkdir()
    cache = tmp_path / "cache"
    monkeypatch.setenv("GLYPH_ATLAS_CACHE", str(cache))
    image = tmp_path / "page.png"
    Image.new("RGB", (200, 200), "white").save(image)
    digest = hashlib.sha256(image.read_bytes()).hexdigest()
    path = cache / "images" / digest[:2] / (digest + ".png")
    path.parent.mkdir(parents=True)
    path.write_bytes(image.read_bytes())
    tables.write(root / "documents.parquet", [Document(id="d", title="Example")], Document)
    tables.write(root / "pages.parquet", [Page(id="p", document_id="d", seq=0, image="x",
                                             width=200, height=200, sha256=digest)], Page)
    tables.write(root / "lines.parquet", [Line(id="l", page_id="p", seq=0, text="手", text_raw="手")], Line)
    tables.write(root / "units.parquet", [Unit(id="u", document_id="d", page_id="p", line_id="l",
                 unicode="U+624B", reading="手", text_source="手", box=Box(x=10, y=10, w=30, h=40))], Unit)
    return Store(root)


def baseline(store):
    unit = store.unit("u")
    return {"character": {"id": "u", "label": "手", "reading": "手", "revision": store.revision("u"),
                          "box": unit.box.model_dump(), "page_id": "p", "image_sha256": bridge._source_digest(store, unit)},
            "source_refs": {"book": "fixture"}}


def remote(publication, *, before=None, issue="character", character="を", correction=None, reading=None,
           current=True, round_review=False):
    shown = deepcopy(before or publication["character"])
    request = {"id": str(uuid4()), "client_id": "reviewer-test", "revision": shown["revision"],
               "image_sha256": shown["image_sha256"], "verdict": "wrong", "issue": issue}
    if character:
        request["character"] = character
    if correction:
        request["correction"] = correction
    if reading:
        request["reading"] = reading
    saved_reading = reading or (correction if issue == "reading" else None) or shown["reading"]
    after = {**shown, "label": bridge.identity_text(character) if character else shown["label"],
             "reading": saved_reading, "revision": shown["revision"] + 1}
    snapshot = {**deepcopy(publication), "character": shown}
    evidence = {"kind": "character-review", "verdict": "wrong", "issue": issue, "request": request,
                "snapshot": snapshot, "suggested_character": " ".join(f"U+{ord(c):04X}" for c in after["label"]) if character else None,
                "suggested_reading": correction,
                "correction": {"unicode": " ".join(f"U+{ord(c):04X}" for c in after["label"]),
                               "reading": after["reading"], "box": after["box"]}}
    if round_review:
        evidence.update(kind="visual-quiz", request={"id": str(uuid4()), "client_id": request["client_id"],
            "label": shown["label"], "answers": [{**request, "id": shown["id"]}]})
    resolved = bool(issue == "character" and character) or bool(issue == "reading" and (reading or correction))
    record = {"event": {"id": "cf:" + str(uuid4()), "target_type": "unit", "target_id": "u",
                        "field": "review", "old": "machine", "new": "reviewed" if resolved else "disputed",
                        "role": "reviewer", "actor": "reviewer-test", "evidence": json.dumps(evidence, ensure_ascii=False),
                        "at": "2026-09-22T00:00:00.000Z"},
              "reviewed": snapshot, "publication_snapshot": deepcopy(publication),
              "expected_revision": shown["revision"], "current": current, "current_revision": after["revision"]}
    return record, after


def payload(*records):
    return {"version": 1, "kind": "atlas-character-reviews", "publication": "fixture-publication", "reviews": list(records)}


def test_preview_import_refinement_and_original_receipts(store, tmp_path):
    record, _ = remote(baseline(store))
    source = payload(record)
    frozen = deepcopy(source)
    preview, report = bridge.ingest_cloudflare(store, source)
    assert report["counts"] == {"ready": 1} and preview["reviews"] == []
    assert not store.events() and store.revision("u") == 0
    bound, report = bridge.ingest_cloudflare(store, source, apply=True)
    assert report["counts"] == {"imported": 1}
    assert store.unit("u").unicode == "U+3092" and store.unit("u").reading == "手"
    assert store.events()[-1].id == record["event"]["id"]
    event = bound["reviews"][0]["event"]
    provenance = json.loads(event["evidence"])["cloudflare_import"]
    assert provenance["remote_event"] == record["event"]
    assert provenance["remote_fingerprint"] == fingerprint(record)
    assert source == frozen
    outcome = refine_feedback(store, bound, apply=True)
    assert outcome["counts"] == {"resolved": 1}
    bridge.bind_remote_outcomes(outcome, report)
    assert complete_batch(store.directory, source, {"feedback": outcome}, tmp_path / "report.json", apply=True) == {"marked": 1}
    assert FeedbackReceipts(store.directory).filter(source["reviews"])[0] == []
    count = len(store.events())
    retry, report = bridge.ingest_cloudflare(store, source, apply=True)
    assert report["counts"] == {"duplicate": 1} and len(store.events()) == count
    assert refine_feedback(store, retry, apply=True)["counts"] == {"already-processed": 1}


@pytest.mark.parametrize("change", ["revision", "pixels", "box", "label", "reading"])
def test_changed_local_occurrence_is_rejected(store, monkeypatch, change):
    record, _ = remote(baseline(store))
    if change == "pixels":
        monkeypatch.setattr(bridge, "_source_digest", lambda *_: "changed")
    elif change == "revision":
        store.record(ReviewRequest(target_id="u", field="review", new="reviewed", client_id="local"))
    else:
        field, value = {"box": ("box", {"x": 11, "y": 10, "w": 30, "h": 40}),
                        "label": ("unicode", "U+624B U+624B"), "reading": ("reading", "て")}[change]
        store.record(ReviewRequest(target_id="u", field=field, new=value, client_id="local"))
    count = len(store.events())
    bound, report = bridge.ingest_cloudflare(store, payload(record), apply=True)
    assert not bound["reviews"] and report["counts"] == {"rejected": 1}
    assert len(store.events()) == count


def test_full_remote_chain_imports_only_current_review(store):
    publication = baseline(store)
    first, after = remote(publication, current=False)
    second, _ = remote(publication, before=after, issue="crop", character=None)
    bound, report = bridge.ingest_cloudflare(store, payload(first, second), apply=True)
    assert report["counts"] == {"imported": 1}
    assert len(bound["reviews"]) == 1 and bound["reviews"][0]["event"]["id"] == second["event"]["id"]
    assert first["event"]["id"] not in {event.id for event in store.events()}
    assert store.unit("u").unicode == "U+3092" and store.unit("u").review == "disputed"
    assert len(json.loads(store.events()[-1].evidence)["cloudflare_import"]["remote_chain"]) == 2


def test_later_batch_can_follow_own_refinement_but_not_later_local_review(store):
    publication = baseline(store)
    first, after = remote(publication)
    bound, _ = bridge.ingest_cloudflare(store, payload(first), apply=True)
    refine_feedback(store, bound, apply=True)
    second, after_second = remote(publication, before=after, issue="crop", character=None)
    _, report = bridge.ingest_cloudflare(store, payload(second), apply=True)
    assert report["counts"] == {"imported": 1}
    store.record(ReviewRequest(target_id="u", field="review", new="reviewed", client_id="local"))
    third, _ = remote(publication, before=after_second, issue="crop", character=None)
    _, report = bridge.ingest_cloudflare(store, payload(third), apply=True)
    assert report["counts"] == {"rejected": 1}
    assert store.unit("u").review == "reviewed"


def test_missing_undo_step_and_withdrawn_review_do_not_import(store):
    publication = baseline(store)
    first, after = remote(publication, current=False)
    after["revision"] += 1
    second, _ = remote(publication, before=after, issue="crop", character=None)
    _, report = bridge.ingest_cloudflare(store, payload(first, second), apply=True)
    assert report["counts"] == {"rejected": 1} and not store.events()
    _, report = bridge.ingest_cloudflare(store, payload(first), apply=True)
    assert report["counts"] == {"not-current": 1} and not store.events()


def test_reused_remote_id_is_rejected(store):
    record, _ = remote(baseline(store))
    bridge.ingest_cloudflare(store, payload(record), apply=True)
    count = len(store.events())
    record["event"]["at"] = "2026-09-22T00:00:01Z"
    _, report = bridge.ingest_cloudflare(store, payload(record), apply=True)
    assert report["counts"] == {"rejected": 1} and len(store.events()) == count


def test_partial_unit_import_rolls_back_if_final_event_fails(store, monkeypatch):
    record, _ = remote(baseline(store))
    original = bridge._append
    def fail(store, conn, remote, field, *args):
        if field == "review":
            raise ValueError("simulated invalid event")
        return original(store, conn, remote, field, *args)
    monkeypatch.setattr(bridge, "_append", fail)
    _, report = bridge.ingest_cloudflare(store, payload(record), apply=True)
    assert report["counts"] == {"rejected": 1}
    assert not store.events() and store.unit("u").unicode == "U+624B" and store.revision("u") == 0


def test_corpus_and_existing_local_exports_pass_through(store):
    corpus = {"origin": "corpus", "event": {"id": "cf:" + str(uuid4()), "target_id": "corpus:u"}}
    local = {"event": {"id": "rv00000001", "target_id": "u"}}
    source = payload(corpus, local)
    assert bridge.ingest_cloudflare(store, source, apply=True) == (source, {"counts": {}, "items": []})


def test_crop_with_identity_edit_cannot_claim_confirmation(store):
    record, _ = remote(baseline(store), issue="crop")
    record["event"]["new"] = "reviewed"
    _, report = bridge.ingest_cloudflare(store, payload(record), apply=True)
    assert report["counts"] == {"rejected": 1} and not store.events()


def test_crop_with_identity_edit_stays_flagged_after_refinement(store):
    record, _ = remote(baseline(store), issue="crop")
    bound, report = bridge.ingest_cloudflare(store, payload(record), apply=True)
    assert report["counts"] == {"imported": 1}
    assert store.unit("u").unicode == "U+3092" and store.unit("u").review == "disputed"
    assert refine_feedback(store, bound, apply=True)["counts"] == {"withheld": 1}
    assert store.unit("u").unicode == "U+3092" and store.unit("u").review == "disputed"


@pytest.mark.parametrize("round_review", [False, True])
def test_joined_identity_and_sequence_reach_refinement_without_confirming_parent(store, round_review):
    class Uncertain:
        def assess(self, crop, expected):
            assert expected == "を手"
            assert crop.size == (30, 40)
            return {"accepted": False, "reason": "fixture needs a boundary"}

    record, _ = remote(baseline(store), issue="merged", correction="を手", round_review=round_review)
    bound, report = bridge.ingest_cloudflare(store, payload(record), apply=True)
    assert report["counts"] == {"imported": 1}
    result = refine_feedback(store, bound, apply=True, engine=Uncertain())
    assert result["counts"] == {"withheld": 1} and result["items"][0]["decision"] == "joined"
    assert store.unit("u").unicode == "U+3092" and store.unit("u").review == "disputed"
    assert json.loads(bound["reviews"][0]["event"]["evidence"])["cloudflare_import"]["remote_event"] == record["event"]


def test_phonetic_correction_keeps_written_identity(store):
    record, _ = remote(baseline(store), issue="reading", character=None, correction="て")
    bound, report = bridge.ingest_cloudflare(store, payload(record), apply=True)
    assert report["counts"] == {"imported": 1}
    assert refine_feedback(store, bound, apply=True)["counts"] == {"unchanged": 1}
    assert store.unit("u").unicode == "U+624B" and store.unit("u").reading == "て"


def test_joined_import_can_split_with_supported_boundaries(store):
    class Supported:
        def assess(self, crop, expected):
            assert expected == "を手"
            return {"accepted": True, "text": ["を", "手"], "sequence": {"text": "を手", "score": .999},
                    "boxes": [{"x": 0, "y": 0, "w": 30, "h": 20}, {"x": 0, "y": 20, "w": 30, "h": 20}]}

    record, _ = remote(baseline(store), issue="merged", correction="を手", round_review=True)
    bound, report = bridge.ingest_cloudflare(store, payload(record), apply=True)
    assert report["counts"] == {"imported": 1}
    result = refine_feedback(store, bound, apply=True, engine=Supported())
    assert result["counts"] == {"split": 1}
    parent = store.unit("u")
    assert not parent.active
    children = [store.unit(identity) for identity in parent.split_into]
    assert [unit.reading for unit in children] == ["を", "手"]
    assert all(unit.review == "machine" for unit in children)


def test_encoded_identity_is_canonicalized_only_in_local_event(store):
    record, _ = remote(baseline(store), character="U+3092")
    bound, report = bridge.ingest_cloudflare(store, payload(record), apply=True)
    assert report["counts"] == {"imported": 1}
    assert refine_feedback(store, bound, apply=True)["counts"] == {"resolved": 1}
    assert store.unit("u").unicode == "U+3092"
    evidence = json.loads(bound["reviews"][0]["event"]["evidence"])
    assert evidence["request"]["character"] == "を"
    assert json.loads(evidence["cloudflare_import"]["remote_event"]["evidence"])["request"]["character"] == "U+3092"


def test_preview_retry_has_no_journal_or_receipt_changes(store):
    record, _ = remote(baseline(store))
    source = payload(record)
    for _ in range(2):
        assert bridge.ingest_cloudflare(store, source)[1]["counts"] == {"ready": 1}
    assert not store.events() and store.revision("u") == 0
    bound, _ = bridge.ingest_cloudflare(store, source, apply=True)
    events = store.events()
    retry, report = bridge.ingest_cloudflare(store, source)
    assert report["counts"] == {"duplicate": 1} and retry == bound
    assert refine_feedback(store, retry)["counts"] == {"resolved": 1}
    assert store.events() == events and not FeedbackReceipts(store.directory).processed()


def test_command_imports_before_refinement_and_acknowledges_both_exports(store, tmp_path, monkeypatch):
    import runpy
    import sys
    from pathlib import Path

    record, _ = remote(baseline(store))
    source = tmp_path / "reviews.json"
    source.write_text(json.dumps(payload(record)))
    report = tmp_path / "result.json"
    script = Path(__file__).resolve().parents[1] / "scripts/refine_feedback.py"
    monkeypatch.setattr(sys, "argv", [str(script), str(store.directory), str(source), "--output", str(report), "--apply"])
    runpy.run_path(str(script), run_name="__main__")
    result = json.loads(report.read_text())
    assert result["cloudflare_import"]["counts"] == {"imported": 1}
    assert result["feedback"]["counts"] == {"resolved": 1}
    receipts = FeedbackReceipts(store.directory).processed()
    assert fingerprint(record) in receipts
    assert result["feedback"]["items"][0]["local_event_fingerprint"] in receipts
    assert store.unit("u").unicode == "U+3092"


def test_receipt_failure_rolls_back_both_identities_and_retry_recovers(store, tmp_path, monkeypatch):
    import runpy
    import sqlite3
    import sys
    from pathlib import Path

    record, _ = remote(baseline(store))
    source = tmp_path / "reviews.json"
    source.write_text(json.dumps(payload(record)))
    report = tmp_path / "result.json"
    receipts = FeedbackReceipts(store.directory)
    receipts.mark([], batch="fixture")
    with sqlite3.connect(receipts.path) as db:
        db.execute("""CREATE TRIGGER fail_second BEFORE INSERT ON feedback_receipts
            WHEN (SELECT COUNT(*) FROM feedback_receipts)>0
            BEGIN SELECT RAISE(ABORT, 'simulated interrupted acknowledgement'); END""")
    script = Path(__file__).resolve().parents[1] / "scripts/refine_feedback.py"
    monkeypatch.setattr(sys, "argv", [str(script), str(store.directory), str(source), "--output", str(report), "--apply"])
    with pytest.raises(sqlite3.IntegrityError, match="interrupted acknowledgement"):
        runpy.run_path(str(script), run_name="__main__")
    assert report.exists() and not receipts.processed()
    events = store.events()
    with sqlite3.connect(receipts.path) as db:
        db.execute("DROP TRIGGER fail_second")
    runpy.run_path(str(script), run_name="__main__")
    assert store.events() == events
    result = json.loads(report.read_text())
    assert result["cloudflare_import"]["counts"] == {"duplicate": 1}
    assert result["feedback"]["counts"] == {"already-processed": 1}
    assert receipts.processed() == {fingerprint(record), result["feedback"]["items"][0]["local_event_fingerprint"]}
