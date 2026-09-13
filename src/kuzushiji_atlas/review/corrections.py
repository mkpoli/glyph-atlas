"""Editorial corrections to a page's transcription, as a layer over the source text.

The atlas imports a transcription and does not own it. A reviewer working here may see that a
character on the scan is not what the transcription says, and the honest place to record that is not
"the page text is now different" but "at this position, this text should read that, for this reason".
This module is that layer.

**The source text is never rewritten.** A correction says what it replaces and what it should become;
keeping the two apart means the imported text stays byte-identical to what the platform published, a
correction can be shown as a diff, and a reimport cannot collide with an edit. It is also exactly how
the publishing project works: its `data/editorial/corrections/<work>/<witness>/p<page>.json` records
are decisions applied at render time, and its README says an editorial decision is not a claim about
who made it.

**A correction is validated the way the source validates it.** It must name a one-based line and an
`original` that appears *exactly once* in that line of the page as the source's parser counts lines.
That rule is what makes a changed upstream transcription fail the source's build instead of silently
moving the correction onto another character, so a correction that fails it here is refused here —
and a page whose text has moved since the correction was written is reported as *conflicted*, never
applied.

**Corrections are journal events, so they survive everything the store survives.** They are written
with the store's existing `record` path as an event on the page (`field="correction"`), which means
they are rebuilt by `replay`, re-applied by `apply`, exported to `reviews.jsonl` and reimported from
it — the same guarantees as a box or a reading, with no second source of truth. Nothing here needs a
new table, and nothing here can be lost by a reimport of the transcription it corrects.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from difflib import unified_diff
from typing import Any, Literal

from .store import ReviewRequest, Store

#: The event field a correction is recorded under. It is not a record field, so the store's own
#: change dispatcher leaves the projection alone and the correction lives purely in the journal.
CORRECTION = "correction"
#: An event that takes a correction back, for undo. The record it names is retired, not deleted.
RETRACTION = "correction-retracted"

#: What a correction targets. The source's `corrections.ts` accepts `transcription` only.
CORRECTION_KINDS = ("transcription",)

Status = Literal["proposed", "applied", "conflicted", "retracted"]


class CorrectionError(RuntimeError):
    """A correction the store will not record."""


@dataclass
class Correction:
    """One editorial decision about a page's transcription.

    `line` and `original` locate the decision the way the source does, and `corrected` is what the
    text should say there. `note` is required by the source's schema and by common sense: a correction
    without a reason cannot be reviewed. `ruby_field`, `ruby_base` and `entry` carry the source's two
    ways of aiming at something more specific than a text substring — a complete ruby field, or a
    wordlist entry's form or gloss.
    """

    id: str
    page_id: str
    line: int
    original: str
    corrected: str
    note: str
    kind: str = "transcription"
    ruby_field: str | None = None
    ruby_base: str | None = None
    entry: dict[str, Any] | None = None
    actor: str | None = None
    created_at: str | None = None

    def __post_init__(self) -> None:
        if not self.id or not self.id.strip():
            raise CorrectionError("a correction needs an id")
        if self.kind not in CORRECTION_KINDS:
            raise CorrectionError(
                f"kind {self.kind!r} is not one the source's loader accepts; "
                f"expected one of {', '.join(CORRECTION_KINDS)}"
            )
        if not isinstance(self.line, int) or self.line < 1:
            raise CorrectionError(f"line must be a one-based integer, got {self.line!r}")
        if not self.original:
            raise CorrectionError("a correction needs the original text it replaces")
        if not isinstance(self.corrected, str):
            raise CorrectionError("corrected must be a string")
        if not self.note or not self.note.strip():
            raise CorrectionError("a correction needs a note saying why")
        if self.ruby_field is not None:
            if self.ruby_field not in ("rb", "rt", "left"):
                raise CorrectionError(f"invalid ruby field: {self.ruby_field!r}")
            if not self.ruby_base or not self.ruby_base.strip():
                raise CorrectionError("a ruby correction needs the unchanged source base")
        if self.entry is not None:
            gloss = self.entry.get("gloss")
            where = self.entry.get("field")
            if not isinstance(gloss, str) or not gloss:
                raise CorrectionError("an entry correction needs a gloss")
            if where not in (None, "form", "gloss"):
                raise CorrectionError(f"invalid entry field: {where!r}")
            if where == "gloss" and self.original != gloss:
                raise CorrectionError("correcting a whole heading means original equals the gloss")

    def payload(self) -> dict[str, Any]:
        """The record as the journal stores it, and as the source's file holds it."""
        record: dict[str, Any] = {
            "id": self.id,
            "line": self.line,
            "original": self.original,
            "corrected": self.corrected,
            "note": self.note,
        }
        if self.kind != "transcription":
            record["kind"] = self.kind
        if self.ruby_field is not None:
            record["rubyField"] = self.ruby_field
        if self.ruby_base is not None:
            record["rubyBase"] = self.ruby_base
        if self.entry is not None:
            record["entry"] = self.entry
        return record

    @classmethod
    def from_payload(cls, page_id: str, payload: dict[str, Any], **extra: Any) -> Correction:
        return cls(
            id=str(payload["id"]),
            page_id=page_id,
            line=int(payload["line"]),
            original=str(payload["original"]),
            corrected=str(payload["corrected"]),
            note=str(payload.get("note") or ""),
            kind=str(payload.get("kind") or "transcription"),
            ruby_field=payload.get("rubyField"),
            ruby_base=payload.get("rubyBase"),
            entry=payload.get("entry"),
            **extra,
        )


