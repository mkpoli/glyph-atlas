"""Tests for `kuzushiji_atlas.review.status`: what a review decided, from the state it left.

Each test here is a case the first version of the module got wrong. They use plain objects rather than
the store, because the question is what a given journal means, not how the journal is written.

The distinction under test is between *activity* and a *decision*. A note, a timing event, a crop
nudged into place and a metadata write all show that somebody was there; none of them says the reading
is right or the boundary is right. A dashboard that counted them would report progress that does not
exist, which is worse than reporting none.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from kuzushiji_atlas.review import status


@dataclass
class Unit:
    """A unit as the store holds it."""

    id: str = "u1"
    page_id: str = "p1"
    document_id: str = "d1"
    review: str = "machine"
    active: bool = True
    reading: str | None = None
    unicode: str | None = None
    text_source: str | None = None
    box: Any = None


@dataclass
class Event:
    """One journal row."""

    target_id: str
    field: str
    new: Any = None
    actor: str | None = None
    old: Any = None
    note: str = ""


def review_of(unit: Unit, events: list[Event], **kwargs: Any) -> status.UnitReview:
    return status.unit_reviews([unit], events, **kwargs)[unit.id]


def test_an_imported_review_is_a_state_the_record_carries() -> None:
    """A unit that arrives already reviewed is reviewed, not untouched machine output.

    The atlas imports records from projects that carry their own review states, and `atlas review
    apply` writes reviewed units back into the tables. Such a unit has no local event at all, and the
    first version of this module therefore called it `machine` — reporting a decision somebody made as
    if the pipeline had just produced it.
    """
    unit = Unit(review="reviewed")
    standing = review_of(unit, [])
    assert standing.kind == "checked"
    assert standing.verified == ["review"], "the review state is the decision that is recorded"
    assert standing.fields["review"].author == "import", "and it is marked as imported, not local"


def test_an_apply_written_review_is_told_apart_from_an_imported_one() -> None:
    """Both are states the record carries, and which one it is stays visible."""
    unit = Unit(review="adjudicated")
    standing = review_of(unit, [], exported=[unit.id])
    assert standing.kind == "checked"
    assert standing.fields["review"].author == "export"


def test_metadata_without_an_actor_is_not_a_decision() -> None:
    """Audit bookkeeping and pipeline writes must not look like somebody confirming a reading.

    Two mistakes are possible and both are tested: a metadata write is not a reading even when a
    person made it, and an event with no actor is the pipeline's own conclusion even when it touches a
    field a person could have decided.
    """
    machine_meta = review_of(Unit(), [Event("u1", "meta", {"source": "audit"}, actor=None)])
    assert machine_meta.kind == "machine", "an actorless write is not evidence anybody looked"
    assert machine_meta.adjusted == []

    person_meta = review_of(Unit(), [Event("u1", "meta", {"note": "seen"}, actor="r1")])
    assert person_meta.kind == "draft", "a person's metadata write is activity, not verification"
    assert person_meta.adjusted == ["meta"]
    assert person_meta.verified == []


def test_a_crop_adjustment_does_not_verify_the_reading() -> None:
    """Moving a boundary is not the same decision as saying the character reads as something."""
    moved = review_of(Unit(), [Event("u1", "box", {"x": 1, "y": 2, "w": 3, "h": 4}, actor="r1")])
    assert moved.kind == "draft"
    assert moved.adjusted == ["box"] and moved.verified == []

    read = review_of(Unit(), [Event("u1", "reading", "あ", actor="r1")])
    assert read.kind == "checked" and read.verified == ["reading"]


def test_an_undo_takes_the_checked_state_away() -> None:
    """The state a person set, then reverted, is not a decision the record still shows.

    The first version accumulated every field ever touched, so undoing a review left the dashboard
    saying it was checked forever. A status describes the record as it now stands, so the same journal
    that made it checked has to be able to make it unchecked again.
    """
    events = [
        Event("u1", "review", "reviewed", actor="r1", old="machine"),
        Event("u1", "review", "machine", actor="r1", old="reviewed"),
    ]
    standing = review_of(Unit(review="machine"), events)
    assert standing.kind == "machine", "the record is back to what the pipeline wrote"
    assert standing.verified == [] and standing.human_review is None

    cleared = review_of(Unit(review="machine"), [
        Event("u1", "reading", "あ", actor="r1"),
        Event("u1", "reading", None, actor="r1", old="あ"),
    ])
    assert cleared.kind == "machine", "a reading cleared by its author is not a verification"


def test_counts_reverse_after_an_undo() -> None:
    """The counters a dashboard shows have to move back, which is the point of the test above."""
    before = review_of(Unit(), [])
    after = review_of(Unit(), [Event("u1", "review", "reviewed", actor="r1")])
    undone = review_of(Unit(review="machine"), [
        Event("u1", "review", "reviewed", actor="r1", old="machine"),
        Event("u1", "review", "machine", actor="r1", old="reviewed"),
    ])
    assert status.summarize([before]) == {**dict.fromkeys(
        ("machine", "checked", "draft", "unresolved", "retired"), 0), "machine": 1, "total": 1}
    assert status.summarize([after])["checked"] == 1
    assert status.summarize([undone])["checked"] == 0, "the counter moves back with the record"
    assert status.summarize([undone])["machine"] == 1


def test_a_pipeline_write_takes_the_decision_back() -> None:
    """An apply or a rerun that writes its own value is the last word on that field."""
    standing = review_of(Unit(review="machine"), [
        Event("u1", "reading", "あ", actor="r1"),
        Event("u1", "reading", "い", actor=None),
    ])
    assert standing.kind == "machine"
    assert standing.fields["reading"].author == "machine"
    assert standing.verified == []


def test_a_rejected_unit_nobody_touched_is_the_queue() -> None:
    """Unresolved is the pipeline's own rejection, waiting for somebody to look."""
    assert review_of(Unit(review="rejected"), []).kind == "unresolved"
    assert review_of(Unit(review="rejected"), [Event("u1", "note", "?", actor="r1")]).kind == "draft"


