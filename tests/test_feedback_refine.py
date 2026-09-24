"""Feedback repairs preserve source events and reject stale or ambiguous input."""
import hashlib
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from glyph_atlas import tables
from glyph_atlas.review import refine
from glyph_atlas.review.server import create_app
from glyph_atlas.review.store import Conflict, ReviewRequest, Store
from glyph_atlas.schema import Box, Document, Line, Page, Unit


@pytest.fixture
def store(tmp_path, monkeypatch):
    root = tmp_path / "dataset"
    root.mkdir()
    cache = tmp_path / "cache"
    monkeypatch.setenv("GLYPH_ATLAS_CACHE", str(cache))
    image = tmp_path / "page.png"
    Image.new("RGB", (200, 200), (220, 210, 190)).save(image)
    digest = hashlib.sha256(image.read_bytes()).hexdigest()
    target = cache / "images" / digest[:2] / f"{digest}.png"
    target.parent.mkdir(parents=True)
    target.write_bytes(image.read_bytes())
    tables.write(root / "documents.parquet", [Document(id="d", title="Sample")], Document)
    tables.write(root / "pages.parquet", [Page(id="p", document_id="d", seq=0, image="x",
                                             width=200, height=200, sha256=digest)], Page)
    tables.write(root / "lines.parquet", [Line(id="l", page_id="p", seq=0, text="に四を",
                 text_raw="に四を", box=Box(x=10, y=10, w=40, h=170))], Line)
    tables.write(root / "units.parquet", [Unit(id="u", document_id="d", page_id="p", line_id="l",
                 seq=0, unicode="U+624B", reading="手", text_source="手", script="han",
                 box=Box(x=10, y=10, w=40, h=80)),
                 Unit(id="v", document_id="d", page_id="p", line_id="l", seq=1,
                      unicode="U+3092", reading="を", box=Box(x=10, y=110, w=40, h=40))], Unit)
    return Store(root)


def feedback(store, *, verdict="wrong", issue="reading", proposal="を", kind="character-review",
             suggested_character=None):
    unit = store.unit("u")
    digest = refine._source_digest(store, unit)
    character = {"id": unit.id, "label": "手", "reading": "手", "revision": 0,
                 "box": unit.box.model_dump(), "page_id": "p", "image_sha256": digest}
    snapshot = {"character": character, "image_sha256": digest}
    evidence = {"kind": kind, "verdict": verdict, "issue": issue,
                "request": {"correction": proposal, "reading": None},
                "suggested_reading": proposal, "snapshot": snapshot,
                "correction": {"reading": "手", "box": unit.box.model_dump()}}
    if suggested_character is not None:
        evidence["suggested_character"] = suggested_character
    store.record(ReviewRequest(target_id="u", field="review", new="disputed", client_id="person",
                               evidence=json.dumps(evidence, ensure_ascii=False)))
    event = store.events()[-1].model_dump(mode="json")
    return {"version": 1, "kind": "atlas-character-reviews", "reviews": [
        {"event": event, "reviewed": snapshot, "current": True, "current_revision": store.revision("u")}]}


def test_implicit_batch_match_retracted_without_deleting_history(store):
    payload = feedback(store, verdict="match", issue=None, proposal=None, kind="visual-quiz")
    original = store.events()[0].model_dump(mode="json")
    result = refine.refine_feedback(store, payload, apply=True)
    assert result["counts"] == {"unconfirmed": 1}
    assert store.unit("u").review == "machine"
    assert store.events()[0].model_dump(mode="json") == original
    client = TestClient(create_app(store.directory))
    assert client.get("/atlas/characters/u").json()["state"] == "pending"
    assert all(e.role == "model" for e in store.events()[1:])
    count = len(store.events())
    assert refine.refine_feedback(store, payload, apply=True)["counts"] == {"already-processed": 1}
    assert len(store.events()) == count


def test_implicit_match_retraction_preserves_newer_decision(store):
    payload = feedback(store, verdict="match", issue=None, proposal=None, kind="visual-quiz")
    store.record(ReviewRequest(target_id="u", field="review", new="reviewed", client_id="person2"))
    assert refine.refine_feedback(store, payload, apply=True)["counts"] == {"stale": 1}
    assert store.unit("u").review == "reviewed"


