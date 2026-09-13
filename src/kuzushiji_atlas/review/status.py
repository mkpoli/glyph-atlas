"""What a review actually decided, as opposed to what it touched.

The store's own counters could not answer "how much has a person checked", and the reason is worth
recording because the mistake is easy to repeat. `Store.counts` counted rows in the `revisions` table,
and that table is written by every event: a note, a dwell time, an opened line, a draft box mid-edit.
So a reviewer who wrote one note on a page of four hundred units made the page look reviewed, and an
unfinished draft counted as progress.

This module derives the answer from the events themselves instead, under one rule: a unit is
**human checked** only when a person changed a field that carries a decision — a box they drew, a
reading they set, a review state they chose, an identity they confirmed. Notes, timings and drafts are
kept, because they are useful, but they are not evidence that anything was verified.

The counts a dashboard shows, and what each means:

* `machine` — the pipeline accepted the unit and no person has decided anything about it. This is
  machine output, and its accuracy is the pipeline's measured precision, not a promise.
* `checked` — a person recorded an explicit decision on it. How many of those decisions are right is
  a separate question that only an audit answers.
* `draft` — events exist (a box moved, a note written) but no decision field was confirmed. Work in
  progress, deliberately not counted as progress.
* `unresolved` — rejected units on pages somebody has not resolved: the queue.
* `retired` — a later operation retired the unit.

**No percentage is derived here.** A page with nothing checked is not a page with zero precision; it
is a page whose quality is unmeasured, and the dashboard says so rather than showing 0%.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Literal

#: The fields a decision is made in. A change to one of these is a person saying what the record is;
#: everything else the journal holds — `note`, `timing`, a crop preview, an opened line — is context.
DECISION_FIELDS = frozenset(
    {
        "box",
        "reading",
        "text_source",
        "unicode",
        "jibo",
        "kind",
        "granularity",
        "script",
        "classification",
        "review",
        "active",
        "split_into",
        "merged_into",
        "group_id",
        "antecedent_ids",
        "voicing",
        "crop",
        "meta",
    }
)

#: Events that are context by construction, whatever field they name.
CONTEXT_FIELDS = frozenset({"note", "timing", "open", "leave"})

#: The review states a person can set on a unit, as opposed to the ones the pipeline writes.
HUMAN_REVIEW_STATES = frozenset({"reviewed", "double-reviewed", "adjudicated", "disputed"})

Kind = Literal["machine", "checked", "draft", "unresolved", "retired"]


def is_decision(field: str) -> bool:
    """Whether a change to `field` is a person deciding something about the record."""
    return field in DECISION_FIELDS and field not in CONTEXT_FIELDS


@dataclass
class UnitReview:
    """One unit's review standing, and the evidence for it."""

    unit_id: str
    page_id: str | None = None
    document_id: str | None = None
    machine_review: str = "machine"
    active: bool = True
    decisions: list[str] = field(default_factory=list)
    context: list[str] = field(default_factory=list)
    actor: str | None = None

    @property
    def checked(self) -> bool:
        """A person decided something here."""
        return bool(self.decisions)

    @property
    def drafted(self) -> bool:
        """Something was touched but nothing was decided."""
        return not self.decisions and bool(self.context)

    @property
    def kind(self) -> Kind:
        if not self.active:
            return "retired"
        if self.checked:
            return "checked"
        if self.drafted:
            return "draft"
        if self.machine_review == "rejected":
            return "unresolved"
        return "machine"


def unit_reviews(
    units: Iterable[Any], events: Iterable[Any]
) -> dict[str, UnitReview]:
    """Every unit's standing, from the units and the journal.

    `units` are the records as they now stand (anything with `id`, `page_id`, `document_id`,
    `review` and `active`), and `events` the journal rows (`target_id`, `field`, `actor`). A unit with
    no event is either accepted machine output or a rejection waiting to be resolved, and the two are
    told apart by the state the pipeline wrote, not by whether anyone looked at it.
    """
    standing: dict[str, UnitReview] = {}
    for unit in units:
        review = getattr(unit.review, "value", unit.review)
        standing[unit.id] = UnitReview(
            unit_id=unit.id,
            page_id=getattr(unit, "page_id", None),
            document_id=getattr(unit, "document_id", None),
            machine_review=str(review),
            active=bool(getattr(unit, "active", True)),
        )
    for event in events:
        record = standing.get(event.target_id)
        if record is None:
            continue
        if is_decision(event.field):
            record.decisions.append(event.field)
            if event.actor:
                record.actor = event.actor
        else:
            record.context.append(event.field)
    return standing


def summarize(standing: Iterable[UnitReview]) -> dict[str, int]:
    """The counts a dashboard shows, with machine output and human decisions kept apart."""
    counts: Counter[str] = Counter()
    for record in standing:
        counts[record.kind] += 1
    return {
        "machine": counts["machine"],
        "checked": counts["checked"],
        "draft": counts["draft"],
        "unresolved": counts["unresolved"],
        "retired": counts["retired"],
        "total": sum(counts.values()),
    }


def by_page(standing: Iterable[UnitReview]) -> dict[str, dict[str, int]]:
    """The same summary, per page, so a page list can show what is left to do."""
    grouped: dict[str, list[UnitReview]] = {}
    for record in standing:
        grouped.setdefault(record.page_id or "", []).append(record)
    return {page_id: summarize(records) for page_id, records in grouped.items()}


def quality(checked: int, decided_correct: int | None) -> dict[str, Any]:
    """What can honestly be said about quality from `checked` decisions.

    `decided_correct` is how many of the checked decisions an audit found right, and it is None until
    somebody scores a sample. With no audit the rate is reported as unavailable rather than as zero:
    an unchecked page is unmeasured, and a dashboard that printed 0% for it would be inventing a
    measurement. The interval is the Wilson one the audit report uses, so the two agree.
    """
    if not checked or decided_correct is None:
        return {"state": "unmeasured", "checked": checked, "correct": None, "rate": None,
                "interval": None, "note": "no scored audit sample yet"}
    from ..evaluate import wilson

    low, high = wilson(decided_correct, checked)
    return {
        "state": "measured",
        "checked": checked,
        "correct": decided_correct,
        "rate": decided_correct / checked,
        "interval": [low, high],
        "note": "from the scored audit sample",
    }
