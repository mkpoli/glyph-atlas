"""Evidence-backed production metadata shared by galleries and model datasets.

A production value is a node of the tree in `data/vocab/production.yaml`, written as its path
(`printed/type/metal/copper`). A scope selects documents by that tree: `all`, a node and everything
under it, or `not:` and a node for everything outside it.
"""
import json
from functools import lru_cache
from pathlib import Path

import yaml

VOCAB = Path(__file__).resolve().parents[2] / "data/vocab/production.yaml"
OVERRIDES = Path(__file__).resolve().parents[2] / "data/vocab/production-overrides.yaml"
#: What a Quick review round deals by default: movable type fills whole books with near-identical glyphs.
REVIEW_SCOPE = "not:printed/type"


@lru_cache(maxsize=1)
def vocabulary() -> dict[str, dict]:
    """Every node by id, in the file's order; a node's parent is the id less its last segment."""
    nodes = {row["id"]: row for row in yaml.safe_load(VOCAB.read_text(encoding="utf-8"))}
    for node in nodes:
        parent = node.rpartition("/")[0]
        if parent and parent not in nodes:
            raise ValueError(f"{VOCAB}: {node} has no parent {parent}")
    return nodes


def check(value: str) -> str:
    """`value` if the vocabulary has it."""
    if value not in vocabulary():
        raise ValueError(f"production {value!r} is not in {VOCAB.name}")
    return value


def label(value: str) -> str:
    return vocabulary()[value]["en"]


def within(value: str, node: str) -> bool:
    """Whether `value` is `node` or lies under it."""
    return value == node or value.startswith(node + "/")


def in_scope(value: str, scope: str) -> bool:
    if scope == "all":
        return True
    if scope.startswith("not:"):
        return not within(value, scope[4:])
    return within(value, scope)


def check_scope(scope: str) -> str:
    if scope != "all":
        check(scope.removeprefix("not:"))
    return scope


@lru_cache(maxsize=4)
def _overrides(path: str, stamp: int):
    data = yaml.safe_load(Path(path).read_text()) or {}
    return data.get("documents", {})


def production_info(document) -> dict:
    row = document.model_dump(mode="json") if hasattr(document, "model_dump") else document or {}
    overrides = _overrides(str(OVERRIDES), OVERRIDES.stat().st_mtime_ns) if OVERRIDES.exists() else {}
    refs = row.get("source_refs") or {}
    if isinstance(refs, str):
        try:
            refs = json.loads(refs)
        except ValueError:
            refs = {}
    if not isinstance(refs, dict):
        refs = {}
    keys = [row.get("id"), refs.get("honkoku-data"), refs.get("iiif-manifest")]
    override = next((overrides[key] for key in keys if key in overrides), None)
    kind = check(str((override or {}).get("production") or row.get("production") or "unknown"))
    evidence = (override or {}).get("evidence", [])
    if not evidence and kind != "unknown":
        evidence = [{"source": "source_metadata", "document_id": row.get("id")}]
    return {"production": kind, "production_label": label(kind), "production_evidence": evidence}