def test_a_retired_unit_is_retired_whatever_else_is_true() -> None:
    """A later split or merge retired it, so it is not outstanding work."""
    standing = review_of(Unit(active=False, review="reviewed"), [])
    assert standing.kind == "retired"


def test_the_summaries_group_by_page_and_document() -> None:
    """The per-page and per-document views add up to the whole."""
    units = [
        Unit(id="u1", page_id="p1", document_id="d1"),
        Unit(id="u2", page_id="p1", document_id="d1", review="reviewed"),
        Unit(id="u3", page_id="p2", document_id="d2", review="rejected"),
    ]
    standing = status.unit_reviews(units, [])
    assert status.summarize(standing.values()) == {"machine": 1, "checked": 1, "draft": 0,
                                                   "unresolved": 1, "retired": 0, "total": 3}
    pages = status.by_page(standing.values())
    assert pages["p1"]["checked"] == 1 and pages["p2"]["unresolved"] == 1
    documents = status.by_document(standing.values())
    assert documents["d1"]["total"] == 2 and documents["d2"]["total"] == 1


def test_quality_is_unmeasured_until_an_audit_is_scored() -> None:
    """No percentage is invented for a page nobody has checked, and a scored one is reported with its
    interval rather than as a bare number."""
    unavailable = status.quality(0, None)
    assert unavailable["state"] == "unmeasured" and unavailable["rate"] is None
    assert "unmeasured" not in str(unavailable["rate"])

    measured = status.quality(40, 36)
    assert measured["state"] == "measured" and measured["rate"] == 0.9
    low, high = measured["interval"]
    assert low < 0.9 < high and 0.0 <= low and high <= 1.0
