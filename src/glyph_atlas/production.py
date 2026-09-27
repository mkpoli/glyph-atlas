"""Evidence-backed production metadata shared by galleries and model datasets.

A production value is a node of the tree in `data/vocab/production.yaml`, written as its path
(`printed/type/metal/copper`). A scope selects documents by that tree: `all`, a node and everything
under it, or `not:` and a node for everything outside it.
"""
import json
from functools import lru_cache
from pathlib import Path

import yaml

from .vocab_tree import Tree

VOCAB = Path(__file__).resolve().parents[2] / "data/vocab/production.yaml"
OVERRIDES = Path(__file__).resolve().parents[2] / "data/vocab/production-overrides.yaml"
#: What a Quick review round deals by default: movable type fills whole books with near-identical glyphs.
REVIEW_SCOPE = "not:printed/type"
TREE = Tree(VOCAB)
check, label, within, in_scope, check_scope = TREE.check, TREE.label, TREE.within, TREE.in_scope, TREE.check_scope


def vocabulary() -> dict[str, dict]:
    return TREE.nodes


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
