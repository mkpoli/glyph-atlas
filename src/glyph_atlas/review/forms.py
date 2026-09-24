"""The local form-assignment API: families, their shape clusters, and the decisions on them."""
from __future__ import annotations

import threading
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from .. import forms, images, refs


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["cluster", "glyph", "inherit"]
    cluster: str | None = Field(default=None, max_length=200)
    units: list[str] | None = Field(default=None, max_length=5000)
    form: str | None = Field(default=None, max_length=8)
    note: str = Field(default="", max_length=2000)


_BOX_LOCK = threading.Lock()


@lru_cache(maxsize=1)
def _boxes(codh: str, stamp: tuple, clustering: str) -> tuple[list[str], dict[str, tuple[int, int, int, int, int]]]:
    """Page scan and box of every clustered glyph: the pages once, and five integers per glyph."""
    import pyarrow.parquet as pq

    wanted = forms.clusters()["units"]
    pages = pq.read_table(Path(codh) / "pages.parquet", columns=["id", "image"]).to_pydict()
    page_index = {page: i for i, page in enumerate(pages["id"])}
    table = pq.read_table(Path(codh) / "units.parquet", columns=["id", "page_id", "box"]).to_pydict()
    boxes = {identity: (page_index[page], box["x"], box["y"], box["w"], box["h"])
             for identity, page, box in zip(table["id"], table["page_id"], table["box"], strict=True)
             if box and identity in wanted and page in page_index}
    return pages["image"], boxes


def _form_entry(char: str) -> dict[str, Any]:
    code_point = refs.to_code_points(char)[0]
    row = refs.character(code_point)
    entry: dict[str, Any] = {"char": char, "code_point": code_point}
    if row is not None:
        entry.update({key: value for key, value in (("jibo", row.jibo[0] if row.jibo else None),
                                                    ("name", row.name), ("script", row.script)) if value})
    return entry


def router(media, corpus_root: Path) -> APIRouter:
    api = APIRouter()
    codh = corpus_root / "codh-full"

    def image(identity: str) -> str | None:
        stamp = forms._stamp(codh / "units.parquet")
        if stamp is None:
            return None
        with _BOX_LOCK:
            pages, boxes = _boxes(str(codh), stamp, forms.clusters()["revision"] or "")
        found = boxes.get(identity)
        path = images.held(pages[found[0]]) if found and pages[found[0]] else None
        # The same edge the published crops use, so a crop rendered for publication is reused.
        return media.local(path, list(found[1:]), edge=480) if path else None

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
    def family(code_point: str) -> dict[str, Any]:
        data = forms.clusters()
        found = data["families"].get(code_point)
        if found is None:
            raise HTTPException(404, "This family was not clustered.")
        decided = forms.resolved()
        named = forms.cluster_decisions()
        clusters = []
        for cluster in found["clusters"]:
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
                             "representatives": [{"id": identity, "image": image(identity)}
                                                 for identity in cluster["representatives"][:12]]})
        return {"revision": data["revision"], **summary(found, decided),
                "forms": [_form_entry(char) for char in forms.family_members(code_point)], "items": clusters}

    @api.get("/forms/clusters/{cluster_id:path}")
    def cluster(cluster_id: str, offset: Annotated[int, Query(ge=0)] = 0,
                limit: Annotated[int, Query(ge=1, le=500)] = 120) -> dict[str, Any]:
        data = forms.clusters()
        members = data["members"].get(cluster_id)
        if members is None:
            raise HTTPException(404, "Unknown cluster.")
        decided = forms.resolved()
        items = []
        for identity in members[offset:offset + limit]:
            _family, _cluster, similarity, rank = data["units"][identity]
            decision = decided.get(identity) or {}
            items.append({"id": identity, "image": image(identity), "rank": rank, "similarity": similarity,
                          "form": decision.get("form"), "basis": decision.get("basis")})
        return {"id": cluster_id, "total": len(members), "offset": offset, "form": forms.cluster_decisions().get(cluster_id),
                "items": items}

    @api.post("/forms/decisions")
    def decide(decision: Decision) -> dict[str, Any]:
        try:
            event = forms.record(decision.kind, form=decision.form, cluster=decision.cluster,
                                 units=decision.units, note=decision.note)
        except forms.DecisionError as error:
            raise HTTPException(422, str(error)) from None
        return {**{k: v for k, v in event.items() if k != "units"}, "count": len(event["units"])}

    return api
