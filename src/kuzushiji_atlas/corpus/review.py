"""Review events for corpus occurrences, keyed so they survive a re-import.

The review store proper is keyed by dataset row id. An occurrence id is a hash of the
*source identity* instead — corpus, document, page, code point, offset and class — so
rebuilding the index, re-importing a corpus or moving rows between parquet files does
not orphan a decision. That property is the whole reason this sidecar exists rather
than reusing row ids.

Events are append-only JSONL. Nothing here rewrites history: a correction is a new
event that supersedes an earlier one, which is what makes a machine repair reversible
and reviewable.

The actor kind matters and is recorded explicitly. An AI supervisor inspecting a crop
is ``actor_kind="ai"``; only ``actor_kind="human"`` can licence
``confirmed=True`` on a rectangle. Machine output is never presented as
human-reviewed truth.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from dataclasses import field as _dc_field
from pathlib import Path
from typing import Any, Literal

ActorKind = Literal["machine", "ai", "human"]

#: What an event asserts about an occurrence.
Action = Literal[
    "observed",  # a machine or AI looked at it and reported what it saw
    "located",  # a rectangle was proposed for the glyph
    "confirmed",  # a human accepted a rectangle or a reading
    "rejected",  # a human rejected it
    "corrected",  # a reading or rectangle was replaced
    "split",  # one occurrence became several
    "merged",  # several became one
    "noted",  # free-form
]

DEFAULT_REVIEW = "corpus-review.jsonl"


@dataclass
class ReviewEvent:
    occurrence_id: str
    action: Action
    actor: str
    actor_kind: ActorKind
    at: str
    field: str | None = None
    old: Any = None
    new: Any = None
    evidence: str | None = None
    meta: dict[str, Any] = _dc_field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class CorpusReview:
    """Append-only review log for occurrences."""

    def __init__(self, path: str | Path = DEFAULT_REVIEW):
        self.path = Path(path)
        self._events: dict[str, list[dict[str, Any]]] | None = None

    # ---------------------------------------------------------------- writing
    def record(
        self,
        occurrence_id: str,
        action: Action,
        *,
        actor: str,
        actor_kind: ActorKind,
        field: str | None = None,
        old: Any = None,
        new: Any = None,
        evidence: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> ReviewEvent:
        event = ReviewEvent(
            occurrence_id=occurrence_id,
            action=action,
            actor=actor,
            actor_kind=actor_kind,
            at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            field=field,
            old=old,
            new=new,
            evidence=evidence,
            meta=meta or {},
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(event.as_dict(), ensure_ascii=False, default=str) + "\n")
        if self._events is not None:
            self._events.setdefault(occurrence_id, []).append(event.as_dict())
        return event

    def observe(
        self,
        occurrence_id: str,
        *,
        actor: str,
        actor_kind: ActorKind = "ai",
        evidence: str | None = None,
        **meta: Any,
    ) -> ReviewEvent:
        """Record that someone looked and what they said. Never a confirmation."""
        return self.record(
            occurrence_id, "observed", actor=actor, actor_kind=actor_kind, evidence=evidence, meta=meta
        )

    # ---------------------------------------------------------------- reading
    def _load(self) -> dict[str, list[dict[str, Any]]]:
        if self._events is None:
            self._events = {}
            if self.path.exists():
                with open(self.path, encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            event = json.loads(line)
                        except ValueError:
                            continue
                        self._events.setdefault(event["occurrence_id"], []).append(event)
        return self._events

    def events_for(self, occurrence_id: str) -> list[dict[str, Any]]:
        return list(self._load().get(occurrence_id, []))

    def state_of(self, occurrence_id: str) -> str:
        """The effective state after replaying the log in order."""
        events = self.events_for(occurrence_id)
        state = "machine"
        for event in events:
            if event["action"] == "confirmed" and event.get("actor_kind") == "human":
                state = "reviewed"
            elif event["action"] == "rejected" and event.get("actor_kind") == "human":
                state = "rejected"
            elif event["action"] == "corrected":
                state = "corrected"
            elif event["action"] == "observed" and state == "machine":
                state = "machine-observed"
        return state

    def human_confirmed(self, occurrence_id: str) -> bool:
        """True only when a human event confirms this occurrence."""
        return any(
            e["action"] == "confirmed" and e.get("actor_kind") == "human"
            for e in self.events_for(occurrence_id)
        )

    def summary(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for events in self._load().values():
            for event in events:
                counts[event["action"]] = counts.get(event["action"], 0) + 1
        return counts
