"""Forms a person assigned to clustered glyphs, and the form each glyph resolves to.

Decisions are appended to `data/forms/decisions.jsonl`, one JSON object per line, and never
rewritten. A decision names the glyphs it covers explicitly, so it keeps its meaning when the
glyphs are clustered again:

- `{"kind": "cluster", "cluster": ..., "units": [...], "form": "𛂥"}` names the form of a cluster's
  glyphs. `"form": null` withdraws the cluster's form. Instead of a form, `"issue": "mixed"` says the
  cluster holds more than one form or character and names nothing for its glyphs, and `"issue":
  "character"` or `"crop"` reports its glyphs as a glyph decision reports single ones.
- `{"kind": "glyph", "units": [...], "form": "𛂞"}` sets the form of single glyphs, whatever their
  cluster says. `"form": null` marks them as not having the cluster's form, which leaves them
  unassigned. With `"issue": "character"` the glyphs are not this family's character at all, and
  `"character"` may say what they are; with `"issue": "crop"` the crop does not show one glyph.
- `{"kind": "inherit", "units": [...]}` removes those glyphs' own decisions, so that they follow
  their cluster again.

A glyph's form or report is its latest glyph decision when it has one, otherwise the latest decision
of a cluster that listed it. A form is always a member of the glyph's family.
"""
from __future__ import annotations

import fcntl
import json
import os
import threading
import time
import unicodedata
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import refs

ROOT = Path(__file__).resolve().parents[2]


def decisions_path() -> Path:
    return Path(os.environ.get("ATLAS_FORM_DECISIONS", ROOT / "data/forms/decisions.jsonl"))


def clusters_dir() -> Path:
    return Path(os.environ.get("ATLAS_FORM_CLUSTERS", ROOT / "work/forms/current"))


