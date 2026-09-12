"""Normalise ``atlas-character-reviews`` exports into canonical feedback records.

The review journal has two doors into it, and they leave different footprints.

**A local character review** writes one event whose ``evidence`` is a JSON *string*
holding ``kind="character-review"``, the reviewer's ``verdict``/``issue``, and a
``correction``. That is a UI action: somebody looked at a crop and said something.

**A corpus review** writes an event whose ``new`` is a *structured object* — the same
review fields, plus ``suggestions`` — and no ``evidence``. That is a report about the
source corpus, not a verdict on one glyph.

They are both valuable and they are not the same evidence, so this module reads each
into one typed record and is explicit about how much each one is worth.

The rules it enforces, because getting them wrong poisons everything downstream:

* **A merged glyph is never a positive single character.** When the issue is
  ``merged``, a one-character proposal contradicts what the reviewer recorded — a
  note like ``シヨロ`` under a correction of ``シ`` — so the record is quarantined as
  ``ambiguous`` rather than turned into training data that teaches one character where
  the manuscript has three. Only a human-selected text of two or more characters makes
  a ``joined`` record, and even then the parent is never trained as one character.
* **The source label and the source reading are different fields.** ``original_identity``
  is what the source wrote; ``original_reading`` is how it read it. They differ on
  hundreds of units, and a correction that matches the reading is a reading fix, not a
  new identity.
* **A note is not a proposal.** Free text is carried as ``note`` and never promoted to
  ``proposed_text``; the proposal is the structured correction and nothing else.
* **A user report is reported, not verified.** ``actor_kind="user-report"`` yields
  ``reported`` regardless of how confident the prose sounds.
* **Stale records are not active.** ``current: false``, or an event superseded by a
  later one for the same unit, keeps its record but is excluded from active use.

Nothing here mutates the payload it is given: the export is read, never written back.
Each record carries a digest of the exact JSON it came from, so a record can be traced
to its source without copying private fields around.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

#: The export this understands.
KIND = "atlas-character-reviews"
VERSION = 1

#: What a record's decision is worth.
#:
#: ``accepted-identity``  a human settled what the character *is*
#: ``accepted-match``     a human confirmed the existing identity; nothing changed
#: ``joined``             a human selected a multi-character transcription for a merge
#: ``crop``               a human corrected the box, not the character
#: ``ambiguous``          the record contradicts itself or proposes nothing usable
#: ``reported``           a report about the corpus, not a verified correction
#: ``other``              understood, but none of the above
DECISIONS = (
    "accepted-identity",
    "accepted-match",
    "joined",
    "crop",
    "ambiguous",
    "reported",
    "other",
)

#: Actor kinds whose word is evidence of a person's decision.
HUMAN_ACTOR_KINDS = frozenset({"human", "reviewer", ""})

#: Actor kinds that report rather than decide. A report is never a verified positive.
REPORT_ACTOR_KINDS = frozenset({"user-report", "reported", "machine", "ai", "model"})

#: Reasons a record is quarantined. They are kept, not discarded: a contradiction is
#: evidence about the review process.
QUARANTINE_MERGED_AS_SINGLE = "merged-glyph-proposed-as-single-character"
QUARANTINE_MERGED_NO_TEXT = "merged-glyph-with-no-joined-text"
QUARANTINE_NOTE_DISAGREES = "note-length-disagrees-with-proposal"
QUARANTINE_DISPUTED_NO_PROPOSAL = "disputed-without-a-proposal"
QUARANTINE_READING_NOT_IDENTITY = "reading-correction-is-not-an-identity"
QUARANTINE_STALE = "stale-or-superseded"
QUARANTINE_MATCH_CHANGED = "match-carries-a-change"
QUARANTINE_PHONETIC_INTENT = "reading-issue-records-phonetic-intent"
QUARANTINE_IMPLICIT_QUIZ_MATCH = "quiz-unselected-is-not-a-confirmation"

#: Flags worth carrying to a human, none of which change the decision on their own.
FLAG_LEGACY_CHOICE = "legacy-wrong-character-ui-choice"
FLAG_LEGACY_DEFAULT_ISSUE = "legacy-default-issue"
FLAG_LAYER_REQUEST = "explicit-layer-character-request"
FLAG_EXISTING_BUG = "event-new-disputed-despite-request"
FLAG_SOURCE_LABEL_DIFFERS = "source-label-differs-from-reading"
FLAG_UNTRUSTED_ACTOR = "actor-is-not-a-human-decision"


def _as_mapping(value: Any) -> Mapping[str, Any]:
    """A mapping from a dict or a JSON string, else an empty mapping."""
    if isinstance(value, Mapping):
        return value
    if isinstance(value, str) and value.strip():
        try:
            loaded = json.loads(value)
        except ValueError:
            return {}
        return loaded if isinstance(loaded, Mapping) else {}
    return {}


def _text(value: Any) -> str | None:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    if isinstance(value, (int, float)):
        return str(value)
    return None


def _box(value: Any) -> dict[str, int] | None:
    if not isinstance(value, Mapping):
        return None
    try:
        return {k: int(value[k]) for k in ("x", "y", "w", "h")}
    except (KeyError, TypeError, ValueError):
        return None


def _digest(value: Any) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def source_corpus_of(unit_id: str | None) -> str | None:
    """The corpus a unit id belongs to, read from its prefix."""
    if not unit_id or ":" not in unit_id:
        return None
    return {
        "codh-omt:": "kokatsuji",
        "codh:": "codh-full",
        "hi:": "hilab",
        "hl:": "honkoku-lines",
        "hk:": "ainu-records",
        "ws:": "wikisource",
    }.get(unit_id.split(":", 1)[0] + ":")


@dataclass(frozen=True)
class Feedback:
    """One review, normalised. Immutable: a record is a reading, not a workspace."""

    event_id: str
    source_unit_id: str
    decision: str
    trusted_human: bool

    # ---- what the source said before anyone touched it
    original_identity: str | None = None
    original_reading: str | None = None
    source_box: dict[str, int] | None = None
    page_hash: str | None = None
    source_revision: Any = None

    # ---- what the reviewer proposed, kept strictly apart from the source
    proposed_text: str | None = None
    proposed_box: dict[str, int] | None = None
    #: The reviewer's typed correction, before any precedence was applied.
    requested_text: str | None = None
    #: The saved unit reading. The effective state, not the proposal.
    effective_text: str | None = None

    # ---- how the record was made
    door: str = "unknown"  # "local" | "corpus"
    target_type: str | None = None
    source_corpus: str | None = None
    verdict: str | None = None
    issue: str | None = None
    note: str | None = None
    actor: str | None = None
    actor_kind: str | None = None
    role: str | None = None
    at: str | None = None
    event_new: Any = None  # the raw ``new``, for audit

    # ---- whether it may be used
    current: bool = False
    is_active: bool = False
    superseded_by: str | None = None
    quarantine_reasons: tuple[str, ...] = ()
    evidence_flags: tuple[str, ...] = ()
    snapshot_digest: str = ""

    @property
    def training_text(self) -> str | None:
        """The confirmed character or joined sequence available for supervision.

        An ``accepted-identity`` offers the reviewer's correction. An ``accepted-match``
        offers the source identity the reviewer confirmed — a confirmation is training
        material too, and it is the same text the corpus already believed.
        """
        if not self.is_active or not self.trusted_human:
            return None
        if self.decision in ("accepted-identity", "joined"):
            return self.proposed_text
        if self.decision == "accepted-match":
            return self.original_identity
        return None

    @property
    def train_parent_as_single_char(self) -> bool:
        """Never for a merge.

        A reviewed merge means several characters share one cell. Even when a human
        picked the transcription, training the parent as one character would teach the
        model exactly the error the review set out to record.
        """
        if self.issue == "merged":
            return False
        text = self.training_text
        return bool(text) and len(text) == 1

    @property
    def quarantined(self) -> bool:
        return self.decision == "ambiguous"

    def as_dict(self) -> dict[str, Any]:
        from dataclasses import asdict

        payload = asdict(self)
        payload["training_text"] = self.training_text
        payload["train_parent_as_single_char"] = self.train_parent_as_single_char
        payload["quarantined"] = self.quarantined
        return payload


def _local_parts(event: Mapping[str, Any], record: Mapping[str, Any]) -> dict[str, Any]:
    """The fields of a local character review: evidence string + reviewed snapshot."""
    evidence = _as_mapping(event.get("evidence"))
    request = _as_mapping(evidence.get("request"))
    snapshot = _as_mapping(evidence.get("snapshot")) or _as_mapping(record.get("reviewed"))
    character = _as_mapping(snapshot.get("character"))
    correction = _as_mapping(evidence.get("correction"))

    # Proposal precedence, highest first:
    #   1. request.character    an explicit identity request from the layer
    #   2. request.correction   what the reviewer actually typed
    #   3. suggested_reading    the reviewer's own suggestion
    # `evidence.correction` is the *effective saved unit state*, not the proposal: when
    # a merge carries a two-character correction the save path does not resolve it, so
    # the stored reading stays the old label. Reading that back as the proposal would
    # turn every joined sequence into a single character.
    explicit = _text(request.get("character"))
    requested = _text(request.get("correction"))
    suggested = _text(evidence.get("suggested_reading"))

    return {
        "door": "local",
        "verdict": _text(evidence.get("verdict")) or _text(request.get("verdict")),
        "issue": _text(evidence.get("issue")) or _text(request.get("issue")),
        # Free text stays free text. It is never promoted to a proposal.
        "note": _text(evidence.get("note")) or _text(request.get("note")),
        "original_identity": _text(character.get("label")),
        "original_reading": _text(character.get("reading")),
        "source_box": _box(character.get("box")),
        "page_hash": _text(character.get("image_sha256")) or _text(snapshot.get("image_sha256")),
        "source_revision": character.get("revision", snapshot.get("revision")),
        "proposed_text": explicit or requested or suggested,
        "requested_text": requested,
        "suggested_reading": suggested,
        # The saved state, kept for audit and usable as an identity only in the legacy
        # wrong+reading case, where the correction was what got resolved into the unit.
        "effective_text": _text(correction.get("reading")),
        "proposed_box": _box(correction.get("box")) or _box(request.get("box")),
        "explicit_character": explicit,
        # The modern layer marks its own saves. Its `issue=reading` means the reviewer is
        # talking about the reading, not inviting an identity to be inferred from it.
        "layer": _text(evidence.get("layer")),
        "request_reading": _text(request.get("reading")),
        "kind": _text(evidence.get("kind")),
        "quiz_character": _canonical_character(evidence.get("suggested_character")),
        "suggestions": _suggestions(evidence, request),
    }


def _corpus_parts(event: Mapping[str, Any], record: Mapping[str, Any]) -> dict[str, Any]:
    """The fields of a corpus review: structured ``new`` + the detail-shaped snapshot."""
    new = _as_mapping(event.get("new"))
    reviewed = _as_mapping(record.get("reviewed"))
    if not reviewed:
        reviewed = _as_mapping(new.get("snapshot"))
    source = _as_mapping(reviewed.get("source"))
    original = _text(reviewed.get("source_label")) or _text(reviewed.get("label"))
    return {
        "door": "corpus",
        "verdict": _text(new.get("verdict")),
        "issue": _text(new.get("issue")),
        "note": _text(new.get("note")),
        "original_identity": original,
        "original_reading": _text(reviewed.get("reading")),
        "source_box": _box(reviewed.get("box")),
        "page_hash": _text(reviewed.get("page_hash")) or _text(source.get("page_hash")),
        "source_revision": reviewed.get("source_revision"),
        # `new.character` is the accepted identity when the corpus layer set one.
        "proposed_text": _text(new.get("character")),
        "requested_text": None,
        "suggested_reading": None,
        "effective_text": _text(_as_mapping(new.get("correction")).get("reading")),
        "proposed_box": _box(_as_mapping(new.get("correction")).get("box")),
        "explicit_character": _text(new.get("character")),
        "layer": _text(new.get("layer")),
        "request_reading": _text(new.get("reading")),
        "kind": "corpus-review",
        "quiz_character": _canonical_character(new.get("suggested_character")),
        "suggestions": _suggestions(new, {}),
    }


def _canonical_character(value: Any) -> str | None:
    """A character from code points like ``U+4E00`` or ``U+30C4 U+309A``, or the character itself.

    The visual quiz records what it suggested as code points, because that is what a
    character layer holds. One character can need several of them — a base and its
    mark, like ツ + U+309A — and the spaced spelling is read as one character, the
    same rule the layers route stores identities by. Canonicalising here means
    downstream code compares characters, never two spellings of one.
    """
    text = _text(value)
    if not text:
        return None
    import unicodedata

    points = text.split()
    if points and all(point[:2].upper() == "U+" for point in points):
        try:
            joined = "".join(chr(int(point.upper().removeprefix("U+"), 16)) for point in points)
        except (ValueError, OverflowError):
            return None
        return unicodedata.normalize("NFC", joined)
    return unicodedata.normalize("NFC", text)


def _suggestions(primary: Mapping[str, Any], secondary: Mapping[str, Any]) -> list[dict[str, Any]]:
    for source in (primary, secondary):
        found = source.get("suggestions")
        if isinstance(found, Sequence) and not isinstance(found, str):
            return [dict(s) for s in found if isinstance(s, Mapping)]
    return []


def _joined_text(suggestions: Iterable[Mapping[str, Any]], proposed: str | None) -> str | None:
    """A human-selected transcription of two or more characters, if there is one.

    Only a suggestion the reviewer actually took counts — one equal to the recorded
    proposal, or explicitly marked selected. A suggestion merely *offered* is not a
    decision.
    """
    texts = [_text(s.get("text")) for s in suggestions]
    texts = [t for t in texts if t and len(t) >= 2]
    if not texts:
        return None
    if proposed and proposed in texts:
        return proposed
    for suggestion in suggestions:
        text = _text(suggestion.get("text"))
        if text and suggestion.get("selected") is True:
            return text
    return None


def _decide(parts: Mapping[str, Any], trusted: bool) -> tuple[str, list[str], list[str], str | None]:
    """The decision, the quarantine reasons, the flags, and the effective proposal.

    Written as one readable table rather than a chain of conditions, because the whole
    point of the module is that these cases are told apart deliberately. The proposal is
    returned because the legacy path may fall back to the saved effective reading, and
    the record has to carry whichever value actually decided the outcome.
    """
    verdict = (parts.get("verdict") or "").lower()
    issue = (parts.get("issue") or "").lower()
    proposed = parts.get("proposed_text")
    original_identity = parts.get("original_identity")
    original_reading = parts.get("original_reading")
    suggestions = parts.get("suggestions") or []
    flags: list[str] = []
    quarantine: list[str] = []

    # A visual quiz states its suggestion as a code point; that is its proposal.
    if not proposed:
        proposed = parts.get("quiz_character")

    if not trusted:
        flags.append(FLAG_UNTRUSTED_ACTOR)
        return "reported", quarantine, flags, proposed

    if original_identity and original_reading and original_identity != original_reading:
        flags.append(FLAG_SOURCE_LABEL_DIFFERS)

    joined = _joined_text(suggestions, proposed)

    # ---- a merge is never a single character ------------------------------------
    if issue == "merged":
        if joined:
            # A human picked a real transcription for the merge. Keep it as the expected
            # text; never train the parent as one character.
            return "joined", quarantine, flags, joined
        if proposed and len(proposed) >= 2:
            # The reviewer typed the whole sequence. That is the transcription.
            return "joined", quarantine, flags, proposed
        quarantine.append(QUARANTINE_MERGED_AS_SINGLE if proposed else QUARANTINE_MERGED_NO_TEXT)
        note = parts.get("note")
        if proposed and note and len(note) >= 2 and len(proposed) == 1:
            quarantine.append(QUARANTINE_NOTE_DISAGREES)
        return "ambiguous", quarantine, flags, proposed

    if verdict == "match":
        # The visual quiz historically sent a match for every unselected tile. That
        # records absence of an obvious error, not a deliberate confirmation. Keep
        # the journal record, but never use it as positive training supervision.
        if parts.get("kind") == "visual-quiz":
            quarantine.append(QUARANTINE_IMPLICIT_QUIZ_MATCH)
            return "ambiguous", quarantine, flags, proposed
        # A match is a confirmation and nothing else. A changed proposal, or an issue
        # that names a defect, contradicts it: the modern door refuses to save such a
        # record, so one appearing here came from somewhere that did not, and it cannot
        # be read as agreement.
        changed = bool(proposed) and proposed != original_identity
        if issue in ("crop", "blank", "character") or changed:
            quarantine.append(QUARANTINE_MATCH_CHANGED)
            return "ambiguous", quarantine, flags, proposed
        if issue == "reading" and (not proposed or proposed == original_identity):
            flags.append(FLAG_LEGACY_DEFAULT_ISSUE)
        return "accepted-match", quarantine, flags, proposed

    if verdict == "wrong":
        if issue == "crop":
            return "crop", quarantine, flags, proposed
        if issue == "reading":
            # The reviewer's own proposal first. The saved effective reading is the
            # fallback and only counts when it actually differs from the source: an
            # unchanged stored value says nothing was corrected.
            effective = parts.get("effective_text")
            candidate = proposed or (effective if effective != original_identity else None)
            readable = bool(candidate) and len(candidate) == 1
            if not readable:
                quarantine.append(QUARANTINE_DISPUTED_NO_PROPOSAL)
                return "ambiguous", quarantine, flags, candidate
            # The modern layer marking the record, or the reviewer filling in a reading,
            # says the intent was phonetic. No identity is inferred from that even when
            # the written form and the reading are the same character: `issue=reading`
            # there means "the reading is wrong", and a reading is not a character.
            if parts.get("layer") or parts.get("request_reading"):
                quarantine.append(QUARANTINE_PHONETIC_INTENT)
                return "ambiguous", quarantine, flags, candidate
            if original_identity and original_reading and original_identity != original_reading:
                # The source already distinguished written form from reading, so a
                # one-character correction is a reading fix. It does not tell us what
                # the character is.
                quarantine.append(QUARANTINE_READING_NOT_IDENTITY)
                return "ambiguous", quarantine, flags, candidate
            if candidate == original_identity:
                quarantine.append(QUARANTINE_DISPUTED_NO_PROPOSAL)
                return "ambiguous", quarantine, flags, candidate
            # The old UI offered "wrong" + issue "reading" as the only way to say "the
            # character itself is wrong": with one written form there was no separate
            # reading to disagree with, so the single-character correction *is* the
            # accepted identity. That inference belongs to the old correction-only
            # choice, which is why the layer and reading checks come first.
            flags.append(FLAG_LEGACY_CHOICE)
            return "accepted-identity", quarantine, flags, candidate
        if issue == "character":
            # An explicit request, or the character a visual quiz suggested — the quiz's
            # suggestion is what the reviewer was shown and accepted.
            explicit = parts.get("explicit_character") or parts.get("quiz_character")
            if explicit and explicit != original_identity:
                flags.append(FLAG_LAYER_REQUEST)
                if parts.get("event_new") == "disputed":
                    # An explicit identity request that still landed as `disputed`: the
                    # decision stands, and the state is flagged as wrong.
                    flags.append(FLAG_EXISTING_BUG)
                return "accepted-identity", quarantine, flags, explicit
            # Disputed, with nothing proposed to put in place of what was rejected.
            quarantine.append(QUARANTINE_DISPUTED_NO_PROPOSAL)
            if parts.get("event_new") == "disputed":
                flags.append(FLAG_EXISTING_BUG)
            return "ambiguous", quarantine, flags, explicit
        quarantine.append(QUARANTINE_DISPUTED_NO_PROPOSAL)
        return "ambiguous", quarantine, flags, proposed

    return "other", quarantine, flags, proposed


def _one(record: Mapping[str, Any]) -> Feedback:
    """One export record, normalised. The payload is read, never modified."""
    event = _as_mapping(record.get("event"))
    event_id = _text(event.get("id")) or ""
    unit_id = _text(event.get("target_id")) or ""
    actor_kind = _text(event.get("actor_kind"))
    role = _text(event.get("role"))

    has_evidence = bool(_as_mapping(event.get("evidence")))
    parts = _local_parts(event, record) if has_evidence else _corpus_parts(event, record)
    parts["event_new"] = event.get("new")

    # Two doors, two shapes. A local review carries `role="reviewer"`. A corpus review
    # from an ordinary user carries `actor_kind="human"` and **no role at all** — see
    # `CorpusReviews.record`, whose default is `actor_kind="human"`. Either is a person
    # deciding; a report is neither.
    trusted = (actor_kind or "") not in REPORT_ACTOR_KINDS and (role == "reviewer" or actor_kind == "human")
    decision, quarantine, flags, proposal = _decide(parts, trusted)

    current = bool(record.get("current"))
    if not current:
        quarantine = [*quarantine, QUARANTINE_STALE]

    return Feedback(
        event_id=event_id,
        source_unit_id=unit_id,
        decision=decision,
        trusted_human=trusted,
        original_identity=parts.get("original_identity"),
        original_reading=parts.get("original_reading"),
        source_box=parts.get("source_box"),
        page_hash=parts.get("page_hash"),
        source_revision=parts.get("source_revision"),
        proposed_text=proposal if proposal is not None else parts.get("proposed_text"),
        proposed_box=parts.get("proposed_box"),
        requested_text=parts.get("requested_text"),
        effective_text=parts.get("effective_text"),
        door=parts.get("door", "unknown"),
        target_type=_text(event.get("target_type")),
        source_corpus=source_corpus_of(unit_id),
        verdict=parts.get("verdict"),
        issue=parts.get("issue"),
        note=parts.get("note"),
        actor=_text(event.get("actor")),
        actor_kind=actor_kind,
        role=role,
        at=_text(event.get("at")),
        event_new=event.get("new"),
        current=current,
        is_active=current and decision != "ambiguous",
        quarantine_reasons=tuple(quarantine),
        evidence_flags=tuple(dict.fromkeys(flags)),
        snapshot_digest=_digest(record),
    )


def _supersede(records: list[Feedback]) -> list[Feedback]:
    """Mark earlier reviews of a unit superseded by a later identity decision.

    The export carries a ``current`` flag, but a journal can hold several events for
    one unit and only the newest identity decision is the live one. Ordering is by
    ``at``, falling back to the export's own order so a missing timestamp cannot make
    the result depend on dict iteration.
    """
    from dataclasses import replace

    latest: dict[str, tuple[int, str]] = {}
    for index, record in enumerate(records):
        # Only a record that still stands can supersede another. A stale event is
        # already out of use, and letting it displace a live one would invert the
        # export's own `current` flag.
        if record.current and record.decision in ("accepted-identity", "joined"):
            previous = latest.get(record.source_unit_id)
            if previous is None or (record.at or "") >= (previous[1] or ""):
                latest[record.source_unit_id] = (index, record.at or "")

    out: list[Feedback] = []
    for index, record in enumerate(records):
        winner = latest.get(record.source_unit_id)
        if winner and winner[0] != index and record.decision in ("accepted-identity", "joined"):
            out.append(
                replace(
                    record,
                    superseded_by=records[winner[0]].event_id,
                    is_active=False,
                    quarantine_reasons=(*record.quarantine_reasons, QUARANTINE_STALE),
                )
            )
        else:
            out.append(record)
    return out


def normalize_export(payload: Any, *, active_only: bool = False) -> list[Feedback]:
    """Canonical feedback records from an ``atlas-character-reviews`` export.

    ``payload`` is the decoded export or its JSON text. Records that contradict
    themselves keep a decision of ``ambiguous`` and are never dropped: they are
    evidence about the review process, and a caller that wants only usable material
    asks for ``active_only=True``.

    The payload is not modified.
    """
    if isinstance(payload, (str, bytes)):
        payload = json.loads(payload)
    if not isinstance(payload, Mapping):
        raise TypeError("an atlas-character-reviews export must be a JSON object")
    kind = payload.get("kind")
    if kind and kind != KIND:
        raise ValueError(f"not an {KIND} export: kind={kind!r}")
    reviews = payload.get("reviews")
    if reviews is None:
        reviews = []
    if not isinstance(reviews, Sequence) or isinstance(reviews, (str, bytes)):
        raise TypeError("an export's `reviews` must be a list")

    parsed = [_one(record) for record in reviews if isinstance(record, Mapping)]
    # A record with no event id identifies nothing and cannot be traced back, so it is
    # dropped rather than carried as an anonymous row.
    records = _supersede([record for record in parsed if record.event_id])
    if active_only:
        records = [r for r in records if r.is_active]
    return records


def active(records: Iterable[Feedback]) -> list[Feedback]:
    """The records that may be used, in export order."""
    return [r for r in records if r.is_active]


def by_unit(records: Iterable[Feedback]) -> dict[str, list[Feedback]]:
    """Records grouped by source unit, newest last."""
    grouped: dict[str, list[Feedback]] = {}
    for record in records:
        grouped.setdefault(record.source_unit_id, []).append(record)
    return grouped


def training_pairs(records: Iterable[Feedback]) -> list[tuple[str, str, dict[str, int] | None]]:
    """``(unit_id, text, box)`` for records safe to train on.

    Only trusted human identity decisions, never a quarantined merge and never the
    proposed box standing in for the source box. Kept as one small function so the
    filtering rule lives in a single place rather than in every experiment.
    """
    pairs: list[tuple[str, str, dict[str, int] | None]] = []
    for record in records:
        if not record.is_active or not record.trusted_human:
            continue
        if record.decision == "joined":
            if not record.proposed_text:
                continue
        elif not record.train_parent_as_single_char:
            continue
        text = record.training_text if record.decision != "joined" else record.proposed_text
        if not text:
            continue
        # The text travels as the reviewer wrote it: one character for a settled
        # identity, the whole sequence for a merge. What the merge must never become is
        # a single-character label for the parent cell, which is what the flag says.
        pairs.append((record.source_unit_id, text, record.source_box))
    return pairs
