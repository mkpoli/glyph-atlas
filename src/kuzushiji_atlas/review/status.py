"""What a review actually decided, as opposed to what it touched.

The first version of this module had three ways of being wrong, and they are worth stating because
each is a plausible shortcut.

* It ignored the review state a record already carries. A unit the atlas imported as `reviewed`, or
  one an `atlas review apply` wrote back, has no local event at all, so it was reported as untouched
  machine output.
* It treated every event on a decision field as a person deciding. A metadata write or a crop
  adjustment is not a reading, and an event with no actor is not evidence that anybody looked.
* It accumulated the fields ever touched, so an undo could not take `checked` away: the history kept
  saying a person had been there even after the record was back to what the pipeline wrote.

The lesson in all three is that a status is a statement about the *current* record, so this module
replays the journal to the state the record is in now and asks what that state is made of. Undo is
then an ordinary case rather than a special one: the value a person set is no longer the value, so
their decision is no longer what the record says.

An **atlas review is an editorial decision**, in the words of the publishing project's own README: it
records that a decision was made, not that the person who made it was human. Nothing here claims
authorship; `actor` carries whoever the client said it was, or nothing.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Literal

#: Fields whose value a person can decide. A change to one of these *may* be a decision; whether it
#: is one depends on what the value says, which is what `_kind_of` settles.
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
        "crop_sha256",
        "meta",
    }
)

#: Events that are context by construction, whatever field they name: they record that somebody
#: looked, wrote, or spent time, and none of them changes what the record says.
CONTEXT_FIELDS = frozenset({"note", "timing", "open", "leave"})

#: The review states a person can choose. The value the pipeline writes is not one of them.
HUMAN_REVIEW_STATES = frozenset({"reviewed", "double-reviewed", "adjudicated", "disputed"})

#: Fields that verify a reading, a boundary or an identity, as opposed to tidying the record.
VERIFYING_FIELDS = frozenset(
    {"reading", "unicode", "text_source", "jibo", "classification", "script", "voicing"}
)
#: Fields that describe the crop or the bookkeeping. A change to one is not a reading.
ADJUSTING_FIELDS = frozenset(
    {"box", "crop", "crop_sha256", "meta", "group_id", "granularity", "antecedent_ids",
     "split_into", "merged_into", "active"}
)

Kind = Literal["machine", "checked", "draft", "unresolved", "retired"]
DecisionKind = Literal["verified", "adjusted", "state", "restored", "context"]


def is_decision(field_name: str) -> bool:
    """Whether a change to `field_name` can decide something about the record."""
    return field_name in DECISION_FIELDS and field_name not in CONTEXT_FIELDS


def _value_of(record: Any, field_name: str) -> Any:
    """One field of a record, as a plain value for comparison."""
    value = getattr(record, field_name, None)
    return getattr(value, "value", value)


def _kind_of(field_name: str, value: Any, initial: Any = None, *, seen: bool = False) -> DecisionKind | None:
    """What a value written into `field_name` says, or None when it says nothing about the record.

    A review state is the clearest case: `reviewed`, `adjudicated` and `disputed` are choices a person
    makes, while `machine` and `rejected` are what the pipeline concluded. A reading field verifies
    when it carries a value and undoes when it is cleared, which is how removing a reading takes a
    verification away. A crop or a metadata write adjusts the record without claiming anything was
    read, and a change to something outside both sets says nothing at all.

    `initial` is the value the record started with. Writing that value back is an undo rather than a
    decision, which is the case that decides whether the dashboard can be trusted after one: undoing a
    reading the pipeline wrote must restore the pipeline's provenance, not claim a person confirmed it.
    `seen` says whether the field has had a human decision already, so the first write of a value is a
    decision and only a later return to the baseline is an undo.
    """
    if seen and field_name in VERIFYING_FIELDS and value == initial and value not in (None, "", [], {}):
        return "restored"
    if field_name == "review":
        return "verified" if str(value) in HUMAN_REVIEW_STATES else "state"
    if field_name in VERIFYING_FIELDS:
        return None if value in (None, "", [], {}) else "verified"
    if field_name in ADJUSTING_FIELDS:
        return "adjusted"
    return None


#: Where a field's current value came from. `human` is a person deciding through a client; the other
#: two are states the record arrived with, from an import or from an earlier apply, and they are the
#: record's own value rather than machine output to be ignored.
LOCAL_AUTHORS = frozenset({"import", "export"})


@dataclass
class FieldStanding:
    """What a field currently says and who last wrote it."""

    value: Any = None
    author: str = "import"
    kinds: list[DecisionKind] = field(default_factory=list)

    @property
    def decided_by_person(self) -> bool:
        """A person decided this, through a client, in this store."""
        return self.author == "human"

    @property
    def decided_locally(self) -> bool:
        """A decision this field currently carries, whoever recorded it.

        An imported or applied *review state* is editorial standing and counts; a baseline value from
        the detector or the classifier does not, which is why `kinds` has to carry a decision as well
        as `author` saying where it came from.
        """
        return bool(self.kinds) and (self.author == "human" or self.author in LOCAL_AUTHORS)

    @property
    def last(self) -> DecisionKind | None:
        return self.kinds[-1] if self.kinds else None


@dataclass
class UnitReview:
    """One unit's review standing, and the evidence for it."""

    unit_id: str
    page_id: str | None = None
    document_id: str | None = None
    #: The `review` value the record currently carries, whatever wrote it.
    review_state: str = "machine"
    active: bool = True
    actor: str | None = None
    fields: dict[str, FieldStanding] = field(default_factory=dict)
    #: What each field held before any event touched it: the pipeline's own value, or what the record
    #: arrived with. A person writing this value back has undone their edit rather than decided
    #: anything, and the distinction cannot be made from the previous value alone.
    baseline: dict[str, Any] = field(default_factory=dict)
    context: list[str] = field(default_factory=list)

    @property
    def verified(self) -> list[str]:
        """The decisions the record currently shows a person made.

        A field counts when the value standing there is one a person could have chosen and the last
        word on it was not the pipeline's own. That includes a state the record arrived with: an
        imported `reviewed` is a decision somebody made, whether or not this store made it.
        """
        return sorted(
            name
            for name, standing in self.fields.items()
            if standing.decided_locally and standing.last == "verified"
        )

    @property
    def adjusted(self) -> list[str]:
        """Fields a person changed without verifying anything: a crop, metadata, a group."""
        return sorted(
            name
            for name, standing in self.fields.items()
            if standing.decided_by_person and standing.last == "adjusted"
        )

    @property
    def human_review(self) -> str | None:
        """The review state the record currently shows, if a person chose it.

        `None` means the record does not stand reviewed — not that nobody ever looked. The record can
        carry a review state the pipeline wrote (`rejected`, or `machine` after an undo), and neither
        of those is somebody's decision.
        """
        value = self.review_state
        standing = self.fields.get("review")
        if standing is not None and not standing.decided_locally:
            return None
        return value if value in HUMAN_REVIEW_STATES else None

    @property
    def checked(self) -> bool:
        """A decision is what the record currently says.

        Either a person's reading or identity stands, or the review state itself is one a person
        chose. The state has to count on its own: a record can be marked reviewed with no field-level
        detail, which is exactly what an import or an apply writes.
        """
        return self.human_review is not None or bool(self.verified)

    @property
    def drafted(self) -> bool:
        """Something was touched but nothing currently stands as a decision."""
        return not self.checked and (bool(self.adjusted) or bool(self.context))

    @property
    def kind(self) -> Kind:
        if not self.active:
            return "retired"
        if self.checked:
            return "checked"
        if self.drafted:
            return "draft"
        if self.review_state == "rejected":
            return "unresolved"
        return "machine"


