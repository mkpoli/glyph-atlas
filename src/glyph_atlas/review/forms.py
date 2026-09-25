"""The local form-assignment API: families, their shape clusters, and the decisions on them."""
from __future__ import annotations

import json
import threading
import uuid
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from .. import form_clusters, forms, refs


class Report(BaseModel):
    """Glyphs whose crop or transcription is wrong, sent to the review queue from the Forms view."""
    model_config = ConfigDict(extra="forbid")
    units: list[str] = Field(min_length=1, max_length=200)
    issue: Literal["crop", "character", "merged", "blank", "other"]
    character: str | None = Field(default=None, max_length=32)
    client_id: str = Field(min_length=1, max_length=128)


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["cluster", "glyph", "inherit"]
    cluster: str | None = Field(default=None, max_length=200)
    units: list[str] | None = Field(default=None, max_length=5000)
    form: str | None = Field(default=None, max_length=8)
    note: str = Field(default="", max_length=2000)
    # The hosted site records who decided; the local server has one person and ignores it.
    client_id: str | None = Field(default=None, max_length=128)


_BOX_LOCK = threading.Lock()


@lru_cache(maxsize=1)
def _glyphs(root: str, stamp: tuple, clustering: str) -> dict[str, tuple]:
    """Where each clustered glyph's pixels are named: `(corpus, page, box, crop)`."""
    return {g["id"]: (g["corpus"], g["page"], g["box"], g["crop"])
            for g in form_clusters.glyphs(Path(root), set(forms.clusters()["units"]))}


def glyph_sources(root: Path) -> dict[str, tuple]:
    with _BOX_LOCK:
        return _glyphs(str(root), form_clusters.units_stamp(root), forms.clusters()["revision"] or "")


#: Adjacent clusters less similar than this start a new run of shapes.
RUN_SIMILARITY = 0.8


def shape_runs(near: dict, clusters: list[dict]) -> list[str]:
    """Clusters in shape order, cut into runs of similar shapes, the runs largest first.

    The shape chain puts similar clusters next to each other but leaves the faint and odd ones at
    either end. Cutting it where neighbours differ and listing the heaviest runs first puts the
    main forms at the top; each run starts from its largest cluster's end.
    """
    count = {c["id"]: c["count"] for c in clusters}
    chain, adjacent = near["order"], near.get("adjacent") or []
    runs, run = [], [chain[0]] if chain else []
    for cluster, similarity in zip(chain[1:], adjacent, strict=False):
        if similarity < RUN_SIMILARITY:
            runs.append(run)
            run = []
        run.append(cluster)
    if run:
        runs.append(run)
    ordered = []
    for run in sorted(runs, key=lambda r: -sum(count.get(c, 0) for c in r)):
        largest = max(range(len(run)), key=lambda i: count.get(run[i], 0))
        ordered.extend(run if largest <= (len(run) - 1) / 2 else run[::-1])
    return ordered


def _form_entry(char: str) -> dict[str, Any]:
    code_point = refs.to_code_points(char)[0]
    row = refs.character(code_point)
    entry: dict[str, Any] = {"char": char, "code_point": code_point}
    if row is not None:
        entry.update({key: value for key, value in (("jibo", row.jibo[0] if row.jibo else None),
                                                    ("name", row.name), ("script", row.script)) if value})
    return entry