@dataclass
class Judgement:
    """Whether a correction can be placed on the page text as it now stands."""

    correction: Correction
    status: Status
    reason: str | None = None

    @property
    def applicable(self) -> bool:
        return self.status in ("proposed", "applied")


def judge(correction: Correction, text: str) -> Judgement:
    """Place a correction against the source text, or say why it cannot be placed.

    The conditions are the source's own: the line exists, and the original appears exactly once in
    it. The third condition is this project's, because a correction here may later become a file
    there: a correction that changes how many lines the page has would move every later line number
    with it, so it is refused rather than accepted and then quietly mis-aimed.
    """
    from ..ainu_source import transcription_lines

    lines = transcription_lines(text)
    if correction.line > len(lines):
        return Judgement(correction, "conflicted",
                         f"line {correction.line} does not exist; the page has {len(lines)} lines")
    target = lines[correction.line - 1]
    found = target.count(correction.original)
    if found == 0:
        return Judgement(correction, "conflicted",
                         f"line {correction.line} no longer contains {correction.original!r}")
    if found > 1:
        return Judgement(correction, "conflicted",
                         f"{correction.original!r} appears {found} times on line {correction.line}")
    if "\n" in correction.corrected or "\r" in correction.corrected:
        return Judgement(correction, "conflicted",
                         "a correction cannot add or remove lines; the source places them by line")
    return Judgement(correction, "proposed")


def diff(correction: Correction, text: str) -> list[str]:
    """A unified diff of the one line the correction changes, for a reviewer to read."""
    from ..ainu_source import transcription_lines

    lines = transcription_lines(text)
    if correction.line > len(lines):
        return []
    before = lines[correction.line - 1]
    after = before.replace(correction.original, correction.corrected, 1)
    return list(unified_diff([before + "\n"], [after + "\n"],
                             fromfile=f"p{correction.line} as published",
                             tofile=f"p{correction.line} as corrected", n=0))


@dataclass
class PageCorrections:
    """Every correction recorded for one page, with the effective text they produce."""

    page_id: str
    base: str
    judgements: list[Judgement] = field(default_factory=list)

    @property
    def corrections(self) -> list[Correction]:
        return [item.correction for item in self.judgements]

    @property
    def applied(self) -> list[Judgement]:
        return [item for item in self.judgements if item.applicable]

    @property
    def conflicted(self) -> list[Judgement]:
        return [item for item in self.judgements if item.status == "conflicted"]

    def text(self) -> str:
        """The page's transcription with the corrections applied, or the base when there are none.

        The line structure is preserved: a correction replaces text inside one line, so the line count
        the source places corrections by cannot move.
        """
        from ..ainu_source import transcription_lines

        lines = transcription_lines(self.base)
        for item in self.applied:
            correction = item.correction
            if correction.line > len(lines):
                continue
            lines[correction.line - 1] = lines[correction.line - 1].replace(
                correction.original, correction.corrected, 1
            )
        return "\n".join(lines)

    def diffs(self) -> list[list[str]]:
        return [diff(item.correction, self.base) for item in self.applied]