@pytest.mark.parametrize("old_change", ["timestamp", "evidence", "legacy"])
def test_reused_event_id_does_not_inherit_old_processing_receipt(store, old_change):
    from copy import deepcopy

    from glyph_atlas.review.receipts import fingerprint

    payload = feedback(store)
    record = payload["reviews"][0]
    previous = deepcopy(record)
    if old_change == "timestamp":
        previous["event"]["at"] = "2020-01-01T00:00:00Z"
    elif old_change == "evidence":
        previous["event"]["evidence"] = '{"kind":"character-review","note":"older decision"}'
    metadata = {"policy": refine.POLICY, "result": "resolved", "source_event_id": record["event"]["id"]}
    if old_change != "legacy":
        metadata["source_event_fingerprint"] = fingerprint(previous)
    # A reset may retain repaired unit metadata while reusing journal numbers.
    refine._changes(store, store.unit("u"), {"meta": {"feedback_repair": metadata}},
                    {"kind": "fixture-retained-metadata"}, base_revision=store.revision("u"))
    record["current_revision"] = store.revision("u")
    result = refine.refine_feedback(store, payload, apply=True)
    assert result["counts"] == {"resolved": 1}
    assert store.unit("u").unicode == "U+3092"
    assert store.unit("u").meta["feedback_repair"]["source_event_fingerprint"] == fingerprint(record)
    assert refine.refine_feedback(store, payload, apply=True)["counts"] == {"already-processed": 1}


class Split:
    def assess(self, crop, expected=None):
        return {"accepted": True, "text": ["に", "四"],
                "sequence": {"text": "に四", "score": .999},
                "boxes": [{"x": 0, "y": 0, "w": 40, "h": 40},
                          {"x": 0, "y": 40, "w": 40, "h": 40}]}


def test_resolves_selected_identity_preserving_source_and_journal(store):
    payload = feedback(store)
    original = store.events()[0].model_dump(mode="json")
    source = (store.directory / "units.parquet").read_bytes()
    dry = refine.refine_feedback(store, payload)
    assert dry["counts"] == {"resolved": 1}
    assert len(store.events()) == 1
    result = refine.refine_feedback(store, payload, apply=True)
    assert result["counts"] == {"resolved": 1}
    assert store.unit("u").unicode == "U+3092"
    assert store.unit("u").review == "reviewed"
    assert store.unit("u").reading == "手"  # the separate reading is preserved
    assert store.events()[0].model_dump(mode="json") == original
    assert all(e.role == "model" for e in store.events()[1:])
    assert (store.directory / "units.parquet").read_bytes() == source
    count = len(store.events())
    assert refine.refine_feedback(store, payload, apply=True)["counts"] == {"already-processed": 1}
    assert len(store.events()) == count
    client = TestClient(create_app(store.directory))
    detail = client.get("/atlas/characters/u").json()
    assert detail["state"] == "checked" and detail["label"] == "を"
    export = client.get("/atlas/reviews").json()["reviews"][0]
    assert export["event"] == original and export["current"]
    assert export["processing"]["result"] == "resolved"


def test_base_plus_mark_identity_resolves_rather_than_going_stale(store):
    """The quiz spells ツ + U+309A as U+30C4 U+309A; one character is one accepted identity."""
    payload = feedback(store, verdict="wrong", issue="character", proposal=None,
                       suggested_character="U+30C4 U+309A")
    result = refine.refine_feedback(store, payload, apply=True)
    assert result["counts"] == {"resolved": 1}
    assert store.unit("u").unicode == "U+30C4 U+309A"
    assert store.unit("u").review == "reviewed"


@pytest.mark.parametrize("change", ["revision", "pixels", "body", "missing-revision"])
def test_stale_feedback_is_never_applied(store, change):
    payload = feedback(store)
    if change == "revision":
        store.record(ReviewRequest(target_id="u", field="reading", new="い", client_id="another"))
    elif change == "pixels":
        payload["reviews"][0]["event"]["evidence"] = payload["reviews"][0]["event"]["evidence"].replace(
            refine._source_digest(store, store.unit("u")), "f" * 64)
    elif change == "body":
        payload["reviews"][0]["event"]["actor"] = "different"
    else:
        del payload["reviews"][0]["current_revision"]
    count = len(store.events())
    assert refine.refine_feedback(store, payload, apply=True)["counts"] == {"stale": 1}
    assert len(store.events()) == count