def router(media, corpus_root: Path, reviews=None) -> APIRouter:
    api = APIRouter()

    pixels = form_clusters.Pixels(corpus_root)

    def image(identity: str) -> str | None:
        source = glyph_sources(corpus_root).get(identity)
        # Files are looked up per request, so a scan harvested while the server runs is shown.
        found = pixels(dict(zip(("corpus", "page", "box", "crop"), source, strict=True))) if source else None
        if found is None:
            return None
        path, box = found
        try:
            # The same edge the published crops use, so a crop rendered for publication is reused.
            return media.local(path, list(box) if box else None, edge=480)
        except OSError:
            return None

    def summary(family: dict, decided: dict[str, dict]) -> dict[str, Any]:
        members = forms.clusters()["members"]
        count = sum(1 for cluster in family["clusters"] for identity in members[cluster["id"]]
                    if (decided.get(identity) or {}).get("form"))
        return {"code_point": family["family"], "char": family["char"], "label": family["label"],
                "count": family["count"], "clusters": len(family["clusters"]), "assigned": count}

    @api.get("/forms/families")
    def families() -> dict[str, Any]:
        data = forms.clusters()
        if data["revision"] is None:
            raise HTTPException(404, "No clustering yet: run `atlas forms cluster`.")
        decided = forms.resolved()
        items = sorted((summary(family, decided) for family in data["families"].values()),
                       key=lambda f: -f["count"])
        return {"revision": data["revision"], "items": items}

    @api.get("/forms/families/{code_point}")
    def family(code_point: str, order: Literal["shape", "size"] = "shape") -> dict[str, Any]:
        data = forms.clusters()
        found = data["families"].get(code_point)
        if found is None:
            raise HTTPException(404, "This family was not clustered.")
        decided = forms.resolved()
        named = forms.cluster_decisions()
        clusters = []
        near = data["neighbours"].get(code_point, {})
        ranked = found["clusters"]
        if order == "shape" and near.get("order"):
            position = {cluster_id: i for i, cluster_id in enumerate(shape_runs(near, found["clusters"]))}
            ranked = sorted(found["clusters"], key=lambda c: position.get(c["id"], len(position)))
        for cluster in ranked:
            members = data["members"][cluster["id"]]
            own = sum(1 for identity in members if (decided.get(identity) or {}).get("basis") == "form_glyph")
            # Decisions list glyphs, so glyphs keep their forms across a re-clustering; a cluster
            # shows how many of its glyphs already have one, and which form most of them have.
            assigned = Counter(decided[identity]["form"] for identity in members
                               if (decided.get(identity) or {}).get("form"))
            clusters.append({**{k: cluster[k] for k in ("id", "label", "count", "coherence")},
                             "form": named.get(cluster["id"]), "exceptions": own,
                             "assigned": sum(assigned.values()),
                             "majority": assigned.most_common(1)[0][0] if assigned else None,
                             "nearest": ({**near["nearest"][cluster["id"]],
                                          "label": data["labels"].get(near["nearest"][cluster["id"]]["id"])}
                                         if cluster["id"] in near.get("nearest", {}) else None),
                             "representatives": [{"id": identity, "image": image(identity)}
                                                 for identity in cluster["representatives"][:12]]})
        return {"revision": data["revision"], **summary(found, decided), "order": order,
                "forms": [_form_entry(char) for char in forms.family_members(code_point)], "items": clusters}

    @api.get("/forms/split/{cluster_id:path}")
    def split(cluster_id: str, k: Annotated[int, Query(ge=2, le=8)] = 4,
              shown: Annotated[int, Query(ge=1, le=240)] = 60) -> dict[str, Any]:
        """A cluster divided by shape into `k` groups; each lists every id and shows its first crops."""
        if cluster_id not in forms.clusters()["members"]:
            raise HTTPException(404, "Unknown cluster.")
        try:
            groups = forms.split(cluster_id, k)
        except forms.DecisionError as error:
            raise HTTPException(422, str(error)) from None
        decided = forms.resolved()
        issues = reported()
        return {"id": cluster_id, "k": k, "groups": [
            {"count": len(ids), "ids": ids,
             "items": [{"id": identity, "image": image(identity), "form": (decided.get(identity) or {}).get("form"),
                        "basis": (decided.get(identity) or {}).get("basis"), "reported": issues.get(identity)}
                       for identity in ids[:shown]]}
            for ids in groups]}

    @api.get("/forms/clusters/{cluster_id:path}")
    def cluster(cluster_id: str, offset: Annotated[int, Query(ge=0)] = 0,
                limit: Annotated[int, Query(ge=1, le=500)] = 120,
                order: Literal["typical", "unusual"] = "typical") -> dict[str, Any]:
        data = forms.clusters()
        members = data["members"].get(cluster_id)
        if members is None:
            raise HTTPException(404, "Unknown cluster.")
        if order == "unusual":
            # The glyphs least like the cluster centre are where a different form hides.
            members = members[::-1]
        decided = forms.resolved()
        issues = reported()
        items = []
        for identity in members[offset:offset + limit]:
            _family, _cluster, similarity, rank = data["units"][identity]
            decision = decided.get(identity) or {}
            items.append({"id": identity, "image": image(identity), "rank": rank, "similarity": similarity,
                          "form": decision.get("form"), "basis": decision.get("basis"),
                          "reported": issues.get(identity)})
        return {"id": cluster_id, "total": len(members), "offset": offset, "order": order,
                "form": forms.cluster_decisions().get(cluster_id),
                "items": items}

    def reported() -> dict[str, str]:
        if reviews is None:
            return {}
        return {identity: json.loads(row["decision"]).get("issue") for identity, row in reviews.latest().items()
                if json.loads(row["decision"]).get("verdict") == "wrong"}

    @api.post("/forms/reports")
    def report(request: Report) -> dict[str, Any]:
        """Flag glyphs as wrong in the corpus review queue, and take them out of their cluster's form."""
        if reviews is None:
            raise HTTPException(404, "The corpus review queue is not available on this server.")
        from .corpus_reviews import CorpusEdit

        data = forms.clusters()
        unknown = [identity for identity in request.units if identity not in data["units"]]
        if unknown or len(set(request.units)) != len(request.units):
            raise HTTPException(422, "Report distinct glyphs of the current clustering.")
        # Every glyph is resolved before any is flagged, so a glyph that cannot be reviewed stops
        # the batch before it starts.
        current = {}
        for identity in request.units:
            detail = reviews.detail(identity)
            if not detail.get("image") or not detail.get("proxyable"):
                raise HTTPException(422, f"{identity} has no image that can be reviewed here.")
            current[identity] = detail
        flagged = []
        try:
            for identity, detail in current.items():
                reviews.record(CorpusEdit(id=uuid.uuid4(), identity=identity, client_id=request.client_id,
                                          revision=detail["revision"], source_revision=detail["source_revision"],
                                          verdict="wrong", issue=request.issue, character=request.character,
                                          note="reported from the Forms view"))
                flagged.append(identity)
        finally:
            # Whatever reached the review queue also leaves its cluster's form, even if a later glyph failed.
            if flagged:
                forms.record("glyph", units=flagged, form=None, note=f"reported: {request.issue}")
        return {"count": len(flagged), "issue": request.issue}

    @api.post("/forms/decisions")
    def decide(decision: Decision) -> dict[str, Any]:
        try:
            event = forms.record(decision.kind, form=decision.form, cluster=decision.cluster,
                                 units=decision.units, note=decision.note)
        except forms.DecisionError as error:
            raise HTTPException(422, str(error)) from None
        return {**{k: v for k, v in event.items() if k != "units"}, "count": len(event["units"])}

    return api
