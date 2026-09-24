"""A machine split is recorded as machine work: its own role, ids, provenance and boxes.

`Store.record_batch(requests, role=...)` is the server's own statement of who is answering — it is not
a field of a request, so a body cannot claim to be a model. With `role="model"` a split writes
children that say `detect-align`/`machine`, carry the run they came from, and state their own
identity and box; a reviewer's split is untouched: `manual`, `transcriber`, reviewer ids.

The tests drive the real `Store` over a temporary dataset, and check the journal, the replay and the
refusals rather than the shape of the code.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from glyph_atlas import tables
from glyph_atlas.review.store import BadRequest, Conflict, ReviewRequest, Store, apply, replay
from glyph_atlas.schema import Box, Document, Line, Page, ReviewState, Unit

PAGE = "doc:p1"
LINE = f"{PAGE}:l0"
#: トモ written as one glyph: the case a split exists for.
JOINED = f"{LINE}:u0"
PARENT_BOX = Box(x=100, y=100, w=80, h=40)
EVIDENCE = json.dumps({"valleys": [{"x": 140, "depth": 0.61, "width": 3}],
                       "ocr": [{"text": "ト", "engine": "fixture", "score": 0.91},
                               {"text": "モ", "engine": "fixture", "score": 0.88}]},
                      ensure_ascii=False)


def test_encoded_ligature_is_never_split_by_a_model(dataset):
    store = Store(dataset)
    store.record(ReviewRequest(target_id=JOINED, field="unicode", new="U+2A708", client_id="person"))
    with pytest.raises(BadRequest, match="ligature"):
        store.record_batch([split_request(revision=1)], role="model")
    assert store.unit(JOINED).active


@pytest.fixture
def dataset(tmp_path: Path) -> Path:
    root = tmp_path / "dataset"
    root.mkdir()
    tables.write(root / "documents.parquet", [Document(id="doc", title="資料")], Document)
    tables.write(root / "pages.parquet", [Page(id=PAGE, document_id="doc", seq=0, image="x",
                 width=400, height=400, sha256="e" * 64)], Page)
    tables.write(root / "lines.parquet", [Line(id=LINE, page_id=PAGE, seq=0, text_raw="トモ",
                 text="トモ", box=Box(x=60, y=60, w=240, h=200))], Line)
    tables.write(root / "units.parquet", [
        Unit(id=JOINED, document_id="doc", page_id=PAGE, line_id=LINE, seq=0, unicode="U+51FA",
             reading="出", text_source="出", box=PARENT_BOX, crop="https://example.org/crop.jpg",
             crop_sha256="a" * 64, granularity="char", kind="char",
             script="han", classification="identified", group_id="g1", antecedent_ids=["x"],
             meta={"alignment_repair": {"from": "U+30C8"}, "sample": "s0"}),
        Unit(id=f"{LINE}:u1", document_id="doc", page_id=PAGE, line_id=LINE, seq=1, unicode="U+30CD",
             reading="ね", text_source="ネ", box=Box(x=200, y=100, w=30, h=40)),
    ], Unit)
    return root


def split_request(*, revision: int = 0, entries: list[dict] | None = None,
                  key: str = "split-1") -> ReviewRequest:
    return ReviewRequest(
        target_type="unit", target_id=JOINED, field="segmentation",
        new={"split": entries if entries is not None else [
            {"box": {"x": 100, "y": 100, "w": 38, "h": 40}, "unicode": "U+30C8", "reading": "ト"},
            {"box": {"x": 142, "y": 100, "w": 38, "h": 40}, "unicode": "U+30E2", "reading": "モ"},
        ]},
        base_revision=revision, client_id="splitter-v1", idempotency_key=key, evidence=EVIDENCE)


NEIGHBOUR = f"{LINE}:u1"


def children_of(directory: Path) -> list[Unit]:
    """The units a split created, which are the ones that are neither the parent nor the neighbour."""
    return [unit for unit, _ in Store(directory).unit_snapshot()
            if unit.active and unit.id.startswith(f"{LINE}:") and unit.id != NEIGHBOUR]


def event_of(directory: Path, field: str = "segmentation"):
    return next(event for event in Store(directory).events() if event.field == field)


# The role ---------------------------------------------------------------------------------------

def test_a_model_batch_records_the_model_role_and_the_actor(dataset):
    store = Store(dataset)
    result = store.record_batch([ReviewRequest(
        target_type="unit", target_id=f"{LINE}:u1", field="reading", new="ネ",
        base_revision=0, client_id="pipeline-1", idempotency_key="m1")], role="model")
    assert result[0]["review"]["role"] == "model"
    assert result[0]["review"]["actor"] == "pipeline-1"
    assert Store(dataset).events()[0].role == "model"

    store.record_batch([ReviewRequest(
        target_type="unit", target_id=f"{LINE}:u1", field="reading", new="ね",
        base_revision=1, client_id="reviewer-1", idempotency_key="m2")])
    roles = [event.role for event in Store(dataset).events()]
    assert roles == ["model", "reviewer"], "the default role is still the reviewer"


def test_the_role_is_not_a_field_of_a_request(dataset):
    """A body cannot claim to be a model: the role reaches the store only as an argument."""
    with pytest.raises(ValidationError):
        ReviewRequest(target_type="unit", target_id=JOINED, field="segmentation",
                      new={"split": []}, base_revision=0, client_id="c", idempotency_key="k",
                      role="model")  # type: ignore[call-arg]
    with pytest.raises(BadRequest):
        Store(dataset).record_batch([split_request()], role="supervisor")  # type: ignore[arg-type]


# A machine split --------------------------------------------------------------------------------

def test_a_machine_split_is_detect_align_machine_and_provenanced(dataset):
    store = Store(dataset)
    results = store.record_batch([split_request()], role="model")
    assert len(results[0]["created"]) == 2

    children = sorted(children_of(dataset), key=lambda unit: unit.id)
    assert [unit.id for unit in children] == [f"{LINE}:{_run(dataset)}:0", f"{LINE}:{_run(dataset)}:1"]
    for index, child in enumerate(children):
        assert child.method == "detect-align", "a machine split is not a transcriber's work"
        assert child.review is ReviewState.MACHINE
        assert child.unicode == ("U+30C8", "U+30E2")[index]
        assert child.reading == ("ト", "モ")[index]
        assert child.meta["feedback_split"] == {
            "parent_id": JOINED, "evidence_sha256": hashlib.sha256(EVIDENCE.encode()).hexdigest(),
            "model": "splitter-v1", "automated": True}
        assert child.confidence is None and child.candidates == [], "the parent's scoring is not the child's"
        assert child.variants == [] and child.group_id is None and child.antecedent_ids == [], "parent relations are cleared"
    parent = next(unit for unit, _ in Store(dataset).unit_snapshot() if unit.id == JOINED)
    assert parent.active is False and parent.split_into == [child.id for child in children]
    assert "U+30C8" not in parent.unicode


def test_a_machine_child_does_not_inherit_the_parent_crop_or_repair_note(dataset):
    Store(dataset).record_batch([split_request()], role="model")
    for child in children_of(dataset):
        assert child.crop is None and child.crop_sha256 is None, "the parent's picture is not the child's"
        assert "alignment_repair" not in child.meta
        assert child.meta["sample"] == "s0", "unrelated metadata still travels"


def test_the_same_run_over_the_same_evidence_mints_the_same_children(dataset):
    """A rerun writes no second generation: the ids come from the parent and the evidence."""
    Store(dataset).record_batch([split_request()], role="model")
    first = sorted(unit.id for unit in children_of(dataset))

    apply(dataset)
    replay(dataset)
    rebuilt = sorted(unit.id for unit in children_of(dataset))
    assert rebuilt == first, "the replay rebuilds the same children"

    # A second caller with the same evidence hits the retired parent and is refused, not duplicated.
    with pytest.raises(Conflict):
        Store(dataset).record_batch([split_request(key="split-2")], role="model")
    assert sorted(unit.id for unit in children_of(dataset)) == first


def test_a_repeat_of_the_same_submission_is_one_split(dataset):
    store = Store(dataset)
    store.record_batch([split_request()], role="model")
    again = store.record_batch([split_request()], role="model")
    assert again[0]["duplicate"] is True
    assert len([unit for unit, _ in Store(dataset).unit_snapshot()
                if unit.active and unit.id.startswith(f"{LINE}:")]) == 3, "two children and a neighbour"
    assert len([event for event in Store(dataset).events() if event.field == "segmentation"]) == 1


def test_a_stale_revision_is_refused(dataset):
    with pytest.raises(Conflict):
        Store(dataset).record_batch([split_request(revision=7)], role="model")


def test_the_caller_evidence_is_kept_exactly(dataset):
    Store(dataset).record_batch([split_request()], role="model")
    assert event_of(dataset).evidence == EVIDENCE


# What a machine split refuses -------------------------------------------------------------------

def test_every_machine_child_states_its_own_identity_and_box(dataset):
    for entry in ({"box": {"x": 100, "y": 100, "w": 38, "h": 40}},
                  {"unicode": "U+30C8", "reading": "ト"},
                  {"box": {"x": 100, "y": 100, "w": 38, "h": 40}, "unicode": "U+30C8"},
                  {"box": {"x": 100, "y": 100, "w": 38, "h": 40}, "reading": "ト"}):
        with pytest.raises(BadRequest):
            Store(dataset).record_batch([split_request(entries=[
                entry, {"box": {"x": 142, "y": 100, "w": 38, "h": 40}, "unicode": "U+30E2",
                        "reading": "モ"}], key=f"bad-{len(entry)}")], role="model")


def test_overlapping_children_are_refused(dataset):
    with pytest.raises(BadRequest):
        Store(dataset).record_batch([split_request(entries=[
            {"box": {"x": 100, "y": 100, "w": 60, "h": 40}, "unicode": "U+30C8", "reading": "ト"},
            {"box": {"x": 120, "y": 100, "w": 60, "h": 40}, "unicode": "U+30E2", "reading": "モ"},
        ])], role="model")


def test_a_child_outside_the_parent_is_refused(dataset):
    with pytest.raises(BadRequest):
        Store(dataset).record_batch([split_request(entries=[
            {"box": {"x": 100, "y": 100, "w": 38, "h": 40}, "unicode": "U+30C8", "reading": "ト"},
            {"box": {"x": 200, "y": 100, "w": 40, "h": 40}, "unicode": "U+30E2", "reading": "モ"},
        ])], role="model")


def test_a_child_covering_a_neighbour_is_refused(dataset):
    """A glyph already recorded inside the same pixels is not part of this split's evidence."""
    tables.write(dataset / "units.parquet", [
        next(unit for unit, _ in Store(dataset).unit_snapshot() if unit.id == JOINED),
        Unit(id=NEIGHBOUR, document_id="doc", page_id=PAGE, line_id=LINE, seq=1, unicode="U+30CD",
             reading="ね", text_source="ネ", box=Box(x=150, y=110, w=20, h=20)),
    ], Unit)
    with pytest.raises(BadRequest):
        Store(dataset).record_batch([split_request()], role="model")


