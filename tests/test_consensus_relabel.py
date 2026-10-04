import json
from pathlib import Path

import numpy as np
import pytest

from glyph_atlas import tables
from glyph_atlas.review import consensus_relabel as cr
from glyph_atlas.review.consensus_relabel import SETTINGS, Read, Vote, agree, decide, gate, nearest, vote
from glyph_atlas.schema import Box, Document, Line, Page, Unit

DEFAULT = SETTINGS["default"]


def test_one_kana_one_variant_and_a_kanji_written_for_a_kana_agree():
    assert agree("ナ", "な")
    assert agree("は", "者") and agree("者", "は")  # 者 is the 字母 of a hentaigana は
    assert not agree("タ", "ヲ")
    assert not agree("禁", "制")


def test_the_vote_counts_other_labels_by_their_kana_and_their_documents():
    neighbours = [("ナ", 0.9, "a"), ("な", 0.95, "b"), ("ナ", 0.92, "a"), ("レ", 0.99, "c"), (None, 0.99, "d")]
    found = vote("レ", neighbours)
    assert found == Vote(own=1, own_similarity=0.99, target="ナ", count=3, similarity=pytest.approx(0.9233, abs=1e-3),
                         documents=2)


def test_the_gate_names_blank_joined_and_fragment_boxes():
    line = [(40, 40)] * 6
    assert gate((40, 40), line, 0.15) is None
    assert gate((40, 40), line, 0.01) == "blank"
    assert gate((40, 70), line, 0.15) == "joined"   # two characters down a vertical line
    assert gate((56, 56), line, 0.15) is None       # a large kanji, as wide as it is tall
    assert gate((12, 40), line, 0.15) == "fragment"  # a stroke
    assert gate((12, 40), line[:2], 0.15) is None    # too few boxes to judge by


def test_a_relabel_needs_the_neighbours_the_documents_the_classifier_and_the_gate():
    sure = Vote(own=0, own_similarity=0.0, target="ナ", count=9, similarity=0.95, documents=3)
    read = Read(p_label=0.01, p_target=0.97, ink=0.15)
    assert decide(sure, read, None, DEFAULT) == "propose"
    assert decide(Vote(0, 0.0, "ナ", 7, 0.95, 3), read, None, DEFAULT) == "vote"
    assert decide(Vote(2, 0.9, "ナ", 8, 0.95, 3), read, None, DEFAULT) == "vote"
    assert decide(Vote(0, 0.0, "ナ", 9, 0.8, 3), read, None, DEFAULT) == "vote"
    assert decide(Vote(0, 0.0, "ナ", 9, 0.95, 1), read, None, DEFAULT) == "documents"
    assert decide(sure, Read(0.4, 0.6, 0.15), None, DEFAULT) == "classifier"
    assert decide(sure, Read(None, None, 0.15), None, DEFAULT) == "classifier"
    assert decide(sure, read, "joined", DEFAULT) == "gate:joined"


def test_nearest_leaves_out_the_crop_and_its_page():
    vectors = np.array([[1, 0], [0.99, 0.14], [0.98, 0.2], [0, 1]], dtype=np.float32)
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    found, scores = nearest(vectors, [0], ["p1", "p1", "p2", "p3"], k=2)
    assert found.tolist() == [[2, 3]]
    assert scores[0, 0] > scores[0, 1]


def test_nearest_without_a_gpu(monkeypatch):
    import builtins

    real = builtins.__import__

    def no_torch(name, *args, **kwargs):
        if name == "torch":
            raise ImportError(name)
        return real(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_torch)
    test_nearest_leaves_out_the_crop_and_its_page()


def test_document_of_a_page():
    assert cr.document_of("hk:abc:12", "x") == "hk:abc"
    assert cr.document_of(None, "hi:1") == "hi:1"


PAGE, LINE = "hk:d:1", "hk:d:1:L1"