def test_machine_split_retires_parent_and_keeps_original_feedback(store):
    payload = feedback(store, issue="merged", proposal="に四")
    original = store.events()[0].model_dump(mode="json")
    result = refine.refine_feedback(store, payload, apply=True, engine=Split())
    assert result["counts"] == {"split": 1}
    parent = store.unit("u")
    assert not parent.active
    children = [store.unit(i) for i in parent.split_into]
    assert [u.reading for u in children] == ["に", "四"]
    assert all(u.review == "machine" and u.method == "detect-align" for u in children)
    assert all(u.meta["feedback_split"]["parent_id"] == "u" for u in children)
    assert store.events()[0].model_dump(mode="json") == original
    assert store.events()[-1].role == "model"
    exported = TestClient(create_app(store.directory)).get("/atlas/reviews").json()["reviews"][0]
    assert not exported["current"] and exported["processing"]["children"] == parent.split_into


def test_ambiguous_join_cannot_be_a_positive_example(store):
    payload = feedback(store, verdict="match", issue="merged", proposal=None)
    class Uncertain:
        def assess(self, *args):
            return {"accepted": False, "reason": "no reliable boundary"}
    refine.refine_feedback(store, payload, apply=True, engine=Uncertain())
    unit = store.unit("u")
    assert unit.review == "disputed" and unit.active
    assert unit.meta["alignment_repair"]["quiz"] is False


def test_changed_during_inference_is_rejected(store):
    payload = feedback(store, issue="merged", proposal="に四")
    class Racing(Split):
        def assess(self, *args):
            store.record(ReviewRequest(target_id="u", field="reading", new="ぬ", client_id="another"))
            return super().assess(*args)
    with pytest.raises(Conflict):
        refine.refine_feedback(store, payload, apply=True, engine=Racing())
    assert store.unit("u").active and store.unit("u").reading == "ぬ"


def test_saved_join_automatically_runs_extraction(store, monkeypatch):
    monkeypatch.setattr(refine, "SplitEngine", Split)
    client = TestClient(create_app(store.directory))
    digest = refine._source_digest(store, store.unit("u"))
    payload = {"id": "ce8ce092-1421-4b61-8c3d-4a176a2b8862", "client_id": "person",
               "revision": 0, "image_sha256": digest, "verdict": "wrong", "issue": "merged", "correction": "に四"}
    response = client.post("/atlas/characters/u", json=payload)
    assert response.status_code == 200, response.text
    assert len(store.unit("u").split_into) == 2
    retry = client.post("/atlas/characters/u", json=payload)
    assert retry.status_code == 200 and all(r["duplicate"] for r in retry.json()["results"])


@pytest.mark.parametrize("classifier,expected", [(None, "identity-repaired"), ("を", "identity-repaired"),
                                                 ("ヲ", "unchanged")])
def test_adjacent_evidence_accepts_abstention_but_rejects_disagreement(store, classifier, expected):
    class Model:
        def read(self, crop):
            return {"engines": [], "candidates": [], "votes": [
                {"engine": "NDLkotenOCR", "text": "を", "score": .995},
                {"engine": "Atlas classifier", "text": classifier, "score": .99}]}
    result = refine.repair_adjacent_labels(store, apply=True, engine=refine.SplitEngine(Model()))
    row = next(r for r in result["items"] if r["unit_id"] == "u")
    assert row["status"] == expected
    unit = store.unit("u")
    if expected == "identity-repaired":
        assert unit.unicode == "U+3092" and unit.reading == "を"
        assert unit.text_source == "手" and unit.review == "machine"
        assert all(e.role == "model" for e in store.events())
    else:
        assert unit.unicode == "U+624B" and not store.events()


def test_adjacent_scan_preserves_reviewed_occurrences(store):
    feedback(store)
    class Never:
        def read(self, crop):
            return {"engines": [], "candidates": [], "votes": []}
    result = refine.repair_adjacent_labels(store, apply=True, engine=refine.SplitEngine(Never()))
    assert "u" not in {r["unit_id"] for r in result["items"]}


def test_family_vote_is_not_classifier_abstention(store):
    class Model:
        def read(self, crop):
            return {"votes": [{"engine": "NDLkotenOCR", "text": "を", "score": .999},
                              {"engine": "Atlas classifier", "text": None, "score": .999,
                               "identity_scope": "family", "members": ["を", "ヲ"]}]}
    result = refine.repair_adjacent_labels(store, apply=True, engine=refine.SplitEngine(Model()))
    assert next(row for row in result["items"] if row["unit_id"] == "u")["status"] == "unchanged"
    assert not store.events()


