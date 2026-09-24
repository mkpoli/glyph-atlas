"""Evidence-backed production metadata shared by galleries and model datasets."""
import json
from functools import lru_cache
from pathlib import Path

import yaml

LABELS = {
    "manuscript": "Handwritten", "woodblock": "Woodblock", "movable-type": "Movable type",
    "mixed": "Mixed", "unknown": "Not classified",
}
OVERRIDES = Path(__file__).resolve().parents[2] / "data/vocab/production-overrides.yaml"


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
    kind = str((override or {}).get("production") or row.get("production") or "unknown")
    if kind not in LABELS:
        kind = "unknown"
    evidence = (override or {}).get("evidence", [])
    if not evidence and kind != "unknown":
        evidence = [{"source": "source_metadata", "document_id": row.get("id")}]
    return {"production": kind, "production_label": LABELS[kind], "production_evidence": evidence}