def _stamp(path: Path) -> tuple[int, int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return stat.st_mtime_ns, stat.st_ino, stat.st_size


class _Watched:
    """A value derived from files, rebuilt when they change and checked at most once a second.

    Identity is resolved for every corpus row, so the files are not stat'ed per call. A write in
    this process calls `invalidate`, which makes the next read check at once.
    """

    def __init__(self, paths, build):
        self.paths, self.build = paths, build
        self.lock = threading.Lock()
        self.key: object = object()
        self.value: Any = None
        self.checked = -1.0
        self.configured: tuple = ()

    def get(self):
        paths = self.paths()
        if paths == self.configured and time.monotonic() - self.checked < 1.0:
            return self.value
        with self.lock:
            key = tuple((str(p.resolve()), _stamp(p)) for p in paths)
            if key != self.key:
                self.value, self.key = self.build(paths), key
            self.configured, self.checked = paths, time.monotonic()
            return self.value

    def invalidate(self):
        self.checked = -1.0


def _load_clusters(paths) -> dict[str, Any]:
    import pyarrow.parquet as pq

    summary, table, neighbours = paths
    if _stamp(summary) is None or _stamp(table) is None:
        return {"revision": None, "families": {}, "units": {}, "members": {}, "labels": {}, "neighbours": {},
                "rows": {}}
    data = json.loads(summary.read_text())
    columns = pq.read_table(table, columns=["id", "family", "cluster", "similarity", "rank"]).to_pydict()
    units = {}
    members: dict[str, list[str]] = {}
    # `form_clusters.run` writes each cluster as one run of rows, row-aligned with embeddings.npy.
    rows: dict[str, tuple[int, int] | None] = {}
    for index, (identity, family, cluster, similarity, rank) in enumerate(zip(*columns.values(), strict=True)):
        units[identity] = (family, cluster, similarity, rank)
        members.setdefault(cluster, []).append(identity)
        span = rows.get(cluster, (index, index))
        rows[cluster] = (span[0], index + 1) if span is not None and span[1] == index else None
    labels = {c["id"]: c["label"] for family in data["families"].values() for c in family["clusters"]}
    # Written beside a clustering by `form_clusters.write_neighbours`; without it clusters keep size order.
    near = json.loads(neighbours.read_text()) if _stamp(neighbours) else {}
    return {"revision": data["revision"], "families": data["families"], "units": units, "members": members,
            "labels": labels, "neighbours": near, "rows": rows}


_CLUSTERS = _Watched(lambda: (clusters_dir() / "clusters.json", clusters_dir() / "units.parquet",
                              clusters_dir() / "neighbours.json"), _load_clusters)


def clusters() -> dict[str, Any]:
    """The current clustering: `families`, each glyph's `units` row, each cluster's `members`."""
    return _CLUSTERS.get()


class DecisionLogError(ValueError):
    pass


def _read_events(path: Path) -> list[dict]:
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return []
    lines = data.split(b"\n")
    # A line is complete once its newline is written; an unterminated tail is a write in progress
    # or one that was cut off, and it is not a decision.
    events = []
    for number, line in enumerate(lines[:-1], 1):
        if line.strip():
            try:
                events.append(json.loads(line))
            except ValueError:
                raise DecisionLogError(f"{path}:{number} is not a decision; the log needs repair") from None
    return events


def _load_decisions(paths) -> tuple[list[dict], dict[str, dict]]:
    events = _read_events(paths[0])
    by_cluster: dict[str, dict] = {}
    by_glyph: dict[str, dict] = {}
    for event in events:
        if event["kind"] == "cluster":
            for identity in event["units"]:
                by_cluster[identity] = event
        elif event["kind"] == "glyph":
            for identity in event["units"]:
                by_glyph[identity] = event
        elif event["kind"] == "inherit":
            for identity in event["units"]:
                by_glyph.pop(identity, None)
    result = {}
    for identity, event in by_cluster.items():
        issue = event.get("issue")
        if event["form"] is not None or issue in ISSUES:
            result[identity] = {"form": event["form"], "basis": "form_cluster", "decision": event["id"],
                                "cluster": event["cluster"], "at": event["at"],
                                **({"issue": issue, "character": event.get("character")} if issue in ISSUES else {})}
    for identity, event in by_glyph.items():
        result[identity] = {"form": event["form"], "basis": "form_glyph", "decision": event["id"],
                            "cluster": None, "at": event["at"], "issue": event.get("issue"),
                            "character": event.get("character")}
    return events, result


_DECISIONS = _Watched(lambda: (decisions_path(),), _load_decisions)


def _events() -> list[dict]:
    return _DECISIONS.get()[0]


def resolved() -> dict[str, dict]:
    """Every decided glyph's form: `{"form", "basis", "decision", "cluster", "at"}`, by glyph id."""
    return _DECISIONS.get()[1]


def split(cluster: str, k: int) -> list[list[str]]:
    """A cluster's glyphs divided into `k` groups by shape, largest group first.

    Spherical k-means over the cluster's own embeddings, seeded by the cluster id, so the same
    split comes back on every request. The groups are a view for choosing glyphs; nothing is stored.
    """
    import hashlib

    import numpy as np

    data = clusters()
    span = data["rows"].get(cluster)
    if span is None:
        raise DecisionError("This cluster cannot be split.")
    members = data["members"][cluster]
    vectors = np.load(clusters_dir() / "embeddings.npy", mmap_mode="r")[span[0]:span[1]].astype(np.float32)
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    k = max(1, min(k, len(members)))
    generator = np.random.default_rng(int(hashlib.sha256(cluster.encode()).hexdigest()[:8], 16))
    centres = [vectors[generator.integers(len(vectors))]]
    closest = 1 - vectors @ centres[0]
    for _ in range(1, k):
        weights = np.clip(closest, 0, None) ** 2
        index = generator.choice(len(vectors), p=weights / weights.sum()) if weights.sum() > 0 else 0
        centres.append(vectors[index])
        closest = np.minimum(closest, 1 - vectors @ vectors[index])
    c = np.stack(centres)
    labels = None
    for _ in range(100):
        updated = (vectors @ c.T).argmax(1)
        if labels is not None and np.array_equal(updated, labels):
            break
        labels = updated
        for j in range(k):
            if (labels == j).any():
                mean = vectors[labels == j].mean(0)
                c[j] = mean / np.linalg.norm(mean)
    similarity = (vectors * c[labels]).sum(1)
    groups = []
    for j in range(k):
        local = np.where(labels == j)[0]
        if len(local):
            groups.append([members[i] for i in local[np.argsort(-similarity[local])]])
    return sorted(groups, key=len, reverse=True)


def cluster_decisions() -> dict[str, dict]:
    """What each cluster of the current clustering was last given, `{"form", "issue"}` by cluster id."""
    revision = clusters()["revision"]
    named: dict[str, dict] = {}
    for event in _events():
        if event["kind"] == "cluster" and event["revision"] == revision:
            named[event["cluster"]] = {"form": event["form"], "issue": event.get("issue")}
    return named


def form_for(identity: str) -> dict | None:
    """The decided form of one glyph, or None when no decision covers it.

    A glyph a person marked as not having its cluster's form resolves to `{"form": None, ...}`.
    """
    return resolved().get(identity)


def cluster_of(identity: str) -> dict | None:
    """The shape cluster a glyph belongs to in the current clustering, or None."""
    data = clusters()
    row = data["units"].get(identity)
    if row is None:
        return None
    family, cluster, similarity, rank = row
    return {"id": cluster, "label": data["labels"].get(cluster), "family": family, "similarity": similarity, "rank": rank,
            "revision": data["revision"]}


def family_members(family: str) -> list[str]:
    info = refs.grapheme_info(family) or {}
    return [member["char"] for member in info.get("members") or []]


class DecisionError(ValueError):
    pass


ISSUES = ("character", "crop")
CLUSTER_ISSUES = ("mixed", *ISSUES)


def record(kind: str, *, form: str | None = None, cluster: str | None = None,
           units: list[str] | None = None, note: str = "", issue: str | None = None,
           character: str | None = None) -> dict:
    """Validate and append one decision; returns it as written."""
    data = clusters()
    if issue is not None and (kind == "inherit" or form is not None
                              or issue not in (CLUSTER_ISSUES if kind == "cluster" else ISSUES)):
        raise DecisionError("Only glyphs or clusters without a form can be reported, as a wrong character or a bad crop; "
                            "only a cluster can be mixed.")
    if character is not None:
        character = unicodedata.normalize("NFC", character.strip()) or None
    if character is not None and issue != "character":
        raise DecisionError("Only a wrong character names what the glyph is.")
    if kind == "cluster":
        if cluster not in data["members"]:
            raise DecisionError("Unknown cluster.")
        units = list(data["members"][cluster])
    elif kind in ("glyph", "inherit"):
        if not units or len(set(units)) != len(units) or len(units) > 5000:
            raise DecisionError("Choose between 1 and 5,000 distinct glyphs.")
        unknown = [identity for identity in units if identity not in data["units"]]
        if unknown:
            raise DecisionError(f"{len(unknown)} glyphs are not in the current clustering.")
        if kind == "inherit" and form is not None:
            raise DecisionError("Following the cluster takes no form.")
    else:
        raise DecisionError("Unknown decision kind.")
    families = {data["units"][identity][0] for identity in units}
    if len(families) != 1:
        raise DecisionError("A decision covers glyphs of one family.")
    family = families.pop()
    if form is not None and form not in family_members(family):
        raise DecisionError(f"{form} is not a form of this family.")
    event = {"id": str(uuid.uuid4()), "at": datetime.now(UTC).isoformat(timespec="seconds"),
             "kind": kind, "family": family, "form": form, "cluster": cluster,
             "revision": data["revision"], "units": units, "note": note,
             **({"issue": issue} if issue else {}), **({"character": character} if character else {})}
    path = decisions_path()
    line = (json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    # One locked append per decision, so two processes cannot interleave their lines.
    descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        written = 0
        while written < len(line):
            written += os.write(descriptor, line[written:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    _DECISIONS.invalidate()
    return event