def test_boundary_scoring_does_not_compare_different_model_probabilities():
    class Model:
        def read(self, crop):
            return {"candidates": [], "votes": [
                {"engine": "NDLkotenOCR", "text": "キ", "score": .98},
                {"engine": "Atlas classifier", "text": "干", "score": .999}]}
    assert refine.SplitEngine(Model()).score([Image.new('RGB', (30, 30))]) == [{"U+30AD": .98}]


def test_actual_encoded_ligature_is_protected_before_any_ocr():
    class Never:
        def read(self, crop):
            raise AssertionError('no need to inspect a known encoded ligature')
    result = refine.SplitEngine(Never()).assess(Image.new('RGB', (30, 60)), 'トキ', identity='U+1B124')
    assert not result['accepted'] and 'encoded ligature' in result['reason']


def test_new_assessment_under_same_feedback_does_not_reuse_submission_id(store):
    evidence = {"kind": "feedback-reconciliation", "source_event_id": "rv1", "result": "withheld"}
    refine._changes(store, store.unit('u'), {"meta": {"assessment": "old"}}, evidence, base_revision=0)
    refine._changes(store, store.unit('u'), {"meta": {"assessment": "updated"}}, evidence, base_revision=1)
    assert store.unit('u').meta['assessment'] == 'updated'


def test_saved_sequence_uses_independent_blank_gap_children_when_whole_is_weak():
    from PIL import ImageDraw

    crop = Image.new("RGB", (20, 60), "white")
    draw = ImageDraw.Draw(crop)
    draw.rectangle((4, 3, 15, 19), fill="black")
    draw.rectangle((4, 40, 15, 57), fill="black")

    class Model:
        def read(self, image):
            text = "ルラ" if image.height == 60 else "ル" if image.getpixel((10, 5))[0] < 128 else "ラ"
            score = .75 if image.height == 60 else .99
            candidate = {"engine": "NDLkotenOCR", "text": text, "score": score}
            return {"candidates": [candidate], "votes": [candidate], "engines": []}

    engine = refine.SplitEngine(Model())
    result = engine.assess(crop, "ルラ", identity="U+30E9")
    assert result["accepted"] and result["text"] == ["ル", "ラ"]
    assert result["cuts"] == [30]
    assert not engine.assess(crop, identity="U+30E9")["accepted"]
    assert not engine.assess(crop, "ルカ", identity="U+30E9")["accepted"]
    assert not engine.assess(crop, "ルラ")["accepted"]


@pytest.mark.parametrize("existing_identity,existing_box,expected", [
    ("U+30EB", Box(x=12, y=12, w=36, h=30), "recropped"),
    ("U+30CC", Box(x=12, y=12, w=36, h=30), "withheld"),
    ("U+30EB", Box(x=12, y=35, w=36, h=30), "withheld"),
])
def test_join_reuses_only_matching_spatial_occurrence(store, existing_identity, existing_box, expected):
    store.record_batch([
        ReviewRequest(target_id="u", field="unicode", new="U+30E9", client_id="setup"),
        ReviewRequest(target_id="v", field="unicode", new=existing_identity, client_id="setup"),
        ReviewRequest(target_id="v", field="box", new=existing_box.model_dump(), client_id="setup", base_revision=1),
    ], role="model")
    other_before = store.unit("v").model_dump(mode="json")
    unit = store.unit("u")
    proposal = {"accepted": True, "text": ["ル", "ラ"],
                "boxes": [{"x": 0, "y": 0, "w": 40, "h": 40},
                          {"x": 0, "y": 40, "w": 40, "h": 40}]}
    result = refine.split_unit(store, unit, proposal, base_revision=store.revision("u"), source_event_id="rv1")
    assert result["status"] == expected
    assert store.unit("v").model_dump(mode="json") == other_before
    if expected == "recropped":
        target = store.unit("u")
        assert target.active and not target.split_into
        assert target.box == Box(x=10, y=50, w=40, h=40)
        assert target.unicode == "U+30E9" and target.review == "machine"
        assert target.meta["feedback_repair"]["reused_children"][0]["id"] == "v"
        assert target.meta["feedback_repair"]["source_event_id"] == "rv1"
        assert store.events()[-1].role == "model"
    else:
        assert store.unit("u").box == unit.box
