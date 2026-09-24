"""Processed feedback leaves the next export without deleting history or newer work."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest
import test_corpus_reviews as corpus_tests
import test_review_atlas as atlas_tests
from fastapi.testclient import TestClient

from glyph_atlas.review.receipts import FeedbackReceipts, complete_batch, fingerprint
from glyph_atlas.review.server import create_app
from glyph_atlas.review.store import Store


@pytest.fixture
def dataset(tmp_path, monkeypatch):
    return atlas_tests.dataset.__wrapped__(tmp_path, monkeypatch)


def event(identifier="rv1", *, origin=None, at="2026-09-21T00:00:00Z"):
    row = {"event": {"id": identifier, "target_id": "unit-1", "at": at,
                     "new": "disputed", "evidence": "a saved answer"},
           "current": True, "current_revision": 1}
    if origin is not None:
        row["origin"] = origin
    return row


def test_receipts_use_full_event_identity_and_ignore_mutable_export_state(tmp_path):
    receipt = FeedbackReceipts(tmp_path)
    original = event()
    assert receipt.mark([original], batch="first.json") == 1
    assert receipt.mark([original], batch="repeat.json") == 0
    updated = {**copy.deepcopy(original), "current": False, "current_revision": 3}
    assert fingerprint(updated) == fingerprint(original)
    assert receipt.filter([updated])[0] == []
    reused = event(at="2026-09-21T01:00:00Z")
    other_origin = event(origin="corpus")
    newer = event("rv2")
    remaining, counts = receipt.filter([original, reused, other_origin, newer])
    assert remaining == [reused, other_origin, newer]
    assert counts == {"total": 4, "processed": 1, "unprocessed": 3}
    assert receipt.mark([other_origin], batch="corpus.json") == 1
    assert receipt.filter([original, other_origin])[0] == []


def test_invalid_batch_marks_nothing(tmp_path):
    receipts = FeedbackReceipts(tmp_path)
    with pytest.raises(ValueError):
        receipts.mark([event(), {"event": {}}], batch="broken.json")
    assert receipts.processed() == set()


def test_dry_run_and_failed_report_do_not_acknowledge_feedback(tmp_path, monkeypatch):
    from glyph_atlas.review import receipts

    payload = {"reviews": [event()]}
    result = {"feedback": {"items": [{"event_id": "rv1", "unit_id": "unit-1", "status": "withheld",
                                      "event_fingerprint": fingerprint(payload["reviews"][0])}]}}
    complete_batch(tmp_path, payload, result, tmp_path / "dry.json", apply=False)
    assert FeedbackReceipts(tmp_path).processed() == set()

    def fail(*args):
        raise OSError("disk full")

    monkeypatch.setattr(receipts, "write_batch_report", fail)
    with pytest.raises(OSError):
        complete_batch(tmp_path, payload, result, tmp_path / "failed.json", apply=True)
    assert FeedbackReceipts(tmp_path).processed() == set()


def test_failed_report_directory_sync_does_not_acknowledge_feedback(tmp_path, monkeypatch):
    import os
    import stat

    row = event()
    result = {"feedback": {"items": [{"status": "resolved", "event_fingerprint": fingerprint(row)}]}}
    sync = os.fsync

    def fail_directory(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("report directory sync failed")
        sync(fd)

    monkeypatch.setattr(os, "fsync", fail_directory)
    with pytest.raises(OSError, match="directory sync"):
        complete_batch(tmp_path, {"reviews": [row]}, result, tmp_path / "applied.json", apply=True)
    assert FeedbackReceipts(tmp_path).processed() == set()


def test_successful_batch_marks_withheld_and_unchanged_after_report(tmp_path):
    rows = [event("a"), event("b")]
    payload = {"reviews": rows}
    result = {"feedback": {"items": [
        {"event_id": "a", "unit_id": "unit-1", "status": "withheld", "event_fingerprint": fingerprint(rows[0])},
        {"event_id": "b", "unit_id": "unit-1", "status": "unchanged", "event_fingerprint": fingerprint(rows[1])}]}}
    output = tmp_path / "applied.json"
    assert complete_batch(tmp_path, payload, result, output, apply=True) == {"marked": 2}
    assert json.loads(output.read_text()) == result
    assert FeedbackReceipts(tmp_path).filter(rows)[0] == []
    assert complete_batch(tmp_path, payload, result, output, apply=True) == {"marked": 0}


def test_skipped_corpus_untrusted_and_noncurrent_feedback_remain_exportable(dataset, tmp_path):
    from glyph_atlas.review.refine import refine_feedback

    client = TestClient(create_app(dataset))
    round = atlas_tests.round_payload(client, count=1)
    round["answers"][0].update(verdict="wrong", issue="crop")
    assert client.post("/atlas/rounds", json=round).status_code == 200
    local = client.get("/atlas/reviews").json()["reviews"][0]
    corpus = {"origin": "corpus", "current": True,
              "event": {"id": "corpus-review", "target_id": "codh:1", "actor_kind": "human",
                        "new": {"verdict": "wrong", "issue": "crop"}}}
    untrusted = copy.deepcopy(local)
    untrusted["event"].update(id="model-review", role="model")
    old = copy.deepcopy(local)
    old["event"]["id"] = "old-review"
    old["current"] = False
    rows = [local, corpus, untrusted, old]
    payload = {"reviews": rows}
    result = {"feedback": refine_feedback(Store(dataset), payload, apply=True)}
    assert len(result["feedback"]["items"]) == 1
    assert result["feedback"]["items"][0]["event_fingerprint"] == fingerprint(local)
    assert complete_batch(dataset, payload, result, tmp_path / "mixed.json", apply=True) == {"marked": 1}
    assert FeedbackReceipts(dataset).filter(rows)[0] == [corpus, untrusted, old]


def test_result_must_name_exact_event_fingerprint_before_acknowledgement(tmp_path):
    original = event()
    reused = event(at="2026-09-21T01:00:00Z")
    result = {"feedback": {"items": [{"event_id": "rv1", "unit_id": "unit-1", "status": "resolved",
                                      "event_fingerprint": fingerprint(original)}]}}
    assert complete_batch(tmp_path, {"reviews": [reused]}, result,
                          tmp_path / "wrong-event.json", apply=True) == {"marked": 0}
    # An old report naming an id alone cannot acknowledge a new event with that id.
    del result["feedback"]["items"][0]["event_fingerprint"]
    assert complete_batch(tmp_path, {"reviews": [original]}, result,
                          tmp_path / "unbound-report.json", apply=True) == {"marked": 0}
    assert FeedbackReceipts(tmp_path).filter([original, reused])[0] == [original, reused]


@pytest.mark.parametrize("unfinished", ["error", "split-proposed", "running"])
def test_incomplete_outcome_remains_unprocessed(tmp_path, unfinished):
    rows = [event("finished"), event("unfinished")]
    result = {"feedback": {"items": [
        {"event_fingerprint": fingerprint(rows[0]), "status": "resolved"},
        {"event_fingerprint": fingerprint(rows[1]), "status": unfinished}]}}
    assert complete_batch(tmp_path, {"reviews": rows}, result,
                          tmp_path / "partial.json", apply=True) == {"marked": 1}
    assert FeedbackReceipts(tmp_path).filter(rows)[0] == [rows[1]]


def test_default_export_is_new_feedback_and_history_keeps_processed_and_undo(dataset):
    client = TestClient(create_app(dataset))
    round = atlas_tests.round_payload(client, count=2)
    for answer in round["answers"]:
        answer.update(verdict="wrong", issue="crop")
    assert client.post("/atlas/rounds", json=round).status_code == 200
    all_before = client.get("/atlas/reviews").json()["reviews"]
    assert len(all_before) == 2
    FeedbackReceipts(dataset).mark(all_before[:1], batch="first.json")
    pending = client.get("/atlas/reviews").json()
    history = client.get("/atlas/reviews?include_processed=true").json()
    assert pending["scope"] == "unprocessed" and history["scope"] == "history"
    assert pending["reviews"] == all_before[1:]
    assert history["reviews"] == all_before
    assert pending["counts"] == {"total": 2, "processed": 1, "unprocessed": 1}
    assert client.get("/atlas/reviews.json").json() == pending
    assert client.get("/atlas/reviews.json?include_processed=true").json() == history
    assert len(Store(dataset).events()) == 2
    # A newer decision remains unprocessed even when it targets the same unit.
    assert client.post(f"/atlas/rounds/{round['id']}/undo",
                       json={"client_id": round["client_id"]}).status_code == 200
    latest = atlas_tests.round_payload(client, count=1)
    latest["answers"][0].update(verdict="wrong", issue="reading")
    assert client.post("/atlas/rounds", json=latest).status_code == 200
    assert len(client.get("/atlas/reviews").json()["reviews"]) == 2
    assert len(client.get("/atlas/reviews?include_processed=true").json()["reviews"]) == 3


def test_corpus_review_receipt_filters_combined_export_without_changing_source(dataset, tmp_path, monkeypatch):
    from uuid import uuid4

    from glyph_atlas.review.corpus_reviews import CorpusEdit

    corpus = corpus_tests.corpus.__wrapped__(tmp_path, monkeypatch)
    reviews = corpus["reviews"]
    source = reviews.source(corpus_tests.UNIT)
    review = CorpusEdit(id=uuid4(), identity=corpus_tests.UNIT, client_id="person",
                        revision=0, source_revision=source["source_revision"],
                        verdict="wrong", issue="character", character="ヌ")
    reviews.record(review)
    before = corpus_tests.fingerprint(corpus["source"])
    client = TestClient(create_app(dataset, corpus=corpus["api"]))
    payload = client.get("/atlas/reviews").json()
    assert len(payload["reviews"]) == 1 and payload["reviews"][0]["origin"] == "corpus"
    FeedbackReceipts(dataset).mark(payload["reviews"], batch="corpus-applied.json")
    assert client.get("/atlas/reviews").json()["reviews"] == []
    assert client.get("/atlas/reviews?include_processed=true").json()["reviews"] == payload["reviews"]
    assert len(reviews.exports()) == 1
    assert corpus_tests.fingerprint(corpus["source"]) == before


def test_failed_offline_refinement_leaves_whole_batch_unprocessed(dataset, tmp_path, monkeypatch):
    module_path = Path(__file__).parents[1] / "scripts/refine_feedback.py"
    spec = importlib.util.spec_from_file_location("refine_feedback_cli", module_path)
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    source = tmp_path / "input.json"
    source.write_text(json.dumps({"reviews": [event()]}))

    def partial_failure(*args, **kwargs):
        raise RuntimeError("inference stopped after a previous item")

    monkeypatch.setattr(cli, "refine_feedback", partial_failure)
    monkeypatch.setattr("sys.argv", ["refine_feedback.py", str(dataset), str(source),
                                     "--output", str(tmp_path / "applied.json"), "--apply"])
    with pytest.raises(RuntimeError):
        cli.main()
    assert FeedbackReceipts(dataset).processed() == set()
    assert not (tmp_path / "applied.json").exists()


def test_background_refinement_never_acknowledges_a_saved_review(dataset, monkeypatch):
    from glyph_atlas.review import refine

    class Uncertain:
        def assess(self, *args):
            return {"accepted": False, "reason": "ambiguous boundary"}

    monkeypatch.setattr(refine, "SplitEngine", Uncertain)
    client = TestClient(create_app(dataset))
    round = atlas_tests.round_payload(client, count=1)
    round["answers"][0].update(verdict="wrong", issue="merged")
    assert client.post("/atlas/rounds", json=round).status_code == 200
    payload = client.get("/atlas/reviews").json()
    refine.background_refine(Store(dataset), payload)
    assert FeedbackReceipts(dataset).processed() == set()
    assert len(client.get("/atlas/reviews").json()["reviews"]) == 1