@pytest.fixture
def dataset(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("GLYPH_ATLAS_CACHE", str(tmp_path / "cache"))
    root = tmp_path / "dataset"
    root.mkdir()
    tables.write(root / "documents.parquet", [Document(id="d", title="資料")], Document)
    tables.write(root / "pages.parquet", [Page(id=PAGE, document_id="d", seq=0, image="x", width=300, height=300,
                                               sha256="0" * 64)], Page)
    tables.write(root / "lines.parquet", [Line(id=LINE, page_id=PAGE, seq=0, text_raw="レタ", text="レタ",
                                               box=Box(x=0, y=0, w=300, h=300))], Line)
    tables.write(root / "units.parquet", [
        Unit(id=f"{LINE}:u{i}", document_id="d", page_id=PAGE, line_id=LINE, seq=i, unicode=code,
             text_source=char, box=Box(x=20, y=10 + 50 * i, w=40, h=40))
        for i, (char, code) in enumerate((("レ", "U+30EC"), ("タ", "U+30BF")))], Unit)
    return root


def test_a_relabel_is_recorded_with_its_evidence_and_undone(dataset: Path):
    from glyph_atlas.review.atlas import written_identity
    from glyph_atlas.review.store import Store

    store = Store(dataset)
    units = {unit.id: (unit, revision) for unit, revision in store.unit_snapshot()}
    item = {"unit_id": f"{LINE}:u0", "before": "レ", "character": "ナ", "vote": {"count": 9}, "read": {"p_target": 0.97},
            "setting": "default", "status": "proposed"}
    cr.record(store, [item], units)
    assert item["status"] == "relabelled"
    unit = Store(dataset).unit_snapshot(f"{LINE}:u0")[0][0]
    assert written_identity(unit) == "ナ"
    assert unit.meta["feedback_identity"]["method"] == cr.METHOD
    assert unit.meta["feedback_identity"]["vote"] == {"count": 9}

    assert cr.undo(dataset)["counts"] == {"to restore": 1}  # a dry run writes nothing
    assert written_identity(Store(dataset).unit_snapshot(f"{LINE}:u0")[0][0]) == "ナ"
    result = cr.undo(dataset, apply=True)
    assert result["counts"] == {"restored": 1}
    unit = Store(dataset).unit_snapshot(f"{LINE}:u0")[0][0]
    assert written_identity(unit) == "レ"
    assert "feedback_identity" not in (unit.meta or {})
    # Undone once, a second undo finds nothing to restore.
    assert cr.undo(dataset, apply=True)["counts"] == {}


def test_a_relabel_made_again_after_an_undo_is_recorded_and_undone_again(dataset: Path):
    from glyph_atlas.review.atlas import written_identity
    from glyph_atlas.review.store import Store

    identity = f"{LINE}:u0"
    for _ in range(2):
        store = Store(dataset)
        units = {unit.id: (unit, revision) for unit, revision in store.unit_snapshot()}
        item = {"unit_id": identity, "before": "レ", "character": "ナ", "status": "proposed"}
        cr.record(store, [item], units)
        assert written_identity(Store(dataset).unit_snapshot(identity)[0][0]) == "ナ"
        assert cr.undo(dataset, apply=True)["counts"] == {"restored": 1}
        assert written_identity(Store(dataset).unit_snapshot(identity)[0][0]) == "レ"


def test_undo_keeps_what_another_pass_wrote_in_meta(dataset: Path):
    from glyph_atlas.review.refine import _changes
    from glyph_atlas.review.store import Store

    identity = f"{LINE}:u0"
    store = Store(dataset)
    units = {unit.id: (unit, revision) for unit, revision in store.unit_snapshot()}
    cr.record(store, [{"unit_id": identity, "before": "レ", "character": "ナ", "status": "proposed"}], units)
    unit, revision = Store(dataset).unit_snapshot(identity)[0]
    _changes(store, unit, {"meta": {**unit.meta, "alignment_repair": {"status": "withheld"}}},
             {"method": "other-pass"}, base_revision=revision)
    assert cr.undo(dataset, apply=True)["counts"] == {"restored": 1}
    unit = Store(dataset).unit_snapshot(identity)[0][0]
    assert unit.meta == {"alignment_repair": {"status": "withheld"}}


def test_undo_keeps_a_label_a_person_reviewed_since(dataset: Path):
    from glyph_atlas.review.store import ReviewRequest, Store

    store = Store(dataset)
    units = {unit.id: (unit, revision) for unit, revision in store.unit_snapshot()}
    identity = f"{LINE}:u0"
    cr.record(store, [{"unit_id": identity, "before": "レ", "character": "ナ", "status": "proposed"}], units)
    revision = store.revision(identity)
    store.record(ReviewRequest(target_id=identity, field="review", new="reviewed", base_revision=revision,
                               client_id="reviewer-1", idempotency_key="r1"))
    assert cr.undo(dataset, apply=True)["counts"] == {"changed since": 1}


def test_measured_against_the_site_reviews():
    def event(target, verdict, issue=None, character=None):
        evidence = {"kind": "character-review", "verdict": verdict, "issue": issue, "suggested_character": character}
        return {"event": {"target_id": target, "evidence": json.dumps(evidence)}}

    reviews = {"reviews": [event("a", "wrong", "character", "U+30CA"), event("b", "match"),
                           event("c", "wrong", "merged"), event("d", "wrong", "character", "U+30BF")]}
    result = {"items": [{"unit_id": "a", "character": "な"}, {"unit_id": "b", "character": "ナ"},
                        {"unit_id": "c", "character": "ナ"}, {"unit_id": "d", "character": "ナ"},
                        {"unit_id": "e", "character": "ナ"}]}
    measured = cr.evaluate(result, reviews)
    assert measured["relabelled"] == {"right": 1, "confirmed": 1, "merged": 1, "other": 1}
    assert measured["reviewed"] == {"character": 2, "confirmed": 1, "merged": 1}