# A reviewer's split is unchanged -----------------------------------------------------------------

def test_a_reviewer_split_still_reads_manual_and_transcriber(dataset):
    results = Store(dataset).record_batch([split_request()])
    assert len(results[0]["created"]) == 2
    children = sorted(children_of(dataset), key=lambda unit: unit.id)
    assert [unit.id for unit in children] == [f"{LINE}:m1", f"{LINE}:m2"], "the reviewer's numbering"
    for child in children:
        assert child.method == "manual" and child.review is ReviewState.TRANSCRIBER
        assert "feedback_split" not in child.meta
        assert child.crop == "https://example.org/crop.jpg", "a reviewer's child keeps what it had"
        assert child.meta["alignment_repair"] == {"from": "U+30C8"}
    assert Store(dataset).events()[0].role == "reviewer"


def _run(directory: Path) -> str:
    """The run fragment the children were minted with, read back from the journal."""
    evidence = event_of(directory).evidence or ""
    digest = hashlib.sha256(evidence.encode("utf-8")).hexdigest()
    return hashlib.sha256(f"{JOINED}:{digest}".encode()).hexdigest()[:8]


def test_partial_split_keeps_connected_child_as_sequence(dataset):
    store = Store(dataset)
    request = split_request(entries=[
        {"box": {"x": 100, "y": 100, "w": 38, "h": 40},
         "unicode": "U+3068 U+308A", "reading": "とり", "text_source": "とり"},
        {"box": {"x": 142, "y": 100, "w": 38, "h": 40},
         "unicode": "U+3044", "reading": "い", "text_source": "い"},
    ])
    store.record_batch([request], role="model")
    children = sorted(children_of(dataset), key=lambda unit: unit.id)
    assert children[0].granularity == "sequence" and children[0].kind.value == "sequence"
    assert children[1].granularity == "char" and children[1].reading == "い"
    apply(dataset)
    replay(dataset)
    assert children_of(dataset)[0].granularity == "sequence"