def unit_reviews(
    units: Iterable[Any], events: Iterable[Any], *, exported: Iterable[str] = ()
) -> dict[str, UnitReview]:
    """Every unit's standing, from the units and the journal, replayed to the present.

    `units` are the records as they now stand and `events` the journal rows, oldest first, each with
    `target_id`, `field`, `new` and an optional `actor`. `exported` names units whose present state was
    written by an apply rather than imported, which is recorded in the standing's author so a caller
    can tell the two apart. A unit whose journal ends with a person's reading, or with a review state
    a person chose, is checked; one whose last word on every decision field is machine output or a
    reverted value is not, whether or not a person was there earlier.
    """
    written_back = set(exported)
    standing: dict[str, UnitReview] = {}
    for unit in units:
        review = str(getattr(unit.review, "value", unit.review))
        record = UnitReview(
            unit_id=unit.id,
            page_id=getattr(unit, "page_id", None),
            document_id=getattr(unit, "document_id", None),
            review_state=review,
            active=bool(getattr(unit, "active", True)),
        )
        # What the record already says: an imported review is a state the record carries, not an
        # absence of one, so it is the baseline the journal is applied over.
        record.fields["review"] = FieldStanding(
            value=review,
            author="export" if unit.id in written_back else "import",
            kinds=["verified" if review in HUMAN_REVIEW_STATES else "state"],
        )
        # A field's value is a baseline, not evidence. The detector put a box there and the
        # classifier put a reading there; neither is a person confirming anything, so these fields
        # start with no decision recorded. Only an explicit review state the record already carries
        # is editorial standing, and only a journal event can make a field a decision.
        for name in ("reading", "unicode", "text_source", "box", "jibo", "classification"):
            if hasattr(unit, name):
                value = _value_of(unit, name)
                record.fields[name] = FieldStanding(value=value, kinds=[])
                record.baseline[name] = value
        standing[unit.id] = record

    for event in events:
        record = standing.get(event.target_id)
        if record is None:
            continue
        field_name = event.field
        if field_name in CONTEXT_FIELDS:
            record.context.append(field_name)
            continue
        if field_name not in DECISION_FIELDS:
            continue
        before = record.fields.get(field_name)
        seen = bool(before.kinds) if before else False
        kind = _kind_of(field_name, event.new, record.baseline.get(field_name), seen=seen)
        if kind is None:
            # Clearing a reading or an identity is how a decision is taken back, so an empty value on
            # a verifying field is an undo rather than an event with nothing to say. Any other field
            # an event writes nothing into is not a decision at all.
            if field_name in VERIFYING_FIELDS and event.new in (None, "", [], {}):
                kind = "state"
            else:
                continue
        # An event with no actor is the pipeline writing its own conclusion — an apply, a rebuild, a
        # classifier pass. It changes the record and takes the field back from whoever had it.
        author = "human" if event.actor else "machine"
        if author == "human" and kind == "state" and before is not None and before.value == event.new:
            # A human event that writes the state the record already shows changes nothing, so it
            # does not become the last word on the field.
            continue
        if author == "machine":
            # The pipeline's own value carries no decision, so restoring one removes whatever
            # verification stood there — including an imported review the new state replaces.
            record.fields[field_name] = FieldStanding(value=event.new, author="machine", kinds=[])
        else:
            history = list(before.kinds) if before else []
            record.fields[field_name] = FieldStanding(value=event.new, author="human",
                                                      kinds=[*history, kind])
            if event.actor:
                record.actor = event.actor
        if field_name == "review":
            # The record's effective review state follows the journal: this is what `human_review`
            # and `kind` read, and leaving it at the imported value made a replayed record disagree
            # with its own events.
            record.review_state = "machine" if event.new is None else str(event.new)
        elif field_name == "active":
            record.active = bool(event.new)
    return standing


def summarize(standing: Iterable[UnitReview]) -> dict[str, int]:
    """The counts a dashboard shows, with machine output and human decisions kept apart."""
    counts: dict[str, int] = dict.fromkeys(
        ("machine", "checked", "draft", "unresolved", "retired", "total"), 0
    )
    for record in standing:
        counts[record.kind] += 1
        counts["total"] += 1
    return counts


def by_page(standing: Iterable[UnitReview]) -> dict[str, dict[str, int]]:
    """The same summary, per page, so a page list can show what is left to do."""
    grouped: dict[str, list[UnitReview]] = {}
    for record in standing:
        grouped.setdefault(record.page_id or "", []).append(record)
    return {page_id: summarize(records) for page_id, records in grouped.items()}


def by_document(standing: Iterable[UnitReview]) -> dict[str, dict[str, int]]:
    """The same summary, per document, so a work list can show what is left to do."""
    grouped: dict[str, list[UnitReview]] = {}
    for record in standing:
        grouped.setdefault(record.document_id or "", []).append(record)
    return {document_id: summarize(records) for document_id, records in grouped.items()}


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
