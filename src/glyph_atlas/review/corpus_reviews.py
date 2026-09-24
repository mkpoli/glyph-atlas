"""Local reviews of imported corpus glyphs. Source tables remain unchanged."""

from __future__ import annotations

import json
import sqlite3
import unicodedata
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from .. import refs


class CorpusEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    identity: str = Field(min_length=1, max_length=1000)
    client_id: str = Field(min_length=1, max_length=128)
    revision: int = Field(ge=0)
    source_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    verdict: Literal["match", "wrong"]
    issue: Literal["reading", "character", "merged", "crop", "blank", "other"] | None = None
    character: str | None = Field(default=None, max_length=32)
    correction: str | None = Field(default=None, max_length=32)
    note: str = Field(default="", max_length=2000)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


class CorpusReviews:
    def __init__(self, api: Any):
        self.api = api
        self.path = Path(api.directory) / "reviews.sqlite"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS glyph_reviews (
                revision INTEGER PRIMARY KEY AUTOINCREMENT,
                id TEXT NOT NULL UNIQUE, identity TEXT NOT NULL, client_id TEXT NOT NULL,
                request TEXT NOT NULL, source TEXT NOT NULL, decision TEXT NOT NULL,
                actor_kind TEXT NOT NULL, at TEXT NOT NULL)""")
            db.execute("CREATE INDEX IF NOT EXISTS glyph_review_identity ON glyph_reviews(identity, revision)")
            db.execute("CREATE TABLE IF NOT EXISTS review_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS glyph_baseline "
                       "(identity TEXT PRIMARY KEY, character TEXT NOT NULL, source_revision TEXT)")

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def revision_floor(self, db=None) -> int:
        if db is None:
            with closing(self.connect()) as connection:
                return self.revision_floor(connection)
        row = db.execute("SELECT value FROM review_meta WHERE key='revision_floor'").fetchone()
        return int(row[0]) if row else 0

    def baseline(self) -> dict[str, dict]:
        with closing(self.connect()) as db:
            return {row["identity"]: dict(row) for row in db.execute("SELECT * FROM glyph_baseline")}

    def source(self, identity: str) -> dict:
        from ..corpus.details import detail

        try:
            return detail(self.api, identity)
        except KeyError as exc:
            raise HTTPException(404, "This corpus glyph is no longer available.") from exc

    def latest(self) -> dict[str, dict]:
        with closing(self.connect()) as db:
            rows = db.execute("SELECT * FROM glyph_reviews WHERE revision IN "
                              "(SELECT max(revision) FROM glyph_reviews GROUP BY identity)")
            return {row["identity"]: dict(row) for row in rows}

    def overlay(self, source: dict, record: dict | None = None) -> dict:
        identity = source["id"]
        if record is None:
            record = self.latest().get(identity)
        baseline = self.baseline().get(identity)
        baseline_current = baseline and baseline["source_revision"] == source["source_revision"]
        human = baseline["character"] if baseline_current else None
        # An issue report does not revoke a previous character assignment. Recover
        # the latest explicit identity tied to these same source pixels.
        if record:
            with closing(self.connect()) as db:
                events = db.execute("SELECT source,decision,actor_kind FROM glyph_reviews WHERE identity=? AND revision<=? ORDER BY revision DESC",
                                    (identity, record["revision"]))
                for event in events:
                    original, decision = json.loads(event["source"]), json.loads(event["decision"])
                    if original["source_revision"] != source["source_revision"] or event["actor_kind"] != "human":
                        continue
                    candidate = decision.get("character")
                    if not candidate and decision.get("verdict") == "match":
                        candidate = original.get("written_character")
                    if candidate:
                        human = candidate
                        break
        written = human or source["label"]
        result = {**source, "label": written, "char": written,
                  "code_point": " ".join(refs.to_code_points(written)),
                  "origin": "corpus", "source_label": source.get("source_label", source["label"]),
                  "state": "pending", "revision": record["revision"] if record else self.revision_floor(),
                  "suggestions": [], "review_event": None}
        if human:
            from ..corpus.identity import identity_fields
            result.update(identity_fields(source, source.get("source", {}).get("corpus"), human_character=human))
        if not record:
            return result
        original, choice = json.loads(record["source"]), json.loads(record["decision"])
        if original["source_revision"] != source["source_revision"]:
            return {**result, "state": "stale"}
        written = choice.get("character") or written
        if choice.get("character") or (choice.get("verdict") == "match" and result.get("written_character")):
            result.update(written_character=written, identity_basis="human_review", identity_status="assigned")
        return {**result, "label": written, "char": written,
                "code_point": " ".join(refs.to_code_points(written)),
                "state": "checked" if choice.get("verdict") == "match" or choice.get("character") else "flagged",
                "suggestions": choice.get("suggestions", []), "issue": choice.get("issue"),
                "review_event": record["id"], "actor_kind": record["actor_kind"]}

    def detail(self, identity: str) -> dict:
        return self.overlay(self.source(identity))

    def record(self, edit: CorpusEdit, *, actor_kind: str = "human", suggestions: list | None = None) -> dict:
        payload = edit.model_dump(mode="json")
        character = unicodedata.normalize("NFC", edit.character) if edit.character is not None else None
        if character is not None and (len(character) != 1 or character.isspace()
                                      or unicodedata.category(character) in ("Cc", "Cf", "Cs")):
            raise HTTPException(422, "Choose one character, or report joined characters.")
        if edit.verdict == "match" and (edit.issue or edit.character or edit.correction):
            raise HTTPException(422, "A match cannot also carry an error or correction.")
        if edit.verdict == "wrong" and not edit.issue:
            raise HTTPException(422, "Choose an error type.")
        if edit.character and edit.issue not in ("character", "reading"):
            raise HTTPException(422, "A character correction needs a wrong-character issue.")
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            repeated = db.execute("SELECT * FROM glyph_reviews WHERE id=?", (str(edit.id),)).fetchone()
            if repeated:
                if repeated["request"] != _json(payload) or repeated["actor_kind"] != actor_kind:
                    raise HTTPException(409, "This save was already used for a different review.")
                current = db.execute("SELECT * FROM glyph_reviews WHERE identity=? ORDER BY revision DESC LIMIT 1",
                                     (edit.identity,)).fetchone()
                try:
                    result = self.overlay(self.source(edit.identity), dict(current))
                except HTTPException as exc:
                    if exc.status_code != 404:
                        raise
                    result = {**json.loads(current["source"]), "origin": "corpus", "state": "missing",
                              "revision": current["revision"], "review_event": current["id"]}
                return {**result, "accepted_event": str(edit.id)}
            source = self.source(edit.identity)
            previous = db.execute("SELECT * FROM glyph_reviews WHERE identity=? ORDER BY revision DESC LIMIT 1",
                                  (edit.identity,)).fetchone()
            revision = previous["revision"] if previous else self.revision_floor(db)
            if edit.revision != revision or edit.source_revision != source["source_revision"]:
                raise HTTPException(409, "This glyph changed. Reload it before saving.")
            if source.get("needs_segmentation") and (edit.verdict == "match" or edit.character):
                raise HTTPException(422, "This crop contains a character group. Split it before confirming a character.")
            if not source.get("image") or not source.get("proxyable"):
                raise HTTPException(422, "This glyph has no image available to review here.")
            if edit.verdict == "match":
                current_identity = self.overlay(source, dict(previous) if previous else None)
                if current_identity.get("identity_status") == "unassigned":
                    raise HTTPException(422, "Choose the written character before confirming this crop.")
                character = current_identity["label"]
                if character == source["label"]:
                    character = None
            choice = {"verdict": edit.verdict, "issue": edit.issue, "character": character,
                      "correction": edit.correction, "note": edit.note, "suggestions": suggestions or []}
            db.execute("INSERT INTO glyph_reviews(id,identity,client_id,request,source,decision,actor_kind,at) "
                       "VALUES (?,?,?,?,?,?,?,?)", (str(edit.id), edit.identity, edit.client_id, _json(payload),
                       _json(source), _json(choice), actor_kind, datetime.now(UTC).isoformat()))
            saved = dict(db.execute("SELECT * FROM glyph_reviews WHERE id=?", (str(edit.id),)).fetchone())
        return {**self.overlay(source, saved), "accepted_event": str(edit.id)}

    def reviewed_rows(self, state: str | None = None) -> list[dict]:
        rows = []
        for identity, event in self.latest().items():
            try:
                item = self.overlay(self.source(identity), event)
            except HTTPException:
                continue
            if state is None or item["state"] == state:
                rows.append(item)
        return rows

    def corrected_rows(self) -> list[dict]:
        rows = []
        latest = self.latest()
        for identity in set(self.baseline()) | set(latest):
            try:
                row = self.overlay(self.source(identity), latest.get(identity))
            except HTTPException:
                continue
            original = refs.to_char(row["source_code_point"]) if row.get("source_code_point") else row["source_label"]
            if row["state"] != "stale" and row.get("identity_basis") == "human_review" and row["label"] != original:
                rows.append(row)
        return rows

    def corrections(self) -> dict[str, str]:
        return self.identity_assignments()

    def identity_assignments(self) -> dict[str, str]:
        """Current explicit human identities, including confirmation of the source class."""
        result = {}
        latest = self.latest()
        for identity in set(self.baseline()) | set(latest):
            try:
                row = self.overlay(self.source(identity), latest.get(identity))
            except HTTPException:
                continue
            if row["state"] != "stale" and row.get("identity_basis") == "human_review" and row.get("written_character"):
                result[identity] = row["written_character"]
        return result

    def adjust_counts(self, rows: list[dict]) -> list[dict]:
        corrected = self.corrected_rows()
        for row in rows:
            for item in corrected:
                original = refs.to_char(item["source_code_point"]) if item.get("source_code_point") else item["source_label"]
                delta = int(row["char"] == item["label"]) - int(row["char"] == original)
                if not delta:
                    continue
                for key in ("n_glyphs", "n_units" if item.get("unit_id") else "n_glyph_rects"):
                    if row.get(key) is not None:
                        row[key] = max(0, row[key] + delta)
                row["renderable"] = bool(row.get("n_glyphs"))
                if delta > 0:
                    row["known"] = True
                    corpus = item.get("source", {}).get("corpus")
                    if corpus and corpus not in row.get("corpora", []):
                        row["corpora"] = [*row.get("corpora", []), corpus]
        return rows

    def overlay_rows(self, rows: list[dict]) -> list[dict]:
        from ..corpus.identity import IDENTITY_FIELDS
        latest = self.latest()
        baseline = self.baseline()
        output = []
        for row in rows:
            identity = row["id"]
            if identity in latest or identity in baseline:
                try:
                    item = self.overlay(self.source(identity), latest.get(identity))
                    row = {**row, **{k: item[k] for k in ("label", "char", "code_point", "source_label",
                                                          "state", "revision", "review_event", "origin", *IDENTITY_FIELDS) if k in item}}
                except HTTPException:
                    pass
            output.append(row)
        return output

    def exports(self) -> list[dict]:
        latest = self.latest()
        with closing(self.connect()) as db:
            rows = list(db.execute("SELECT * FROM glyph_reviews ORDER BY revision"))
        output = []
        for record in rows:
            source, decision = json.loads(record["source"]), json.loads(record["decision"])
            current = latest[record["identity"]]["id"] == record["id"]
            try:
                current = current and self.source(record["identity"])["source_revision"] == source["source_revision"]
            except HTTPException:
                current = False
            output.append({"origin": "corpus", "event": {"id": record["id"], "target_id": record["identity"],
                           "actor": record["client_id"], "actor_kind": record["actor_kind"], "at": record["at"],
                           "field": "review", "new": decision}, "reviewed": source,
                           "current": current, "current_revision": latest[record["identity"]]["revision"],
                           "source_update": {"identity": record["identity"], "source_revision": source["source_revision"],
                                             "source": source.get("source"), "box": source.get("box"),
                                             "original_character": (refs.to_char(source["source_code_point"]) if source.get("source_code_point")
                                                                    else source.get("source_label") or source["label"]),
                                             "source_label": source.get("source_label", source["label"]),
                                             "proposed_character": decision.get("character"),
                                             "issue": decision.get("issue"), "applied_upstream": False}})
        return output


def router(reviews: CorpusReviews) -> APIRouter:
    api = APIRouter()

    @api.get("/atlas/corpus/character")
    def character(id: str = Query(min_length=1, max_length=1000)) -> dict:
        return reviews.detail(id)

    @api.get("/atlas/corpus/reviews")
    def records(state: Literal["all", "flagged", "checked"] = "all") -> dict:
        return {"items": reviews.reviewed_rows(None if state == "all" else state)}

    @api.post("/atlas/corpus/reviews")
    def save(edit: CorpusEdit) -> dict:
        return reviews.record(edit)

    return api