# Neighbours on other lines and the child's own fields --------------------------------------------

OTHER_LINE = f"{PAGE}:l1"


def add_other_line(dataset: Path, box: Box) -> None:
    """A second line on the page, with one unit at `box`, as a later alignment could write it."""
    lines = tables.read(dataset / "lines.parquet", Line)
    tables.write(dataset / "lines.parquet", [*lines, Line(id=OTHER_LINE, page_id=PAGE, seq=1, text_raw="一",
                 text="一", box=Box(x=100, y=100, w=40, h=40))], Line)
    units = tables.read(dataset / "units.parquet", Unit)
    tables.write(dataset / "units.parquet", [*units, Unit(id=f"{OTHER_LINE}:u0", document_id="doc", page_id=PAGE,
                 line_id=OTHER_LINE, seq=0, unicode="U+4E00", reading="一", box=box)], Unit)


def test_a_machine_child_may_not_cover_a_unit_of_another_line(dataset):
    add_other_line(dataset, Box(x=110, y=110, w=10, h=10))
    store = Store(dataset)
    with pytest.raises(BadRequest, match="covers"):
        store.record_batch([split_request()], role="model")
    assert store.unit(JOINED).active


def test_a_split_the_new_tables_leave_no_room_for_is_skipped_on_replay(dataset):
    aligned = tables.read(dataset / "units.parquet", Unit)
    Store(dataset).record_batch([split_request()], role="model")
    apply(dataset)
    # A re-alignment writes the page again: the joined unit as it was, and a unit of a new line
    # where the split's first child would go.
    tables.write(dataset / "units.parquet", aligned, Unit)
    add_other_line(dataset, Box(x=110, y=110, w=10, h=10))
    store = Store(dataset)  # opens, and leaves the split that no longer fits unapplied
    assert store.unit(JOINED).active and store.unit(f"{OTHER_LINE}:u0").active


def test_a_machine_child_carries_its_own_text_and_script(dataset):
    Store(dataset).record_batch([split_request()], role="model")
    children = sorted(children_of(dataset), key=lambda unit: unit.box.x)
    assert [(child.text_source, child.script.value) for child in children] == [("ト", "katakana"), ("モ", "katakana")]
