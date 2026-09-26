"""Admission to shape clustering, using source quality and saved human decisions.

Exclusion leaves the source and review journals intact. A mixed cluster still needs
form assignment; a report of a wrong character or damaged crop excludes its glyphs.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from itertools import pairwise
from pathlib import Path

import pyarrow.compute as pc
import pyarrow.dataset as ds

from . import forms, production, refs
from .feedback import REPORT_ACTOR_KINDS

POLICY = "reviewed-form-inputs-v1"
HUMAN = {"reviewed", "double-reviewed", "adjudicated"}


def mapping(value) -> dict:
    return json.loads(value) if isinstance(value, str) else value or {}


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def backwards_lines(rows: list[dict], horizontal: set[str]) -> set[str]:
    """Lines whose token sequence moves backwards within the same physical column.

    A move to a separate column is allowed. Equal/overlapping boxes do not establish
    a reversal. Only detector-aligned units are evidence for this diagnostic.
    """
    lines = defaultdict(list)
    for row in rows:
        if row.get("method") == "detect-align" and row.get("box") and row.get("line_id"):
            lines[row["line_id"]].append(row)
    bad = set()
    for identity, units in lines.items():
        vertical = identity not in horizontal
        along, size, across, breadth = ("y", "h", "x", "w") if vertical else ("x", "w", "y", "h")
        units.sort(key=lambda row: row.get("seq", 0))
        for first, second in pairwise(units):
            a, b = first["box"], second["box"]
            overlap = min(a[across] + a[breadth], b[across] + b[breadth]) - max(a[across], b[across])
            if overlap >= min(a[breadth], b[breadth]) / 2 and a[along] >= b[along] + b[size]:
                bad.add(identity)
                break
    return bad


class Admission:
    def __init__(self, root: Path, reviews: Path | None = None):
        self.decisions = forms.resolved()
        self.reviews = {}
        if reviews is not None:
            payload = json.loads(reviews.read_text())
            if payload.get("kind") != "atlas-character-reviews":
                raise ValueError("Expected an atlas-character-reviews export")
            for item in payload["reviews"]:
                event = item["event"]
                human = (event.get("actor_kind") not in REPORT_ACTOR_KINDS
                         and (event.get("actor_kind") == "human" or event.get("role") == "reviewer"))
                if item.get("current") and human:
                    self.reviews[event["target_id"]] = item
        self.inputs = {"policy": POLICY, "decisions": digest(self.decisions), "reviews": digest(self.reviews),
                       "production_scope": production.REVIEW_SCOPE,
                       "production_overrides": hashlib.sha256(production.OVERRIDES.read_bytes()).hexdigest()}
        self.counts = defaultdict(Counter)
        self.excluded = {}
        self.documents = {}
        self.bad_lines = set()

    def corpus(self, corpus) -> None:
        documents = corpus.parquet_files("documents")
        self.documents = ({row["id"]: row for row in ds.dataset(documents, format="parquet").to_table().to_pylist()}
                          if documents else {})
        table = ds.dataset(corpus.parquet_files("units"), format="parquet")
        self.bad_lines = set()
        if "method" not in table.schema.names:
            return
        rows = table.to_table(columns=["id", "line_id", "seq", "box", "method"],
                              filter=pc.field("active") & (pc.field("method") == "detect-align")).to_pylist()
        horizontal = set()
        lines = corpus.parquet_files("lines")
        if lines:
            line_table = ds.dataset(lines, format="parquet")
            if "vertical" in line_table.schema.names:
                horizontal = set(line_table.to_table(columns=["id"], filter=~pc.field("vertical"))["id"].to_pylist())
        self.bad_lines = backwards_lines(rows, horizontal)

    def reason(self, row: dict) -> str | None:
        decision = self.decisions.get(row["id"], {})
        if decision.get("issue") in {"character", "crop"}:
            return "reported-" + decision["issue"]
        # Review snapshots are usable only for the same crop and source label.
        review = self.reviews.get(row["id"])
        confirmed = False
        if review:
            snapshot = review["reviewed"]
            snapshot = snapshot.get("character", snapshot)
            label = snapshot.get("source_label") or snapshot.get("label")
            if snapshot.get("box") == row.get("box") and label == refs.to_char(row["unicode"]):
                evidence = mapping(review["event"].get("evidence"))
                decision_value = review["event"].get("new")
                if isinstance(decision_value, dict):
                    evidence = {**evidence, **decision_value}
                request = mapping(evidence.get("request"))
                reading_only = evidence.get("issue") == "reading" and (
                    evidence.get("layer") or request.get("reading")
                    or (snapshot.get("reading") and snapshot["reading"] != label))
                if evidence.get("verdict") == "wrong" and not reading_only:
                    return "review-" + (evidence.get("issue") or "wrong")
                if evidence.get("verdict") == "uncertain":
                    return "review-uncertain"
                confirmed = (evidence.get("verdict") == "match" and evidence.get("kind") != "visual-quiz"
                             and evidence.get("issue") not in {"character", "crop", "merged", "blank"}
                             and (evidence.get("character") or request.get("character")
                                  or evidence.get("suggested_character")) in {None, label, row["unicode"]})
        document = self.documents.get(row.get("document_id"), {})
        kind = production.production_info(document)["production"]
        if not production.in_scope(kind, production.REVIEW_SCOPE):
            return "movable-type"
        if row.get("review") in HUMAN or confirmed or decision.get("form"):
            return None
        meta = mapping(row.get("meta"))
        repair = meta.get("alignment_repair") or {}
        if repair.get("withheld") or repair.get("quiz") is False:
            return "alignment-withheld"
        if row.get("line_id") in self.bad_lines:
            return "line-order"
        if row.get("review") in {"rejected", "disputed"}:
            return "alignment-" + row["review"]
        if row.get("granularity", "char") != "char" or row.get("group_id"):
            return "multiple-glyphs"
        return None

    def assess(self, row: dict, corpus: str) -> str | None:
        reason = self.reason(row)
        document = row.get("document_id") or corpus
        self.counts[(corpus, document)][reason or "eligible"] += 1
        if reason:
            self.excluded[row["id"]] = reason
        return reason

    def report(self) -> dict:
        totals = Counter()
        documents = []
        for (corpus, document), counts in sorted(self.counts.items()):
            totals.update(counts)
            documents.append({"corpus": corpus, "document": document, "counts": dict(counts)})
        return {"inputs": self.inputs, "counts": dict(totals), "documents": documents}


def audit(root: Path, *, reviews: Path | None = None, wanted: set[str] | None = None) -> dict:
    """Count every admission decision without loading images or a model."""
    from .form_clusters import glyphs

    admission = Admission(root, reviews)
    for _ in glyphs(root, wanted, admission=admission):
        pass
    return admission.report()