def page_corrections(store: Store, page_id: str, base: str) -> PageCorrections:
    """Read the journal for one page's corrections, oldest first, with the text they produce.

    A correction whose id is later retracted is dropped, so an undo reads the same as if the correction
    had never been written. A correction that no longer places against `base` is kept and reported as
    conflicted rather than dropped: a reimport that moved the text is something a person has to see.
    """
    live: dict[str, Correction] = {}
    order: list[str] = []
    for event in store.events():
        if event.target_type != "page" or event.target_id != page_id:
            continue
        if event.field == CORRECTION and isinstance(event.new, dict):
            correction = Correction.from_payload(page_id, event.new, actor=event.actor, created_at=event.at)
            if correction.id not in live:
                order.append(correction.id)
            live[correction.id] = correction
        elif event.field == RETRACTION:
            identifier = event.new.get("id") if isinstance(event.new, dict) else event.new
            if isinstance(identifier, str):
                live.pop(identifier, None)
                order = [name for name in order if name != identifier]
    judged = [judge(live[identifier], base) for identifier in order]
    return PageCorrections(page_id=page_id, base=base, judgements=judged)


def record(store: Store, correction: Correction, *, client_id: str | None = None,
           base_revision: int | None = None) -> dict[str, Any]:
    """Record a correction in the store's journal, refusing one that cannot be placed.

    The placement is checked here rather than at export, because a correction recorded against text
    that does not hold it is worse than none: it looks like work and would fail the source's build.
    """
    text = store.page_text(correction.page_id)
    if text is None:
        raise CorrectionError(f"{correction.page_id} has no transcription to correct")
    verdict = judge(correction, text)
    if not verdict.applicable:
        raise CorrectionError(verdict.reason or "the correction cannot be placed")
    return store.record(ReviewRequest(
        target_type="page",
        target_id=correction.page_id,
        field=CORRECTION,
        new=correction.payload(),
        client_id=client_id,
        base_revision=base_revision,
    ))


def retract(store: Store, page_id: str, correction_id: str, *, client_id: str | None = None,
            reason: str = "") -> dict[str, Any]:
    """Take a correction back. The journal keeps both, so an undo is itself reviewable."""
    return store.record(ReviewRequest(
        target_type="page",
        target_id=page_id,
        field=RETRACTION,
        new={"id": correction_id, "reason": reason},
        client_id=client_id,
    ))


def to_proposals(page: PageCorrections, unit: str, page_number: int) -> list[Any]:
    """The corrections as proposals for the publishing project's own correction format.

    Only the applied ones become proposals; a conflicted one is returned with `unmappable` set, so a
    reviewer sees it in the submission instead of it disappearing. `unit` and `page_number` are the
    source's slugs and its one-based reader page — the bridge is what establishes them, because they
    are not derivable from an atlas page id.
    """
    from ..ainu_source import Proposal

    proposals: list[Any] = []
    for item in page.judgements:
        correction = item.correction
        if not item.applicable:
            proposals.append(Proposal(unit=unit, page=page_number, id=correction.id,
                                      unmappable=item.reason))
            continue
        proposals.append(Proposal(
            unit=unit, page=page_number, id=correction.id, line=correction.line,
            original=correction.original, corrected=correction.corrected, note=correction.note,
            kind=correction.kind, entry=correction.entry,
            ruby_field=correction.ruby_field, ruby_base=correction.ruby_base,
        ))
    return proposals


def as_json(corrections: Iterable[Correction]) -> str:
    """The records as the source's file holds them: a JSON array, one object a correction."""
    return json.dumps([correction.payload() for correction in corrections], ensure_ascii=False,
                      indent=2) + "\n"
